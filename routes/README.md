# Routes

Running route generator: adjustable distance, start point, loop or
out-and-back, and elevation. Public bathrooms come later as a sub-feature.

Owner: Rob · Branch: `routes`

## Files

| File | What |
| --- | --- |
| `layers.js` | The Routes tab: controls, start-point picker, route layer, stats |
| `api.py` | `GET /api/routes/generate` - routes on streets from OpenRouteService |
| `elevation.py` | Heights and climb for a route, from the elevation grid |
| `tools/build_elevation_grid.py` | Builds a city's grid from USGS data. Run once per city |
| `tests/` | Unit tests, no network: `python -m unittest discover -s routes/tests -t .` |
| `../data/elevation/<city>.json` | The generated grid. Do not edit by hand |

## Generating a route

```
GET /api/routes/generate?lon=&lat=&km=5&shape=loop|out-and-back&seed=1
```

Returns a GeoJSON LineString Feature with `requestedM`, `lengthM` (actual),
`ascent`, `descent` and an `elevation` summary. Routes use ORS's
`foot-walking` profile, called from the server so `ORS_API_KEY` never reaches
the browser. Without a key it answers 501 and the tab shows a straight-line
preview with an explanation, so it still works on a machine with no key.

What the server does that ORS doesn't:

- **Avoids ferries.** ORS's walking profile treats ferry lines as walkable.
  Near the Seaport a "5 km" loop came back as 46 km, 36 of them by boat.
- **Loops:** asks for three at once and keeps the one closest to the
  distance. ORS's round trips miss by up to ~18%, much more where few streets
  connect. If the best is still more than 10% off, it asks again for a scaled
  length - a request that keeps returning 8 km is asked for ~3 km next.
- **Out-and-back:** ORS only makes loops, so this aims at a turnaround,
  routes there, and retraces the leg. It re-aims to correct the length and,
  if a direction is blocked - aim across the harbour and ORS snaps to the same
  spot on the shore however far you aim - tries another direction.
- **Caps ORS calls at 6 per click**, so the free tier (2,000 a day, 40 a
  minute) allows 330+ routes a day. Hitting the per-minute limit gives a
  "try again shortly" message, not an error page.

On test runs from Boston Common, results landed within about 1-8% of the
requested distance. The page warns when a route is more than 15% off.

## Elevation

Elevation is an **input** to route generation (flattest / hilliest), so the
climb figures have to rank routes correctly - not just look plausible.

### Why not OpenRouteService's own elevation

ORS returns heights with every route, but measured against USGS lidar along
real Boston routes it reads about **8 m high on average** (worst 26 m), and
its profile is noisy. One 5 km loop from Boston Common:

| | Climb |
| --- | --- |
| USGS lidar, every 25 m | **41 m** |
| ORS as reported | 114 m |
| ORS, best smoothing found | 52 m (+27%) |

Smoothing removes jitter but not a wrong profile. Worse, across four
candidate loops from Boston Common, ORS's ranking was wrong: the loop it
called flattest was third-flattest on the ground, and the actual flattest it
rated nearly the hilliest. In flat areas the noise is bigger than the real
differences. At Mission Hill, where the hills are real, it ranked correctly.

### What we use instead

`data/elevation/boston.json`: a 50 m grid of USGS 3DEP heights over the city's
bounds, sampled once from USGS's elevation service and read locally - no
network call per route. `elevation.measure(grid, coords)` resamples a route
every 25 m, looks up each height, and counts climb with a 1 m threshold so
sub-metre ripple doesn't add up.

Climbs are reported with a `coverage` figure. A route that leaves the grid is
measured over the part that's on it and flagged `complete: false`, so the UI
can say the figure is partial rather than show a wrong number.

### How accurate it is

Validated on 13 October 2026 against direct USGS lookups:

- **Heights:** on 400 random points along the Cambridge riverside, within
  0.53 m on average; 95% within 2.1 m.
- **Ranking:** on eight candidate 5 km loops (four each from Boston Common and
  Mission Hill), the grid picked the true flattest route at both starts. ORS
  got it wrong at Boston Common. Where the grid's order differed from the
  reference, the routes involved were within 3 m of each other - a tie.
- **Climb totals:** typically within ~7 m of the reference.

There is no perfect reference. USGS lidar measures bare ground, so it sees
*under* bridges and *down into* the highway and rail cuts that cross Boston -
the Mass Pike, I-93, the Orange Line corridor - none of which a runner on the
bridge above ever climbs out of. Direct 25 m sampling turns those into sharp
dips (removed above with a 3-point median filter); the 50 m grid smears them
into wide dips that look like real terrain.

**Known limitation:** routes that cross or run beside those cuts can read high.
The worst case found was a loop past South Station reading +23 m over the
reference (60 m against 37 m). The principled fix is to follow the bridge deck
instead of the ground - interpolate straight across any part of the route that
OSM marks `bridge=yes`. Not done yet.

### Coverage

The Boston grid covers the city's bounds in `shared/cities.json`, and is
**required** to cover every street and path in Cambridge within 100 m of the
Charles River bank, so runners can follow the river. The build fetches those
from OpenStreetMap, extends the grid if any fall outside, and fails unless
every point on them can be measured. Today all of them already lie inside the
Boston bounds.

How "within 100 m of the river" is measured:

- From the **bank**, not the centreline - the Basin is up to ~600 m wide, so
  100 m from the centreline wouldn't reach the Cambridge shore.
- OSM names only the centreline "Charles River"; the water polygons it runs
  through are taken to be the river. That includes Broad Canal and Lechmere
  Canal, which OSM maps as part of it.
- A street counts if any part of it is within 100 m - so side streets meeting
  Memorial Drive are in.
- Footpaths and cycleways count, not just roads: the riverside paths are where
  people run.

### Rebuilding

```
python -m routes.tools.build_elevation_grid --dry-run     # size only
python -m routes.tools.build_elevation_grid               # ~15 minutes
```

Only needed if the city's bounds or the grid step change. US cities only:
3DEP covers the United States. Elsewhere, fall back to ORS elevation and
label it approximate.
