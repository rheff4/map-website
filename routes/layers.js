/*
 * Routes - map layers.
 *
 * Owner: Rob - branch `routes`
 *
 * shared/index.html already loads this file, so nothing in shared/ has to
 * change when you fill it in. Keep every id namespaced `routes:`.
 * The contract is documented in shared/README.md.
 */
(function (global) {
  'use strict';

  // The shared map, created in shared/index.html. Features never make their own.
  var view = global.view;

  // ---------------------------------------------------------------------
  // Your feature goes here. Sketch:
  //
  //   view.addLayer({
  //     id: 'routes:line',
  //     type: 'line',
  //     data: routeGeoJSON,            // LineString from the generator
  //     paint: { 'line-color': '#2b6cb0', 'line-width': 5 },
  //     legend: { label: 'Running route', color: '#2b6cb0' }
  //   });
  //
  // Then redraw on each new route without re-adding the layer:
  //
  //   view.setData('routes:line', newRouteGeoJSON);
  //   view.fitBounds(boundsOf(newRouteGeoJSON));
  //
  // Start against a hardcoded LineString. The OpenRouteService call needs a
  // server to hold the API key, which does not exist yet - but the distance
  // slider, start-point click, loop/out-and-back toggle and elevation readout
  // can all be built and tested before that lands. Swapping the stub for
  // fetch('/api/routes/generate') is then a few lines.
  // ---------------------------------------------------------------------

  void view;
})(window);
