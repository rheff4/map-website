# Fuel Stop — Sprint 1 PRD, Revision 4

**Pedro Silva · Rob Hefferon** · AI Builder Space Proseminar
Revision 4, 19 October 2026. Supersedes Revision 3 (13 October 2026).

> The product was called "Map Website" until this revision. Revisions 1
> (`SilvaHefferon_ProjectBrief.pdf`), 2 (`Map Website_Sprint 1_PRD 2.md`) and
> 3 (`Map Website_Sprint 1_PRD 3.md`) are unchanged on disk as the record of
> earlier plans. Where they disagree with this document, this one is current.

---

## What changed since Revision 3

Both feature PRs are merged, the app has a name, and it is live at
**https://map-website-snowy.vercel.app**. Of the two features Revision 3 added,
run to a restaurant is built; restroom-friendly routes are not. Distance is
now judged in metres, not percent.

| Area | Revision 3 | Now |
| --- | --- | --- |
| **Name** | "Map Website", open question | **Fuel Stop**, with a logo and colours |
| **PRs #1 and #2** | Open, awaiting review | **Merged** 19 October. PR #3 (price bands) also merged |
| **Elevation control** | Data built; control not yet | **Built:** flattest / balanced / hilliest |
| **Run to a restaurant** | Planned | **Built**, and widened: run to *any point* picked on the map |
| **Out-and-back** | Built, in the UI | **Removed from the UI;** "To a point" took its place. The server still supports it |
| **Restroom-friendly routes** | Planned | **Not built** |
| **Distance tolerance** | "Within 10%" | **Within 500 m**, at every distance |
| **Map clicks** | Every click moved the start | Only on the Routes tab, and never on a restaurant pin |
| **Price levels** | `$`–`$$$$` | Shown with dollars, e.g. `$$$ ($50–75)` |
| **Hosting** | Local server only | **Deployed on Vercel**; local server unchanged |
| **Routing quota** | A risk | **Ran out on 19 October** (see Risks) |

---

## The idea and the user

Unchanged. Fuel Stop is an interactive web map for "foodie runners", people
new to a city who care about both their run and their next meal. It brings
running routes, public restrooms and authentic local restaurants together on
one map, and now runs from one to the other.

---

## Scope for this sprint

### Must-haves

| # | Feature | State |
| --- | --- | --- |
| 1 | Route generator: adjustable distance, start point, **loop or run to a point** | **Built** |
| 2 | Elevation as an input: flattest / hilliest | **Built** |
| 3 | Restaurant finder, filtered by price and "recognised" picks | **Built**; prices now in dollars |
| 4 | Restaurant quality from established sources, not star ratings alone | **Built** |
| 5 | Public restroom finder | **Built** (117 restrooms) |
| 6 | Run to a restaurant | **Built** |
| 7 | Restroom-friendly routes | **Not built.** Carried over |

Feature 1 changed shape: out-and-back is gone from the page, and a one-way run
to a point picked on the map replaced it. A run to a restaurant is the same
feature with the restaurant as the point.

### Out of scope

Unchanged from Revision 3: recommending restaurants *along* a route, live
navigation or GPS tracking, user accounts and saved routes, user-submitted
reviews, and a mobile app (the page works on a phone-sized screen).

---

## Built: routes

**Distance is within 500 m, not a percentage.** Every check of "close enough"
uses 500 m at every distance: retrying a loop, aiming a run to a point, and
choosing between candidates. A percentage let long runs drift too far: at 10%,
a 20 km run could be 2 km out. The results panel shows the difference in
metres ("+85 m"). If no route lands within 500 m, the page says so. On short
runs this is looser than before: 400 m on a 2 km loop is 20%, but passes.

**Hilliness picks among routes near the distance.** Flattest and hilliest
compare the candidates within 500 m of the requested distance by their climb
on the USGS grid. Distance always comes first. When the candidates' climb
differs by under 10 m, the page says routes here are much the same.

**Run to a point.** Choosing *To a point* turns the next map click into the
destination; later clicks move the start. **Run here** on any restaurant in the
Restaurants tab sets that restaurant as the destination and opens the Routes
tab. The result shows distance, climb, estimated time and arrival time.

- **Shorter than the direct route:** within 500 m either way, the direct
  route *is* the run. More than 500 m short, the runner still gets the direct
  route, with a warning that the point is too far for that distance.
- **Longer runs loop round instead of doubling back.** The route bends through
  three waypoints on a circular arc from start to destination, on one side of
  the straight line. Long runs to nearby places curl round towards a full
  loop. This replaced a detour through one waypoint, which made hairpins: out
  and back up the next street over. On four test runs every old route was a
  hairpin and every new one an open loop. Short dead-end spurs the router adds
  to reach a waypoint are cut.
- **Known limit:** when the run is many times the direct distance, landing
  within 500 m is harder. A 20 km run to a place 1.3 km away came back 824 m
  over after five routing calls. The page says so rather than hiding it.

**Map clicks** act only while the Routes tab is open, and never on a
restaurant pin (that selects the restaurant). This closes the bug Pedro raised
in PR #1.

---

## Not built: restroom-friendly routes

The design in Revision 3 stands: a restroom serves the route within about
150 m; rank by the longest stretch without one, then by how many the route
passes; show hours where known and "hours unknown" otherwise; never promise a
restroom is open. It now fits the code more easily: hilliness already ranks
candidates near the distance, so restrooms would rank them first, before hills,
as Revision 3 proposed.

---

## Name and brand

The app is **Fuel Stop**. The logo is an orange dot (the runner), a green path
they run, and the fork it ends in. It lives in `shared/brand/` as SVG with a
transparent background: `logo.svg` with the name, `mark.svg` without (also the
browser icon).

