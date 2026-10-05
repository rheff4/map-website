/*
 * Restaurants - the Restaurants tab and its map layers.
 *
 * Owner: Pedro - branch `restaurants`
 *
 * shared/index.html already loads this file. Everything here stays in the
 * `restaurants:` namespace (layer ids, element ids). The ranking itself is
 * done on the server (restaurants/scoring.py); this file asks
 * /api/restaurants/search for a ranked list around a point and then filters
 * it locally, so changing a filter never costs an API call.
 */
(function (global) {
  'use strict';

  var view = global.view;
  var sidebar = global.sidebar;
  var API = '/api/restaurants';

  var COLORS = {
    recognised: '#c53030',
    other: '#a0aec0',
    selected: '#1a202c',
    origin: '#2b6cb0'
  };
  var RADII = [500, 1000, 1500, 2000, 3000, 5000];
  var EMPTY = { type: 'FeatureCollection', features: [] };

  var state = {
    config: null,
    origin: null,          // [lon, lat]
    originLabel: '',
    radius: 1500,
    data: null,            // last /search response
    filters: { prices: [], cuisine: '', openNow: false, recognisedOnly: true },
    selectedId: null,
    picking: false,
    seq: 0,                // ignore responses from superseded searches
    started: false
  };

  // ---------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function safeUrl(url) {
    return /^https?:\/\//i.test(url || '') ? url : null;
  }

  function $(id) {
    return document.getElementById('restaurants-' + id);
  }

  function formatDistance(m) {
    return m < 1000 ? Math.round(m / 10) * 10 + ' m' : (m / 1000).toFixed(1) + ' km';
  }

  function priceText(price) {
    return price ? new Array(price + 1).join('$') : '';
  }

  function openText(open) {
    return open === true ? 'Open now' : open === false ? 'Closed now' : '';
  }

  function points(results) {
    return {
      type: 'FeatureCollection',
      features: results.map(function (r) {
        return {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [r.lon, r.lat] },
          properties: { id: r.id, name: r.name, score: r.score }
        };
      })
    };
  }

  function circle(center, radiusM) {
    var coords = [];
    var lat = center[1] * Math.PI / 180;
    for (var i = 0; i <= 64; i++) {
      var a = (i / 64) * 2 * Math.PI;
      coords.push([
        center[0] + (radiusM * Math.cos(a)) / (111320 * Math.cos(lat)),
        center[1] + (radiusM * Math.sin(a)) / 110540
      ]);
    }
    return { type: 'Feature', geometry: { type: 'Polygon', coordinates: [coords] }, properties: {} };
  }

  function inCity(lonLat) {
    var b = state.config.city.bounds;
    return lonLat[0] >= b[0][0] && lonLat[0] <= b[1][0] &&
           lonLat[1] >= b[0][1] && lonLat[1] <= b[1][1];
  }

  function getJSON(url) {
    return fetch(url).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (res.ok) return body;
        if (res.status === 404 && !body.error) {
          throw new Error('The restaurant API is not running. Start the site with ' +
                          '"python server/app.py".');
        }
        throw new Error(body.error || 'The server returned an error (' + res.status + ').');
      });
    }, function () {
      throw new Error('Could not reach the server. Is "python server/app.py" running?');
    });
  }

  // ---------------------------------------------------------------------
  // Panel
  // ---------------------------------------------------------------------

  (function loadCss() {
    var script = document.currentScript;
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = script ? new URL('restaurants.css', script.src).href : '../restaurants/restaurants.css';
    document.head.appendChild(link);
  })();

  var panel = sidebar.panel('restaurants');
  panel.innerHTML =
    '<h2>Restaurants</h2>' +
    '<p class="r-intro">Ranked by how many trusted local sources vouch for each place.</p>' +
    '<div id="restaurants-notices"></div>' +
    '<form id="restaurants-filters" class="r-filters" onsubmit="return false">' +
      '<fieldset class="r-field">' +
        '<legend>Search from</legend>' +
        '<div class="r-origin" id="restaurants-origin">…</div>' +
        '<div class="r-buttons">' +
          '<button type="button" class="r-btn" id="restaurants-locate">My location</button>' +
          '<button type="button" class="r-btn" id="restaurants-pick" aria-pressed="false">Pick on map</button>' +
          '<button type="button" class="r-btn" id="restaurants-reset">City centre</button>' +
        '</div>' +
        '<p class="r-origin-msg" id="restaurants-origin-msg" role="status" aria-live="polite"></p>' +
      '</fieldset>' +
      '<label class="r-field">Within' +
        '<select id="restaurants-radius">' +
          RADII.map(function (r) { return '<option value="' + r + '">' + formatDistance(r) + '</option>'; }).join('') +
        '</select>' +
      '</label>' +
      '<fieldset class="r-field">' +
        '<legend>Price</legend>' +
        '<div class="r-buttons r-prices">' +
          [1, 2, 3, 4].map(function (p) {
            return '<button type="button" class="r-btn" data-price="' + p + '" aria-pressed="false">' +
                   priceText(p) + '</button>';
          }).join('') +
        '</div>' +
      '</fieldset>' +
      '<label class="r-field">Cuisine' +
        '<select id="restaurants-cuisine"><option value="">Any cuisine</option></select>' +
      '</label>' +
      '<label class="r-check"><input type="checkbox" id="restaurants-open"> Open now</label>' +
      '<label class="r-check"><input type="checkbox" id="restaurants-recognised" checked>' +
        ' Only places recognised by trusted sources</label>' +
    '</form>' +
    '<div id="restaurants-status" class="r-status" role="status" aria-live="polite"></div>' +
    '<ol id="restaurants-list" class="r-list"></ol>';

  // ---------------------------------------------------------------------
  // Map layers
  // ---------------------------------------------------------------------

  function onPinClick(props) {
    if (state.picking) return;
    sidebar.show('restaurants');
    select(props.id, 'map');
  }

  view.addLayer({
    id: 'restaurants:area', type: 'fill', data: EMPTY, z: 4,
    paint: { 'fill-color': COLORS.origin, 'fill-opacity': 0.06 }
  });
  view.addLayer({
    id: 'restaurants:area-edge', type: 'line', data: EMPTY, z: 9,
    paint: { 'line-color': COLORS.origin, 'line-width': 1.5,
             'line-opacity': 0.6, 'line-dasharray': [2, 2] }
  });
  view.addLayer({
    id: 'restaurants:other', type: 'circle', data: EMPTY, z: 20,
    paint: { 'circle-radius': 5, 'circle-color': COLORS.other,
             'circle-stroke-width': 1, 'circle-stroke-color': '#fff' },
    legend: { label: 'Other restaurants', color: COLORS.other },
    onClick: onPinClick
  });
  view.addLayer({
    id: 'restaurants:recognised', type: 'circle', data: EMPTY, z: 21,
    paint: {
      // Bigger pin = higher local score.
      'circle-radius': ['interpolate', ['linear'], ['get', 'score'], 0, 6, 20, 10],
      'circle-color': COLORS.recognised,
      'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff'
    },
    legend: { label: 'Recognised restaurants', color: COLORS.recognised },
    onClick: onPinClick
  });
  view.addLayer({
    id: 'restaurants:selected', type: 'circle', data: EMPTY, z: 23,
    paint: { 'circle-radius': 14, 'circle-color': 'rgba(0,0,0,0)',
             'circle-stroke-width': 3, 'circle-stroke-color': COLORS.selected }
  });
  view.addLayer({
    id: 'restaurants:origin', type: 'circle', data: EMPTY, z: 25,
    paint: { 'circle-radius': 7, 'circle-color': COLORS.origin,
             'circle-stroke-width': 2, 'circle-stroke-color': '#fff' },
    legend: { label: 'Restaurant search point', color: COLORS.origin }
  });

  // ---------------------------------------------------------------------
  // Origin: city centre, my location, or a point picked on the map
  // ---------------------------------------------------------------------

  function setOrigin(lonLat, label) {
    state.origin = lonLat;
    state.originLabel = label;
    $('origin').textContent = label;
    $('origin-msg').textContent = '';
    view.setData('restaurants:origin', {
      type: 'Feature', geometry: { type: 'Point', coordinates: lonLat }, properties: {}
    });
    drawArea();
  }

  function drawArea() {
    var area = circle(state.origin, state.radius);
    view.setData('restaurants:area', area);
    view.setData('restaurants:area-edge', area);
  }

  function fitArea() {
    var ring = circle(state.origin, state.radius).geometry.coordinates[0];
    var lons = ring.map(function (c) { return c[0]; });
    var lats = ring.map(function (c) { return c[1]; });
    view.fitBounds([[Math.min.apply(null, lons), Math.min.apply(null, lats)],
                    [Math.max.apply(null, lons), Math.max.apply(null, lats)]], 40);
  }

  function setPicking(on) {
    state.picking = on;
    $('pick').setAttribute('aria-pressed', on ? 'true' : 'false');
    document.getElementById('map').classList.toggle('restaurants-picking', on);
    $('origin-msg').textContent = on ? 'Click the map to choose a starting point. Esc cancels.' : '';
  }

  view.onMapClick(function (lonLat) {
    if (!state.picking) return;
    setPicking(false);
    if (!inCity(lonLat)) {
      $('origin-msg').textContent = 'That point is outside ' + state.config.city.name +
                                    '. Pick a point inside the city.';
      return;
    }
    setOrigin(lonLat, 'Point picked on the map');
    search();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && state.picking) setPicking(false);
  });

  $('pick').addEventListener('click', function () { setPicking(!state.picking); });

  $('reset').addEventListener('click', function () {
    setPicking(false);
    var city = state.config.city;
    setOrigin(city.center, city.center_label || city.name);
    search();
  });

  $('locate').addEventListener('click', function () {
    var button = $('locate');
    setPicking(false);
    if (!navigator.geolocation) {
      $('origin-msg').textContent = 'This browser cannot share your location.';
      return;
    }
    button.disabled = true;
    button.textContent = 'Locating…';
    navigator.geolocation.getCurrentPosition(function (pos) {
      button.disabled = false;
      button.textContent = 'My location';
      var here = [pos.coords.longitude, pos.coords.latitude];
      if (!inCity(here)) {
        $('origin-msg').textContent = 'You seem to be outside ' + state.config.city.name +
          '. Pick a point on the map instead.';
        return;
      }
      setOrigin(here, 'Your location');
      search();
    }, function (err) {
      button.disabled = false;
      button.textContent = 'My location';
      $('origin-msg').textContent = err.code === 1
        ? 'Location access was blocked. Allow it in the browser, or pick a point on the map.'
        : 'Could not find your location. Try again, or pick a point on the map.';
    }, { timeout: 10000, maximumAge: 60000 });
  });

  // ---------------------------------------------------------------------
  // Filters (applied locally to the ranked list)
  // ---------------------------------------------------------------------

  $('radius').addEventListener('change', function () {
    state.radius = Number(this.value);
    drawArea();
    search();
  });

  panel.querySelector('.r-prices').addEventListener('click', function (e) {
    var button = e.target.closest('[data-price]');
    if (!button) return;
    var price = Number(button.getAttribute('data-price'));
    var on = button.getAttribute('aria-pressed') !== 'true';
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
    state.filters.prices = state.filters.prices.filter(function (p) { return p !== price; });
    if (on) state.filters.prices.push(price);
    applyFilters();
  });

  $('cuisine').addEventListener('change', function () {
    state.filters.cuisine = this.value;
    applyFilters();
  });

  $('open').addEventListener('change', function () {
    state.filters.openNow = this.checked;
    applyFilters();
  });

  $('recognised').addEventListener('change', function () {
    state.filters.recognisedOnly = this.checked;
    if (state.data) renderCuisineOptions();
    applyFilters();
  });

  function clearFilters() {
    state.filters = { prices: [], cuisine: '', openNow: false, recognisedOnly: state.filters.recognisedOnly };
    panel.querySelectorAll('[data-price]').forEach(function (b) { b.setAttribute('aria-pressed', 'false'); });
    $('cuisine').value = '';
    $('open').checked = false;
    applyFilters();
  }

  function visibleResults() {
    var f = state.filters;
    return state.data.results.filter(function (r) {
      if (f.recognisedOnly && !r.recognised) return false;
      if (f.prices.length && f.prices.indexOf(r.price) === -1) return false;
      if (f.cuisine && !r.cuisines.some(function (c) { return c.key === f.cuisine; })) return false;
      if (f.openNow && r.open_now !== true) return false;
      return true;
    });
  }

  // Counts reflect the "recognised only" switch, so "Seafood (3)" means three rows.
  function renderCuisineOptions() {
    var counts = {};
    state.data.results.forEach(function (r) {
      if (state.filters.recognisedOnly && !r.recognised) return;
      r.cuisines.forEach(function (c) {
        counts[c.key] = counts[c.key] || { label: c.label, n: 0 };
        counts[c.key].n++;
      });
    });
    var keys = Object.keys(counts).sort(function (a, b) {
      return counts[b].n - counts[a].n || counts[a].label.localeCompare(counts[b].label);
    });
    if (state.filters.cuisine && !counts[state.filters.cuisine]) state.filters.cuisine = '';
    $('cuisine').innerHTML = '<option value="">Any cuisine</option>' + keys.map(function (k) {
      return '<option value="' + esc(k) + '"' + (k === state.filters.cuisine ? ' selected' : '') + '>' +
             esc(counts[k].label) + ' (' + counts[k].n + ')</option>';
    }).join('');
  }

  // ---------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------

  function search() {
    var seq = ++state.seq;
    setStatus('loading', '<span class="r-spinner" aria-hidden="true"></span>Finding restaurants…');
    $('list').innerHTML = '';

    var url = API + '/search?lat=' + state.origin[1].toFixed(6) +
              '&lon=' + state.origin[0].toFixed(6) + '&radius=' + state.radius;
    getJSON(url).then(function (data) {
      if (seq !== state.seq) return;
      state.data = data;
      state.selectedId = null;
      renderNotices();
      renderCuisineOptions();
      applyFilters();
      fitArea();
    }).catch(function (err) {
      if (seq !== state.seq) return;
      state.data = null;
      renderPins([]);
      view.setData('restaurants:selected', EMPTY);
      showError(err.message, search);
    });
  }

  function applyFilters() {
    if (!state.data) return;
    var results = visibleResults();
    if (state.selectedId && !results.some(function (r) { return r.id === state.selectedId; })) {
      state.selectedId = null;
      view.setData('restaurants:selected', EMPTY);
    }
    renderPins(results);
    renderList(results);
    renderStatus(results);
  }

  // ---------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------

  function setStatus(kind, html) {
    var el = $('status');
    el.className = 'r-status r-status-' + kind;
    el.innerHTML = html;
  }

  function showError(message, retry) {
    setStatus('error', '<strong>Something went wrong.</strong> ' + esc(message) +
              ' <button type="button" class="r-btn r-retry">Try again</button>');
    $('status').querySelector('.r-retry').addEventListener('click', retry);
  }

  function renderNotices() {
    var data = state.data || state.config;
    var notes = [];
    if (data.demo) {
      notes.push('<div class="r-notice r-notice-demo"><strong>Demo data.</strong> These are ' +
        'fictional restaurants and placeholder awards. ' + esc(data.demo_reason || '') +
        ' Add API keys to <code>.env</code> to see real ' + esc(state.config.city.name) +
        ' restaurants - see <code>restaurants/README.md</code>.</div>');
    }
    if (state.data) {
      Object.keys(state.data.sources).forEach(function (name) {
        var s = state.data.sources[name];
        if (s.state === 'error' || s.state === 'off') {
          notes.push('<div class="r-notice r-notice-warn"><strong>' +
            (name === 'google' ? 'Google' : 'Yelp') + ' not used.</strong> ' + esc(s.message) + '</div>');
        }
      });
      if (!state.data.demo && state.data.recognition.loaded === 0) {
        notes.push('<div class="r-notice r-notice-warn"><strong>No recognition entries yet.</strong> ' +
          'Without them nothing counts as recognised. Add real entries to the city’s ' +
          'recognition file - see <code>restaurants/README.md</code>.</div>');
      }
      if (state.data.warnings.length) {
        notes.push('<details class="r-notice r-notice-warn"><summary>' +
          state.data.warnings.length + ' data warning(s)</summary><ul>' +
          state.data.warnings.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join('') +
          '</ul></details>');
      }
    }
    $('notices').innerHTML = notes.join('');
  }

  function renderStatus(results) {
    var data = state.data;
    var all = data.results.length;
    var hidden = data.excluded.chains
      ? ' · ' + data.excluded.chains + ' chain' + (data.excluded.chains === 1 ? '' : 's') + ' hidden'
      : '';

    if (results.length) {
      setStatus('ok', results.length + ' of ' + all + ' restaurants, best first' + hidden);
      return;
    }
    if (!all) {
      setStatus('empty', '<strong>No restaurants found</strong> within ' +
        formatDistance(state.radius) + ' of ' + esc(state.originLabel) + '. Try a larger distance.');
      return;
    }
    var recognised = data.results.filter(function (r) { return r.recognised; }).length;
    if (state.filters.recognisedOnly && !recognised) {
      setStatus('empty', '<strong>None of the ' + all + ' restaurants here are recognised</strong> ' +
        'by a trusted source yet. <button type="button" class="r-btn r-show-all">Show all ' + all +
        ' ranked by rating</button>');
      $('status').querySelector('.r-show-all').addEventListener('click', function () {
        $('recognised').checked = false;
        state.filters.recognisedOnly = false;
        renderCuisineOptions();
        applyFilters();
      });
      return;
    }
    setStatus('empty', '<strong>No restaurants match these filters.</strong> ' +
      '<button type="button" class="r-btn r-clear">Clear filters</button>');
    $('status').querySelector('.r-clear').addEventListener('click', clearFilters);
  }

  function renderPins(results) {
    view.setData('restaurants:recognised', points(results.filter(function (r) { return r.recognised; })));
    view.setData('restaurants:other', points(results.filter(function (r) { return !r.recognised; })));
    view.refreshLegend();
  }

  function chipHTML(row) {
    var weak = (row.kind === 'rating' && !row.strong) || (row.kind === 'recognition' && !row.points);
    var cls = 'r-chip r-chip-' + row.kind + (weak ? ' r-chip-weak' : '') +
              (row.placeholder ? ' r-chip-placeholder' : '');
    var text = esc(row.text) + (row.placeholder ? ' <span class="r-chip-tag">placeholder</span>' : '');
    var title = row.note || row.why || '';
    var url = safeUrl(row.url);
    return url
      ? '<a class="' + cls + '" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer"' +
        (title ? ' title="' + esc(title) + '"' : '') + '>' + text + '</a>'
      : '<span class="' + cls + '"' + (title ? ' title="' + esc(title) + '"' : '') + '>' + text + '</span>';
  }

  function breakdownHTML(r) {
    return '<details class="r-why"><summary>Local score ' + r.score + '</summary><ul>' +
      r.breakdown.map(function (row) {
        return '<li><span>' + esc(row.text) + '</span><span class="r-points">' +
          (row.points ? '+' + row.points : esc(row.why || '0')) + '</span></li>';
      }).join('') + '</ul></details>';
  }

  function itemHTML(r, i) {
    var meta = [
      r.cuisines.slice(0, 2).map(function (c) { return c.label; }).join(', '),
      priceText(r.price),
      formatDistance(r.distance_m),
      openText(r.open_now)
    ].filter(Boolean).join(' · ');
    var chips = r.breakdown
      .filter(function (row) { return row.kind === 'recognition' || row.kind === 'rating'; })
      .map(chipHTML).join('');
    var url = safeUrl(r.url);

    return '<li class="r-item' + (r.id === state.selectedId ? ' is-selected' : '') +
             (r.recognised ? ' is-recognised' : '') + '" data-id="' + esc(r.id) + '">' +
      '<span class="r-rank">' + (i + 1) + '</span>' +
      '<div class="r-body">' +
        '<button type="button" class="r-name" aria-pressed="' + (r.id === state.selectedId) + '">' +
          esc(r.name) + '</button>' +
        (meta ? '<div class="r-meta">' + esc(meta) + '</div>' : '') +
        (chips ? '<div class="r-chips">' + chips + '</div>' : '') +
        breakdownHTML(r) +
        (url ? '<a class="r-maplink" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' +
               'Directions & details ↗</a>' : '') +
      '</div>' +
    '</li>';
  }

  function renderList(results) {
    $('list').innerHTML = results.map(itemHTML).join('');
  }

  // Clicking a row (anywhere but its links) selects it.
  $('list').addEventListener('click', function (e) {
    if (e.target.closest('a, summary, details ul')) return;
    var item = e.target.closest('.r-item');
    if (item) select(item.getAttribute('data-id'), 'list');
  });

  function select(id, from) {
    if (!state.data) return;
    var result = null;
    state.data.results.forEach(function (r) { if (r.id === id) result = r; });
    state.selectedId = result ? id : null;
    view.setData('restaurants:selected', result ? points([result]) : EMPTY);

    var items = $('list').querySelectorAll('.r-item');
    for (var i = 0; i < items.length; i++) {
      var on = items[i].getAttribute('data-id') === state.selectedId;
      items[i].classList.toggle('is-selected', on);
      items[i].querySelector('.r-name').setAttribute('aria-pressed', on ? 'true' : 'false');
      if (on && from === 'map') items[i].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    if (result && from === 'list') view.flyTo([result.lon, result.lat]);
  }

  // ---------------------------------------------------------------------
  // Start once the tab is first shown
  // ---------------------------------------------------------------------

  function start() {
    if (state.started) return;
    state.started = true;
    setStatus('loading', '<span class="r-spinner" aria-hidden="true"></span>Loading…');

    getJSON(API + '/config').then(function (config) {
      state.config = config;
      state.radius = config.radius.default;
      $('radius').value = String(state.radius);
      renderNotices();
      setOrigin(config.city.center, config.city.center_label || config.city.name);
      search();
    }).catch(function (err) {
      state.started = false;
      showError(err.message, start);
    });
  }

  sidebar.onShow('restaurants', start);
})(window);
