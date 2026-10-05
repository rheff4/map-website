/*
 * Routes - map layers and sidebar controls.
 *
 * Owner: Rob - branch `routes`
 *
 * PRD feature: a running route generator with adjustable distance, start
 * point, loop or out-and-back, and elevation.
 *
 * Everything here talks to generateRoute() and nothing else. Today that
 * function does local geometry and returns a fake route; when the backend
 * exists it becomes a fetch() and nothing downstream changes. See the SEAM
 * comment below.
 *
 * Map contract: shared/README.md. All ids namespaced `routes:` / `routes-`.
 */
(function (global) {
  'use strict';

  var view = global.view;
  var sidebar = global.sidebar;

  // ---------------------------------------------------------------- state

  var state = {
    start: MapWebsite.DEFAULT_CENTER.slice(),  // Boston Common until clicked
    lengthM: 5000,
    shape: 'loop',                             // 'loop' | 'out-and-back'
    paceSecPerKm: 360,                         // 6:00 min/km
    seed: 1,
    route: null
  };

  var EMPTY = { type: 'FeatureCollection', features: [] };

  // ------------------------------------------------------------ geometry

  var EARTH_R = 6371008.8; // metres, mean radius

  function toRad(d) { return d * Math.PI / 180; }
  function toDeg(r) { return r * 180 / Math.PI; }

  /* Great-circle distance in metres between two [lon, lat] pairs. */
  function haversine(a, b) {
    var dLat = toRad(b[1] - a[1]);
    var dLon = toRad(b[0] - a[0]);
    var la1 = toRad(a[1]);
    var la2 = toRad(b[1]);
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * EARTH_R * Math.asin(Math.sqrt(h));
  }

  /* Total length of a coordinate list, in metres. */
  function lengthOf(coords) {
    var total = 0;
    for (var i = 1; i < coords.length; i++) total += haversine(coords[i - 1], coords[i]);
    return total;
  }

  /* The point distM metres from `from` along a compass bearing. */
  function destination(from, bearingDeg, distM) {
    var d = distM / EARTH_R;
    var brg = toRad(bearingDeg);
    var la1 = toRad(from[1]);
    var lo1 = toRad(from[0]);

    var la2 = Math.asin(Math.sin(la1) * Math.cos(d) +
                        Math.cos(la1) * Math.sin(d) * Math.cos(brg));
    var lo2 = lo1 + Math.atan2(Math.sin(brg) * Math.sin(d) * Math.cos(la1),
                               Math.cos(d) - Math.sin(la1) * Math.sin(la2));
    return [toDeg(lo2), toDeg(la2)];
  }

  function boundsOf(coords) {
    var w = coords[0][0], e = coords[0][0], s = coords[0][1], n = coords[0][1];
    coords.forEach(function (c) {
      if (c[0] < w) w = c[0];
      if (c[0] > e) e = c[0];
      if (c[1] < s) s = c[1];
      if (c[1] > n) n = c[1];
    });
    return [[w, s], [e, n]];
  }

  // ------------------------------------------------- the generator (SEAM)

  /*
   * A ring through the start point whose circumference is the target length.
   * Cuts across buildings and water - it is a shape of the right size, not a
   * runnable route. Real routing needs OpenRouteService.
   */
  function stubLoop(start, lengthM, seed) {
    var radius = lengthM / (2 * Math.PI);
    var bearing = (seed * 67) % 360;
    var centre = destination(start, bearing, radius);
    var fromCentre = bearing + 180;   // angle from the centre back to the start

    var coords = [];
    var steps = 72;
    for (var i = 0; i <= steps; i++) {
      coords.push(destination(centre, fromCentre + (360 * i / steps), radius));
    }
    return coords;
  }

  /* Straight out to half the distance, then back along the same line. */
  function stubOutAndBack(start, lengthM, seed) {
    var bearing = (seed * 67) % 360;
    var half = lengthM / 2;
    var steps = 24;

    var out = [];
    for (var i = 0; i <= steps; i++) {
      out.push(destination(start, bearing, half * i / steps));
    }
    return out.concat(out.slice(0, -1).reverse());
  }

  /*
   * SEAM. Everything else in this file depends only on this signature.
   *
   * Today: local geometry, resolved immediately.
   * Later: POST to a backend that holds ORS_API_KEY, e.g.
   *
   *     return fetch('/api/routes/generate', {
   *       method: 'POST',
   *       headers: { 'Content-Type': 'application/json' },
   *       body: JSON.stringify(opts)
   *     }).then(function (r) { return r.json(); });
   *
   * The key must stay server-side: this repo is public. ORS returns the same
   * GeoJSON shape, plus real ascent/descent and a third elevation value in
   * each coordinate.
   */
  function generateRoute(opts) {
    var coords = opts.shape === 'out-and-back'
      ? stubOutAndBack(opts.start, opts.lengthM, opts.seed)
      : stubLoop(opts.start, opts.lengthM, opts.seed);

    return Promise.resolve({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: coords },
      properties: {
        requestedM: opts.lengthM,
        shape: opts.shape,
        ascent: null,      // no elevation until the real API is wired up
        descent: null,
        stub: true
      }
    });
  }

  // ------------------------------------------------------------ formatting

  function km(metres) { return (metres / 1000).toFixed(2) + ' km'; }

  function clock(seconds) {
    var s = Math.round(seconds);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    var mm = h ? String(m).padStart(2, '0') : String(m);
    return (h ? h + ':' : '') + mm + ':' + String(sec).padStart(2, '0');
  }

  /* Accepts "6:00", "6", "5:30". Returns seconds per km, or null. */
  function parsePace(text) {
    var trimmed = String(text).trim();
    var parts = trimmed.split(':');
    if (parts.length === 2) {
      var m = parseInt(parts[0], 10);
      var s = parseInt(parts[1], 10);
      if (isNaN(m) || isNaN(s) || s >= 60) return null;
      return m * 60 + s;
    }
    var mins = parseFloat(trimmed);
    return isNaN(mins) ? null : Math.round(mins * 60);
  }

  function coord(c) { return c[1].toFixed(4) + ', ' + c[0].toFixed(4); }

  // ------------------------------------------------------------ map layers

  function startFeature() {
    return {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: { type: 'Point', coordinates: state.start },
        properties: { name: 'Start' }
      }]
    };
  }

  // Registered once; redrawn with setData. Re-adding would throw on the
  // duplicate id, which is the point of the contract.
  view.addLayer({
    id: 'routes:line',
    type: 'line',
    data: EMPTY,
    paint: {
      'line-color': '#2b6cb0',
      'line-width': 5,
      'line-opacity': 0.85
    },
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    legend: { label: 'Running route', color: '#2b6cb0' }
  });

  view.addLayer({
    id: 'routes:start',
    type: 'circle',
    data: startFeature(),
    paint: {
      'circle-radius': 7,
      'circle-color': '#111',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#fff'
    }
  });

  // -------------------------------------------------------------- sidebar

  var panel = sidebar.panel('routes');

  var style = document.createElement('style');
  style.textContent = [
    '.routes-field { margin: 0 0 16px; }',
    '.routes-field > label, .routes-legend {',
    '  display: block; font-weight: 600; margin-bottom: 6px; }',
    '.routes-field input[type=range] { width: 100%; }',
    '.routes-shape { display: flex; gap: 12px; border: 0; padding: 0; margin: 0; }',
    '.routes-shape label { font-weight: 400; display: flex; align-items: center; gap: 5px; }',
    '.routes-pace { width: 70px; }',
    '.routes-hint { color: #666; margin: 0 0 16px; }',
    '#routes-generate {',
    '  width: 100%; padding: 10px; font: inherit; font-weight: 600; color: #fff;',
    '  background: #2b6cb0; border: 0; border-radius: 8px; cursor: pointer; }',
    '#routes-generate:hover { background: #245c99; }',
    '.routes-results { margin-top: 16px; border-top: 1px solid #e2e2e2; padding-top: 12px; }',
    '.routes-results dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; }',
    '.routes-results dt { color: #666; }',
    '.routes-results dd { margin: 0; font-variant-numeric: tabular-nums; }',
    '.routes-note { color: #8a6d3b; background: #fcf4e4; padding: 8px 10px;',
    '  border-radius: 6px; margin: 12px 0 0; font-size: 13px; }'
  ].join('\n');
  document.head.appendChild(style);

  panel.innerHTML = [
    '<h2>Routes</h2>',
    '<p class="routes-hint">Click the map to move your start point.</p>',

    '<div class="routes-field">',
    '  <span class="routes-legend">Start</span>',
    '  <span id="routes-start">' + coord(state.start) + '</span>',
    '</div>',

    '<div class="routes-field">',
    '  <label for="routes-distance">Distance: ',
    '    <output id="routes-distance-out">5.0 km</output></label>',
    '  <input type="range" id="routes-distance" min="1" max="20" step="0.5" value="5">',
    '</div>',

    '<div class="routes-field">',
    '  <fieldset class="routes-shape">',
    '    <label><input type="radio" name="routes-shape" value="loop" checked> Loop</label>',
    '    <label><input type="radio" name="routes-shape" value="out-and-back"> Out and back</label>',
    '  </fieldset>',
    '</div>',

    '<div class="routes-field">',
    '  <label for="routes-pace">Pace (min/km)</label>',
    '  <input type="text" id="routes-pace" class="routes-pace" value="6:00">',
    '</div>',

    '<button id="routes-generate" type="button">Generate route</button>',

    '<div class="routes-results" id="routes-results"></div>'
  ].join('\n');

  var els = {
    start: document.getElementById('routes-start'),
    distance: document.getElementById('routes-distance'),
    distanceOut: document.getElementById('routes-distance-out'),
    pace: document.getElementById('routes-pace'),
    generate: document.getElementById('routes-generate'),
    results: document.getElementById('routes-results')
  };

  // ---------------------------------------------------------------- wiring

  function setStart(lonLat) {
    state.start = lonLat;
    els.start.textContent = coord(lonLat);
    view.setData('routes:start', startFeature());
  }

  els.distance.addEventListener('input', function () {
    state.lengthM = parseFloat(els.distance.value) * 1000;
    els.distanceOut.textContent = (state.lengthM / 1000).toFixed(1) + ' km';
  });

  panel.querySelectorAll('input[name="routes-shape"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      if (radio.checked) state.shape = radio.value;
    });
  });

  els.pace.addEventListener('change', function () {
    var secs = parsePace(els.pace.value);
    if (secs === null) {
      els.pace.value = clock(state.paceSecPerKm);
      return;
    }
    state.paceSecPerKm = secs;
    if (state.route) showResults(state.route);
  });

  els.generate.addEventListener('click', function () {
    els.generate.disabled = true;
    generateRoute({
      start: state.start,
      lengthM: state.lengthM,
      shape: state.shape,
      seed: state.seed++
    }).then(function (route) {
      state.route = route;
      view.setData('routes:line', route);
      view.fitBounds(boundsOf(route.geometry.coordinates), 60);
      showResults(route);
    }).catch(function (err) {
      els.results.innerHTML = '<p class="routes-note">Could not generate a route: ' +
        err.message + '</p>';
    }).then(function () {
      els.generate.disabled = false;
    });
  });

  function showResults(route) {
    var coords = route.geometry.coordinates;
    var actual = lengthOf(coords);
    var requested = route.properties.requestedM;
    var delta = 100 * (actual - requested) / requested;
    var seconds = (actual / 1000) * state.paceSecPerKm;
    var ascent = route.properties.ascent;

    els.results.innerHTML = [
      '<dl>',
      '  <dt>Requested</dt><dd>' + km(requested) + '</dd>',
      '  <dt>Actual</dt><dd>' + km(actual) +
        ' <span style="color:#666">(' + (delta >= 0 ? '+' : '') + delta.toFixed(1) + '%)</span></dd>',
      '  <dt>Est. time</dt><dd>' + clock(seconds) + '</dd>',
      '  <dt>Elevation</dt><dd>' +
        (ascent === null ? '&mdash;' : '+' + Math.round(ascent) + ' m') + '</dd>',
      '</dl>',
      route.properties.stub
        ? '<p class="routes-note">Straight-line preview. Routes will follow real ' +
          'streets, and elevation will appear, once the OpenRouteService backend ' +
          'is wired up.</p>'
        : ''
    ].join('\n');
  }

  // Clicking the map moves the start point. Note this fires for clicks on
  // restaurant pins too - their popup still opens.
  view.onMapClick(function (lonLat) {
    setStart(lonLat);
  });

  // Re-frame the route when the user comes back to this tab.
  sidebar.onShow('routes', function () {
    if (state.route) view.fitBounds(boundsOf(state.route.geometry.coordinates), 60);
  });
})(window);