| Colour | Hex | Used for |
| --- | --- | --- |
| Green | `#1b4436` | Header text, tabs, buttons, the route line |
| Orange | `#e8743f` | Recognised restaurants, run destinations |
| Cream | `#f8f6f1` | Page and panel backgrounds |

Restroom pins moved from green to blue, so they stand apart from the green
route. Orange is too light for white text, so the restaurant source tags use
a pale orange with dark text.

---

## Restaurant quality: the scoring model

Unchanged from Revision 2. Weights live in `restaurants/scoring.py`, and
`restaurants/README.md` has the full model.

**New in display (PR #3): prices in dollars.** Each `$` level now shows what it
means in money, taken from the same source as the level: Google's own range
for that restaurant, else Yelp's per-person band, else Michelin's band for a
full meal without drinks. Google's level alone shows no figure, because Google
publishes none. Without Google or Yelp keys, only Michelin-listed places have a
price.

---

## How it is built

- **One map, owned by `shared/`.** Unchanged, with one addition: **messages
  between features**, `view.emit` / `view.on`. The only message so far is
  `restaurants:run-to` `{name, lon, lat}`. It is the cross-feature hook
  Revision 3 called for, and the one sanctioned crossing between the two
  halves.
- **One local server** (`python server/app.py`, standard library only),
  unchanged.
- **Vercel** serves the same code: static files directly, and every `/api/*`
  request through `api/index.py`, which loads the same feature handlers.
  - `ORS_API_KEY` is set in the Vercel project; `.vercelignore` keeps `.env`
    from being uploaded.
  - A fresh serverless instance starts with an empty cache, and the city's
    restaurant data takes minutes to fetch from Overpass. So a snapshot ships
    with the site (`data/cache-seed/`, refreshed with `python -m server.seed_cache`).
  - Pushes to `main` deploy automatically, though on 19 October two pushes
    did not until a deploy was started by hand.

---

## Cost and dependencies

Unchanged, except that the routing quota is now the binding constraint.

- **No card is needed.** Restaurants run on OpenStreetMap; routing uses
  OpenRouteService's free tier; Vercel's free plan hosts the site.
- **The routing quota ran out on 19 October.** OpenRouteService answers a
  spent quota with "Quota exceeded", so for the rest of the day no visitor can
  generate a route. The page now says that plainly; until it was fixed it
  claimed the API key was rejected. What used the 2,000 calls is not known;
  testing that day accounts for perhaps 150–200. The OpenRouteService
  dashboard shows usage.
- Every visitor shares that one quota. Loops ask for up to 6 routes per click,
  and flattest / hilliest always use the full 6. **HeiGIT's free academic plan
  (10,000 a day, with an MIT email) is now urgent, not optional.**

---

## Success criteria

1. **Restaurants.** Unchanged: a local expert approves **at least 90%** of
   the recommendation list.
2. **Routes.** Unchanged: a test runner runs a generated route and confirms
   it works as planned.
3. **Run to a restaurant.** A test runner starts at a chosen point and reaches
   a chosen restaurant on a generated route **within 500 m of the requested
   distance** *(was: within 10%)*, with no impassable sections.
4. **Restroom-friendly routes.** Unchanged, pending the feature.

**Still open:** "90% of the list" has no list length. The expert and the test
runner are still to be recruited.

---

## Open questions and risks

| # | Question | Status |
| --- | --- | --- |
| 1 | A permanent name | **Closed — Fuel Stop** |
| 2 | Recruit the local food expert | Open |
| 3 | Recruit the test runner | Open |
| 5 | Recognition data: sources to verify | Open — each entry copied from its published source with a link |
| 7 | How many results does the expert review? | Open |
| 10 | When distance, hilliness and restrooms conflict, which wins? | **Distance first** (within 500 m), then hills. Restrooms would slot in between, as proposed |
| 11 | How close must a restroom be to count? | Proposed 150 m; untested |
| 12 | How to present restroom hours, unknown for 93% | Open |
| 13 | Which detour shapes are acceptable? | **Closed — loops, not hairpins:** an arc of waypoints, spurs cut |
| 14 | What does a map click do? | **Closed:** start point on the Routes tab; destination while picking; nothing on restaurant pins |
| 15 | Pedro's agreement to the cross-feature scope | **Built** as `view.emit` / `view.on`. Pedro to confirm, along with the colour changes in his Restaurants files |
| 16 | Routing quota | **Realised 19 October.** Academic plan; route caching |
| 17 | Is a public link agreed? | **New.** The site is public. Revision 3 planned to agree deployment with Pedro first |
| 18 | Distance tolerance on short runs | **New.** 500 m is loose on a 2 km run. Option: 500 m or 10%, whichever is smaller |
| 19 | Vercel's per-instance rate limits | **New.** Limits are counted per serverless instance, so the shared quota is guarded less tightly than on one server |

Questions 4, 6, 8 and 9 were closed in Revision 3.

---

## Status at this revision

| | State |
| --- | --- |
| `main` | PRs #1, #2 and #3 merged, plus the work above. 173 tests (100 routes, 73 restaurants) |
| Live site | https://map-website-snowy.vercel.app, running the latest `main` |
| Routing | Down for 19 October: daily quota spent |

**Next:**

1. Apply for HeiGIT's academic plan, and check the OpenRouteService dashboard
   for what spent the quota.
2. Confirm with Pedro: the public link, the cross-feature hook, and the colour
   changes in his files.
3. Build restroom-friendly routes on the candidate ranking hilliness already
   uses.
4. Recruit the food expert and the test runner, and set the review list's
   length.
