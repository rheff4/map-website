/*
 * Restaurants - map layers.
 *
 * Owner: Pedro - branch `restaurants`
 *
 * shared/index.html already loads this file, so nothing in shared/ has to
 * change when you fill it in. Keep every id namespaced `restaurants:`.
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
  //     id: 'restaurants:pins',
  //     type: 'circle',
  //     data: restaurantGeoJSON,       // FeatureCollection of Points
  //     paint: {
  //       'circle-radius': 5,
  //       'circle-color': '#e53e3e',
  //       'circle-stroke-width': 1,
  //       'circle-stroke-color': '#fff'
  //     },
  //     legend: { label: 'Restaurants', color: '#e53e3e' },
  //     popup: function (props) { return '<b>' + props.name + '</b>'; }
  //   });
  //
  // Re-filter by price or "quintessential local" without re-adding the layer:
  //
  //   view.setData('restaurants:pins', filtered);
  //
  // Data: spikes/spike.py shows the Overpass query, and what OSM actually has
  // near Boston Common - 312 restaurants, 99% named, 65% with a cuisine tag,
  // but only 37% with opening hours. Don't call Overpass on every map move;
  // it rate-limits. Fetch once, store the result, serve it from here.
  // ---------------------------------------------------------------------

  void view;
})(window);
