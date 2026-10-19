/*
 * Routes - map layers and sidebar controls.
 *
 * Owner: Rob - branch `routes`
 *
 * PRD features: a running route generator with adjustable distance, start
 * point, a loop or a one-way run to a point, and elevation (flattest /
 * hilliest). The point is picked on the map, or is a restaurant handed over
 * by the Restaurants tab's "Run here" button through
 * view.on('restaurants:run-to'). The server still offers out-and-backs
 * (shape=out-and-back); this page no longer asks for them.
 *
 * Everything here talks to generateRoute() and nothing else. It asks the
 * server (routes/api.py) for a real route on streets; if the server has no
 * OpenRouteService key it falls back to a straight-line preview. See the SEAM
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
    shape: 'loop',                             // 'loop' | 'to'
    hills: 'balanced',                         // 'flat' | 'balanced' | 'hilly'
    dest: null,                                // {name, lon, lat} to run to
    picking: false,                            // next map click sets dest
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
   * Fallback only. A ring through the start point whose circumference is the
   * target length. Cuts across buildings and water - a shape of the right
   * size, not a runnable route - so it's used only when the server can't route.
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

  /*
   * SEAM. Everything else in this file depends only on this signature:
   * opts -> Promise of a GeoJSON LineString Feature.
   *
   * Calls GET /api/routes/generate (routes/api.py), which asks
   * OpenRouteService for routes on streets and paths and measures their climb
   * from the USGS elevation grid. The ORS key stays on the server - this repo
   * is public. A 501 means the server has no key; the preview stands in so
   * the tab still works, and the results panel says why.
   */
  function generateRoute(opts) {
    var query = [
      'lon=' + opts.start[0],
      'lat=' + opts.start[1],
      'km=' + (opts.lengthM / 1000),
      'shape=' + encodeURIComponent(opts.shape),
      'hills=' + encodeURIComponent(opts.hills),
      'seed=' + opts.seed
    ].concat(opts.dest ? ['to_lon=' + opts.dest.lon, 'to_lat=' + opts.dest.lat] : []).join('&');

    return fetch('/api/routes/generate?' + query).then(function (r) {
      return r.json().then(function (body) {
        if (r.ok) return body;
        // 501: this server has no ORS key. Fall back to the preview so the
        // tab still works, and say why.
        if (r.status === 501) return stubRoute(opts, body.error);
        throw new Error(body.error || ('HTTP ' + r.status));
      });
    });
  }

  /* The straight-line preview, used only when the server can't route. */
  function stubRoute(opts, reason) {
    var coords = opts.shape === 'to' ? [opts.start, [opts.dest.lon, opts.dest.lat]]
      : stubLoop(opts.start, opts.lengthM, opts.seed);

    return {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: coords },
      properties: {
        requestedM: opts.lengthM,
        shape: opts.shape,
        ascent: null,
        descent: null,
        stub: true,
        stubReason: reason
      }
    };
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

  /* Time of day `seconds` from now, e.g. "7:42 pm". */
  function arrival(seconds) {
    return new Date(Date.now() + seconds * 1000)
      .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }

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
    legend: { label: 'Running route', color: '#2b6cb0' },
    // Required by HeiGIT's terms wherever openrouteservice results are shown,
    // in exactly this wording.
    attribution: '&copy; <a href="https://openrouteservice.org/" target="_blank" rel="noopener">' +
      'openrouteservice</a> by HeiGIT | Data from ' +
      '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
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

  function destFeature() {
    return state.dest ? {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [state.dest.lon, state.dest.lat] },
      properties: { name: state.dest.name }
    } : EMPTY;
  }

  view.addLayer({
    id: 'routes:dest',
    type: 'circle',
    data: EMPTY,
    paint: {
      'circle-radius': 8,
      'circle-color': '#c53030',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#fff'
    },
    popup: function (p) { return '<b>' + esc(p.name) + '</b><br>Run destination'; }
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
    '.routes-dest { background: #f0f5fb; border-radius: 8px; padding: 8px 10px; }',
    '.routes-dest button { font: inherit; font-size: 12px; margin-left: 6px; cursor: pointer; }',
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
    '  border-radius: 6px; margin: 12px 0 0; font-size: 13px; }',
    '.routes-warn { color: #9b2c2c; background: #fdecec; border-left: 3px solid #c53030; }'
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
    '    <label><input type="radio" name="routes-shape" value="to"> To a point</label>',
    '  </fieldset>',
    '</div>',

    '<div class="routes-field routes-dest" id="routes-dest" hidden>',
    '  <span class="routes-legend">Running to</span>',
    '  <span id="routes-dest-name"></span>',
    '  <button type="button" id="routes-dest-pick">Pick on map</button>',
    '  <p class="routes-hint" style="margin:6px 0 0">Or press <b>Run here</b> on a restaurant.</p>',
    '</div>',

    '<div class="routes-field">',
    '  <span class="routes-legend">Hills</span>',
    '  <fieldset class="routes-shape">',
    '    <label><input type="radio" name="routes-hills" value="flat"> Flattest</label>',
    '    <label><input type="radio" name="routes-hills" value="balanced" checked> Balanced</label>',
    '    <label><input type="radio" name="routes-hills" value="hilly"> Hilliest</label>',
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
    results: document.getElementById('routes-results'),
    dest: document.getElementById('routes-dest'),
    destName: document.getElementById('routes-dest-name'),
    destPick: document.getElementById('routes-dest-pick')
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

  function setShape(shape) {
    state.shape = shape;
    panel.querySelector('input[name="routes-shape"][value="' + shape + '"]').checked = true;
    els.dest.hidden = shape !== 'to';
    // Nowhere to run to yet: the next map click chooses it.
    setPicking(shape === 'to' && !state.dest);
  }

  panel.querySelectorAll('input[name="routes-shape"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      if (radio.checked) setShape(radio.value);
    });
  });

  panel.querySelectorAll('input[name="routes-hills"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      if (radio.checked) state.hills = radio.value;
    });
  });

  /* Where a "to a point" run ends: {name, lon, lat}. name is null for a
     point picked on the map, which is shown by its coordinates. */
  function setDest(dest) {
    state.dest = dest;
    els.destName.textContent = dest.name || coord([dest.lon, dest.lat]);
    view.setData('routes:dest', destFeature());
  }

  /* While picking, the next map click sets the destination, not the start. */
  function setPicking(on) {
    state.picking = on;
    els.destPick.textContent = on ? 'Click the map…' : 'Pick on map';
    els.destPick.disabled = on;
    if (!state.dest) els.destName.textContent = on ? '' : 'Nowhere yet';
    view.getMap().getCanvas().style.cursor = on ? 'crosshair' : '';
  }

  els.destPick.addEventListener('click', function () { setPicking(true); });

  // "Run here" on the Restaurants tab.
  view.on('restaurants:run-to', function (dest) {
    setDest(dest);
    setShape('to');
    sidebar.show('routes');
    view.fitBounds(boundsOf([state.start, [dest.lon, dest.lat]]), 80);
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
    if (state.shape === 'to' && !state.dest) {
      els.results.innerHTML = '<p class="routes-note">Pick a point on the map to run to first.</p>';
      setPicking(true);
      return;
    }
    els.generate.disabled = true;
    els.generate.textContent = 'Finding a route…';
    generateRoute({
      start: state.start,
      lengthM: state.lengthM,
      shape: state.shape,
      hills: state.hills,
      dest: state.shape === 'to' ? state.dest : null,
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
      els.generate.textContent = 'Generate route';
    });
  });

  // Beyond this many metres the page says plainly that the route isn't the
  // length asked for. The server sends its own (TOLERANCE_M in routes/api.py);
  // this is only for the straight-line preview.
  var LENGTH_TOLERANCE_M = 500;

  function showResults(route) {
    var p = route.properties;
    var actual = p.lengthM || lengthOf(route.geometry.coordinates);
    var requested = p.requestedM;
    var delta = Math.round(actual - requested);    // metres over (+) or under (-)
    var seconds = (actual / 1000) * state.paceSecPerKm;
    var climb = p.ascent === null || p.ascent === undefined
      ? '&mdash;'
      : '+' + Math.round(p.ascent) + ' m' +
        (p.elevation && !p.elevation.complete ? ' <span style="color:#666">(partial)</span>' : '');

    var notes = [];
    var warning = null;
    var c = p.choice;
    if (p.stub) {
      notes.push('Straight-line preview, not a real route. ' + (p.stubReason || ''));
    } else if (p.tooFar) {
      warning = '<b>' + (state.dest && state.dest.name ? esc(state.dest.name) : 'That point') +
        ' is too far for a ' + km(requested) + ' run.</b> The shortest route there is ' +
        km(p.directM) + ', ' + km(p.directM - requested) + ' more than you asked for. ' +
        'This is that route. Choose a closer point, or a longer distance.';
    } else if (Math.abs(actual - requested) > (p.toleranceM || LENGTH_TOLERANCE_M)) {
      notes.push('Couldn’t get within ' + Math.round(p.toleranceM || LENGTH_TOLERANCE_M) +
        ' m of ' + km(requested) + ' from here — ' +
        'few streets connect near this start point. Try generating again, or move the start.');
    }
    if (c && c.similar) {
      notes.push('Routes here are much the same for hills: ' + Math.round(c.climbMin) + '–' +
        Math.round(c.climbMax) + ' m of climb across the ' + c.compared + ' compared.');
    } else if (c && p.hills !== 'balanced' && c.compared > 1) {
      notes.push((p.hills === 'flat' ? 'Flattest' : 'Hilliest') + ' of ' + c.compared +
        ' routes near this distance (' + Math.round(c.climbMin) + '–' +
        Math.round(c.climbMax) + ' m of climb).');
    }

    els.results.innerHTML = [
      '<dl>',
      '  <dt>Requested</dt><dd>' + km(requested) + '</dd>',
      '  <dt>Actual</dt><dd>' + km(actual) +
        ' <span style="color:#666">(' + (delta >= 0 ? '+' : '−') + Math.abs(delta) + ' m)</span></dd>',
      '  <dt>Est. time</dt><dd>' + clock(seconds) + '</dd>',
      p.shape === 'to' ? '  <dt>Arrive</dt><dd>' + arrival(seconds) + ' if you leave now</dd>' : '',
      '  <dt>Climb</dt><dd>' + climb + '</dd>',
      '</dl>'
    ].concat(warning ? ['<p class="routes-note routes-warn" role="alert">' + warning + '</p>'] : [])
     .concat(notes.map(function (n) {
      return '<p class="routes-note">' + n + '</p>';
    })).join('\n');
  }

  // Clicking the map moves the start point - or, while picking, sets where to
  // run to. Only while this tab is open, and never on a restaurant pin (that
  // selects the restaurant instead; its "Run here" sets the destination).
  var PIN_LAYERS = ['restaurants:recognised', 'restaurants:other'];
  view.onMapClick(function (lonLat, e) {
    if (sidebar.active() !== 'routes') return;
    var map = view.getMap();
    var pins = PIN_LAYERS.filter(function (id) { return map.getLayer(id); });
    if (pins.length && map.queryRenderedFeatures(e.point, { layers: pins }).length) return;
    if (state.picking) {
      setDest({ name: null, lon: lonLat[0], lat: lonLat[1] });
      setPicking(false);
      return;
    }
    setStart(lonLat);
  });

  // Re-frame the route when the user comes back to this tab.
  sidebar.onShow('routes', function () {
    if (state.route) view.fitBounds(boundsOf(state.route.geometry.coordinates), 60);
  });

  // -------------------------------------------------------------- toilets

  // Public toilets, each labelled with its name or, when it has no real one,
  // its nearest intersection ("Street A / Street B") - built by
  // routes/tools/build_toilets.py. Labels appear from zoom 13 so the city-wide
  // view isn't buried in text; MapLibre hides any that would overlap.

  var TOILET_GREEN = '#2f855a';
  var LABEL_FONT = ['Montserrat Medium', 'Open Sans Bold', 'Noto Sans Regular'];  // the basemap's own

  function esc(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function toiletPopup(p) {
    var lines = ['<b>' + esc(p.label) + '</b>'];
    if (p.label_source === 'intersection') {
      lines.push('Nearest intersection, about ' + p.label_distance_m + ' m away');
    } else if (p.label_source === 'place') {
      lines.push('No street corner nearby, so labelled by where it is');
    }
    if (p.name && p.name !== p.label) lines.push('Signed as “' + esc(p.name) + '”');
    if (p.access === 'customers') lines.push('Customers only');
    if (p.fee === 'yes') lines.push('Fee charged');
    else if (p.fee === 'no') lines.push('Free');
    if (p.wheelchair === 'yes') lines.push('Wheelchair accessible');
    if (p.opening_hours) lines.push('Hours: ' + esc(p.opening_hours));
    return lines.join('<br>');
  }

  fetch('/api/routes/toilets').then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function (toilets) {
    view.addLayer({
      id: 'routes:toilets',
      type: 'circle',
      data: toilets,
      paint: {
        'circle-radius': 6,
        'circle-color': TOILET_GREEN,
        'circle-stroke-width': 1.5,
        'circle-stroke-color': '#fff'
      },
      legend: { label: 'Public toilets', color: TOILET_GREEN },
      popup: toiletPopup
    });
    view.addLayer({
      id: 'routes:toilet-labels',
      type: 'symbol',
      data: toilets,
      layout: {
        'text-field': ['get', 'label'],
        'text-font': LABEL_FONT,
        'text-size': 11,
        'text-anchor': 'left',
        'text-offset': [0.9, 0],
        'text-max-width': 11,
        'text-optional': true
      },
      paint: {
        'text-color': '#22543d',
        'text-halo-color': '#fff',
        'text-halo-width': 1.5,
        'text-opacity': ['step', ['zoom'], 0, 13, 1]
      }
    });
  }).catch(function (err) {
    if (global.console) console.warn('[routes] toilets unavailable:', err.message);
  });
})(window);
