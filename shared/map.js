/*
 * Map Website - shared base map.
 *
 * There is ONE map on the page and it lives here. Feature branches never
 * create a map; they hand layers to the one that already exists:
 *
 *     const view = MapWebsite.createMap({ container: 'map' });
 *
 *     view.addLayer({
 *       id: 'routes:line',              // must start with your namespace
 *       type: 'line',
 *       data: someGeoJSON,
 *       paint: { 'line-color': '#2b6cb0', 'line-width': 5 },
 *       legend: { label: 'Running route', color: '#2b6cb0' }
 *     });
 *
 * See shared/README.md for the rules. Keep changes here small, and tell the
 * other person - both branches depend on this file.
 */
(function (global) {
  'use strict';

  var BASEMAP_STYLE = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';
  var BACKGROUND_COLOR = '#f5efe6';
  var DEFAULT_CENTER = [-71.0656, 42.3550]; // Boston Common
  var DEFAULT_ZOOM = 14;

  // Layer ids are global strings in MapLibre, so two features both using
  // "markers" would collide and throw at runtime. Namespacing is enforced
  // rather than left to convention.
  var NAMESPACES = ['routes', 'restaurants', 'shared'];

  // Draw order when a layer does not set `z` itself: fills at the bottom,
  // then lines, then points, then labels. Decided once, here, so that the
  // order does not depend on which branch happens to register first.
  var DEFAULT_Z = { fill: 5, line: 10, circle: 20, symbol: 30 };

  function fail(message) {
    throw new Error('[MapWebsite] ' + message);
  }

  function validate(layer, registry) {
    if (!layer || typeof layer !== 'object') fail('addLayer needs a layer object.');
    if (!layer.id) fail('Layer is missing an id.');

    var namespace = String(layer.id).split(':')[0];
    if (NAMESPACES.indexOf(namespace) === -1) {
      fail('Layer id "' + layer.id + '" must start with one of: ' +
           NAMESPACES.join(':, ') + ':  (e.g. "routes:' + layer.id + '")');
    }
    if (registry[layer.id]) {
      fail('Layer "' + layer.id + '" is already on the map. Use setData() to ' +
           'update it, or removeLayer() first.');
    }
    if (!layer.type) fail('Layer "' + layer.id + '" is missing a type.');
    if (!layer.data) fail('Layer "' + layer.id + '" is missing GeoJSON data.');
  }

  function zOf(layer) {
    return typeof layer.z === 'number' ? layer.z : (DEFAULT_Z[layer.type] || 20);
  }

  function emptyCollection() {
    return { type: 'FeatureCollection', features: [] };
  }

  function createMap(options) {
    options = options || {};

    if (typeof maplibregl === 'undefined') {
      fail('maplibre-gl is not loaded. Add its <script> tag before this one.');
    }

    var map = new maplibregl.Map({
      container: options.container || 'map',
      style: options.style || BASEMAP_STYLE,
      center: options.center || DEFAULT_CENTER,
      zoom: typeof options.zoom === 'number' ? options.zoom : DEFAULT_ZOOM
    });

    var registry = {};   // id -> layer spec
    var ready = false;
    var pending = [];    // work queued until the style finishes loading

    // Anything touching sources or layers has to wait for the style, so
    // callers can register layers immediately without caring about timing.
    function whenReady(fn) {
      if (ready) fn();
      else pending.push(fn);
    }

    // MapLibre reports most layer and source problems as error events rather
    // than throwing, so without this they vanish silently.
    map.on('error', function (e) {
      console.error('[MapWebsite]', (e && e.error && e.error.message) || e);
    });

    map.on('load', function () {
      if (map.getLayer('background')) {
        map.setPaintProperty('background', 'background-color', BACKGROUND_COLOR);
      }
      ready = true;
      pending.forEach(function (fn) { fn(); });
      pending = [];
    });

    // MapLibre inserts a layer *before* an existing one, so to place a layer
    // by z we insert it before the lowest layer that should sit above it.
    //
    // Only layers already ON the map count. The registry also holds layers
    // that are still queued, and naming one of those as the insert point makes
    // MapLibre fire an error event and silently skip the layer - no exception,
    // so the source exists but nothing draws.
    function beforeIdFor(z) {
      var above = Object.keys(registry)
        .map(function (id) { return registry[id]; })
        .filter(function (other) { return zOf(other) > z && map.getLayer(other.id); })
        .sort(function (a, b) { return zOf(a) - zOf(b); });
      return above.length ? above[0].id : undefined;
    }

    function wirePopup(layer) {
      if (typeof layer.popup !== 'function') return;

      map.on('click', layer.id, function (e) {
        var feature = e.features && e.features[0];
        if (!feature) return;
        var html = layer.popup(feature.properties, feature);
        if (!html) return;
        new maplibregl.Popup()
          .setLngLat(feature.geometry.coordinates.slice(0, 2))
          .setHTML(html)
          .addTo(map);
      });
      map.on('mouseenter', layer.id, function () {
        map.getCanvas().style.cursor = 'pointer';
      });
      map.on('mouseleave', layer.id, function () {
        map.getCanvas().style.cursor = '';
      });
    }

    var view = {
      /* Add a feature layer. Safe to call before the map has loaded. */
      addLayer: function (layer) {
        validate(layer, registry);
        registry[layer.id] = layer;

        whenReady(function () {
          map.addSource(layer.id, { type: 'geojson', data: layer.data });
          map.addLayer({
            id: layer.id,
            type: layer.type,
            source: layer.id,
            paint: layer.paint || {},
            layout: layer.layout || {}
          }, beforeIdFor(zOf(layer)));
          wirePopup(layer);
          view.refreshLegend();
        });
        return view;
      },

      /* Swap a layer's GeoJSON without removing and re-adding it. */
      setData: function (id, data) {
        if (!registry[id]) fail('No layer "' + id + '" to update.');
        registry[id].data = data;
        whenReady(function () {
          var source = map.getSource(id);
          if (source) source.setData(data || emptyCollection());
        });
        return view;
      },

      removeLayer: function (id) {
        if (!registry[id]) return view;
        delete registry[id];
        whenReady(function () {
          if (map.getLayer(id)) map.removeLayer(id);
          if (map.getSource(id)) map.removeSource(id);
          view.refreshLegend();
        });
        return view;
      },

      /* Show or hide a layer, keeping its data loaded. */
      setVisible: function (id, visible) {
        if (!registry[id]) fail('No layer "' + id + '" to toggle.');
        registry[id].hidden = !visible;
        whenReady(function () {
          if (map.getLayer(id)) {
            map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
          }
          view.refreshLegend();
        });
        return view;
      },

      /* Hide every layer belonging to one namespace, e.g. 'restaurants'. */
      setNamespaceVisible: function (namespace, visible) {
        Object.keys(registry)
          .filter(function (id) { return id.split(':')[0] === namespace; })
          .forEach(function (id) { view.setVisible(id, visible); });
        return view;
      },

      listLayers: function () {
        return Object.keys(registry);
      },

      /*
       * The camera belongs to the map, not to a feature. Features ask to move
       * it; they do not reach past this and call map.flyTo themselves, or two
       * features will fight over the view on every render.
       */
      flyTo: function (center, zoom) {
        whenReady(function () {
          map.flyTo({ center: center, zoom: typeof zoom === 'number' ? zoom : map.getZoom() });
        });
        return view;
      },

      fitBounds: function (bounds, padding) {
        whenReady(function () {
          map.fitBounds(bounds, { padding: typeof padding === 'number' ? padding : 60 });
        });
        return view;
      },

      /*
       * The legend is rendered from the registered layers, so neither branch
       * has to edit shared markup to add an entry.
       */
      refreshLegend: function () {
        var el = document.getElementById(options.legend || 'legend');
        if (!el) return view;

        var rows = Object.keys(registry)
          .map(function (id) { return registry[id]; })
          .filter(function (layer) { return layer.legend; })
          .sort(function (a, b) { return zOf(a) - zOf(b); })
          .map(function (layer) {
            var count = layer.data && layer.data.features ? layer.data.features.length : null;
            return '<div class="legend-row' + (layer.hidden ? ' is-hidden' : '') + '">' +
              '<span class="legend-swatch" style="background:' +
                (layer.legend.color || '#888') + '"></span>' +
              (layer.legend.label || layer.id) +
              (count === null ? '' : ' <span class="legend-count">(' + count + ')</span>') +
              '</div>';
          });

        el.innerHTML = rows.join('') ||
          '<div class="legend-row legend-empty">No layers yet</div>';
        return view;
      },

      /* Run something once the style is loaded. */
      ready: function (fn) {
        whenReady(function () { fn(view, map); });
        return view;
      },

      /* Escape hatch for anything this wrapper does not cover yet. */
      getMap: function () {
        return map;
      }
    };

    return view;
  }

  global.MapWebsite = {
    createMap: createMap,
    BASEMAP_STYLE: BASEMAP_STYLE,
    BACKGROUND_COLOR: BACKGROUND_COLOR,
    DEFAULT_CENTER: DEFAULT_CENTER,
    DEFAULT_ZOOM: DEFAULT_ZOOM,
    NAMESPACES: NAMESPACES
  };
})(window);
