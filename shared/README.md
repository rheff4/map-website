# Shared

The map page and code both features use. Keep changes small and tell the other
person, since both branches depend on this folder.

| File             | What                                                    |
| ---------------- | ------------------------------------------------------- |
| `index.html`     | The page. Creates the one map and loads feature scripts |
| `map.js`         | The base map: layer registry, camera, legend            |
| `sidebar.js`     | The right-hand sidebar: one tab and panel per feature   |
| `sample-data.js` | Demo fixture from `spikes/spike.py`, delete when unused |

Run `python server/app.py` and open http://localhost:8000/. No build step, no
install. Opening the file directly does not work: it loads scripts by relative
path and calls the API, so it needs the server.

## The rule

**There is one map, and `shared/` owns it.** Feature branches never create a
map. They add layers to the one that already exists:

```js
view.addLayer({
  id: 'routes:line',                 // namespaced - see below
  type: 'line',                      // line | circle | fill | symbol
  data: geojson,                     // Feature or FeatureCollection
  paint: { 'line-color': '#2b6cb0', 'line-width': 5 },
  legend: { label: 'Running route', color: '#2b6cb0' },
  popup: (props) => `<b>${props.name}</b>`   // optional
});
```

Everything else about your feature lives in your own folder.

## Adding your feature

1. Write `routes/layers.js` (or `restaurants/layers.js`) that fetches your data
   and calls `view.addLayer(...)`.
2. Add one line to `index.html` where the plug-in comment is.

That one line is the only shared edit, which keeps conflicts between the two
branches down to a single predictable spot.

## Rules that stop the two branches colliding

**Namespace your ids.** MapLibre layer ids are global, so two features both
using `markers` collide and throw at runtime. Ids must start with `routes:`,
`restaurants:` or `shared:` — `map.js` throws a clear error if they don't.

**Don't move the camera directly.** Use `view.flyTo()` / `view.fitBounds()`
rather than reaching for `map.flyTo`. If both features recenter on their own
results they will fight over the view.

**Draw order is decided once,** by layer type: fills (5), lines (10), circles
(20), symbols (30). Pass `z` to override. Order does not depend on which branch
registers first.

**Don't edit the map set-up in `index.html`.** Add your script line; leave the
rest.

## API

| Call                                      | Does                                    |
| ----------------------------------------- | --------------------------------------- |
| `createMap({ container, center, zoom })`  | Makes the map, returns `view`           |
| `view.addLayer(layer)`                    | Adds a layer; safe before map load      |
| `view.setData(id, geojson)`               | Swaps a layer's data in place           |
| `view.removeLayer(id)`                    | Removes layer and source                |
| `view.setVisible(id, bool)`               | Shows/hides, keeps data loaded          |
| `view.setNamespaceVisible(ns, bool)`      | Toggles a whole feature                 |
| `view.flyTo(center, zoom)`                | Moves the camera                        |
| `view.fitBounds(bounds, padding)`         | Zooms to fit                            |
| `view.ready((view, map) => {})`           | Runs once the style has loaded          |
| `view.listLayers()`                       | Registered layer ids                    |
| `view.getMap()`                           | Raw MapLibre map, escape hatch          |

The legend renders itself from registered layers, so adding an entry needs no
shared markup.

## Sidebar

The sidebar on the right has a **Restaurants** tab and a **Routes** tab. Each
feature owns the panel behind its tab and fills it from its own `layers.js`,
so the sidebar markup in `index.html` never needs editing:

```js
var panel = sidebar.panel('restaurants');      // a plain <section>
panel.innerHTML = '<h2>Restaurants</h2>' +
  '<label>Max price <select id="restaurants-price">...</select></label>';

sidebar.onShow('restaurants', function () {
  // the user opened this tab - e.g. view.fitBounds(...) to your results
});
```

Prefix element ids inside your panel with your namespace (`restaurants-price`,
`routes-distance`), for the same reason layer ids are namespaced: ids are
global to the page.

| Call                      | Does                                       |
| ------------------------- | ------------------------------------------ |
| `sidebar.panel(name)`     | The `<section>` for a feature's tab        |
| `sidebar.onShow(name, fn)`| Runs `fn` whenever that tab is opened      |
| `sidebar.show(name)`      | Opens a tab                                |
| `sidebar.active()`        | Name of the open tab                       |

Switching tabs only changes the panel. Both features' layers stay on the map;
hide your own with `view.setNamespaceVisible(...)` if you need to.

## Server and API keys

`python server/app.py` serves this page and each feature's API (see the root
README). API keys live in `.env` and are only read on the server - never put a
key in client JavaScript; the repo is public. A feature adds endpoints by
writing `<feature>/api.py` with a `register(router)` function, documented in
`server/router.py`, and calls them from its `layers.js` with
`fetch('/api/<feature>/...')`. The layer contract above does not change.
