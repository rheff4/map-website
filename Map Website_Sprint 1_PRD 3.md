# Map Website — Sprint 1 PRD, Revision 3

**Pedro Silva · Rob Hefferon** · AI Builder Space Proseminar
Revision 3, 13 October 2026. Supersedes Revision 2 (7 October 2026).

> Revisions 1 (`SilvaHefferon_ProjectBrief.pdf`) and 2 (`Map Website_Sprint 1_PRD 2.md`)
> are unchanged on disk as the record of earlier plans. Where they disagree with
> this document, this one is current.

---

## What changed since Revision 2

Two features are added to this sprint. A run to a chosen restaurant and
restroom-friendly routes turn the two separate halves of the app into one
product. The rest of this revision records what has been built or decided
since 7 October.

| Area | Revision 2 | Now |
| --- | --- | --- |
| **Run to a restaurant** | Out of scope ("restaurants along a route") | **Added:** start → chosen restaurant, at a chosen distance and hilliness |
| **Restroom-friendly routes** | Bathroom finder deferred to "later" | **Added:** routes can be chosen to keep restrooms close |
| Public restroom finder | Out of scope, later | **Built:** 117 labelled public toilets on the map |
| Route generation | UI against a stub | **Built:** real streets, typically within ~1–8% of the requested distance |
| Elevation | Open question: input or display? | **Decided: input.** Elevation grid built; flattest / hilliest control not yet built |
| Restaurant data | Google Places + Yelp, both paid | Free OpenStreetMap by default; Google and Yelp optional |
| Recognition data | 13 placeholders; owner unassigned | 230 entries on Pedro's branch, none placeholders |
| Cost | Needs a billing account | No card needed for the default setup |

New risks this revision introduces:

- **Restroom opening hours are mostly unknown.** OpenStreetMap has hours for
  only 8 of the 117 toilets.
- **Choosing between more route candidates uses more of the shared routing
  quota.** Both new features need that choice.

---

## The idea and the user

Unchanged. Map Website is an interactive web map for "foodie runners", people
new to a city who care about both their run and their next meal. It brings
running routes, public restrooms and authentic local restaurants together on
one map. The two new features are what make those three things work together
rather than sit side by side.

---

## Scope for this sprint

### Must-haves

| # | Feature | State |
| --- | --- | --- |
| 1 | Route generator: adjustable distance, start point, loop or out-and-back | **Built** (PR #2) |
| 2 | Elevation as an input: flattest / hilliest | Data built (PR #2); control not yet |
| 3 | Restaurant finder, filtered by price and "recognised" picks | **Built** (PR #1) |
| 4 | Restaurant quality from established sources, not star ratings alone | **Built** (PR #1) |
| 5 | Public restroom finder | **Built** (PR #2), moved in from "later" |
| 6 | **Run to a restaurant** | **New**, planned for the week of 19 October |
| 7 | **Restroom-friendly routes** | **New** |

### Out of scope

- **Recommending restaurants along a route.** Feature 6 runs *to one
  restaurant the runner picks*. Suggesting restaurants the route happens to
  pass is still out.
- Live navigation or GPS tracking during the run. *(new)*
- User accounts; saving or sharing routes.
- User-submitted reviews.
- Mobile app (stretch goal only). The page already works on a phone-sized
  screen.

---

## New feature: run to a restaurant

**The runner's story:** *"I'm here, I want to eat there, and I want about 8 km
of running between the two, mostly flat."*

**Inputs:** start point, a restaurant chosen from the Restaurants tab or the
map, desired distance, and hilliness (flattest / balanced / hilliest).

**Behaviour:**

- **One way.** The route ends at the restaurant door.
- **Distance has a floor.** A run can't be shorter than the most direct
  walkable route to the restaurant. If the runner asks for less, the app says
  so and offers the direct route, with its real distance. It doesn't quietly
  return a longer run.
- **Longer runs detour.** When the runner asks for more than the direct
  distance, the route passes through intermediate waypoints chosen to add the
  difference. Several candidates are generated. The app keeps those near the
  requested distance and then picks by hilliness.
- **Hilliness is relative.** It means the flattest or hilliest of the
  candidates actually found, with the climb shown as a number. If the
  candidates barely differ, the app says routes here are similar.
- **Shows arrival.** Distance, climb, and estimated arrival time at the
  runner's pace.

**Depends on:**

- **Both PRs merged.** It uses the restaurant results (PR #1) and the routing
  and elevation code (PR #2).
- **A link between the two features.** "Run here" on a restaurant has to hand
  that restaurant to the Routes tab. That's a change to the shared map
  contract in `shared/`, to agree with Pedro.
- **Defined map clicks.** Today *every* map click moves the route's start
  point, including clicks on restaurant pins. Pedro flagged this in PR #1 and
  suggested acting on clicks only while the Routes tab is open. This feature
  needs click behaviour designed anyway: a click on a restaurant chooses the
  destination, and a click elsewhere chooses the start.

**Known limits:**

- **Hilliness has less room to work than on a loop.** A loop can head
  anywhere to find hills; a run to a fixed destination can only detour
  roughly on the way. On flat filled land — the Boston Common area showed only
  33–52 m of climb across four loops — the choice may be small.
- **Detours can look odd.** Waypoints can produce spurs or small loops. What
  counts as an acceptable route shape is an open question.

---

## New feature: restroom-friendly routes

**The runner's story:** *"I don't want to be 4 km from a restroom when I need
one."*

**Applies to** loops, out-and-backs and runs to a restaurant. It works as an
optional preference on top of distance and hilliness.

**Proposed definition**, to confirm:

- A restroom **serves** the route if it's within about **150 m** of it — a
  short detour on foot.
- **Main measure:** the **longest stretch of the route without a restroom**.
  That's what matters mid-run, more than the total count.
- **Second measure:** how many restrooms the route passes.
- Candidate routes are ranked by these measures. The chosen route shows its
  restrooms highlighted, and the longest gap as a figure ("never more than
  1.8 km from a restroom").

**Data:** the 117 labelled public toilets already on the map. They come from
OpenStreetMap; airports are excluded, and toilets tagged private are left out.

**Risks:**

- **Opening hours are unknown for 93% of them.** Only 8 of the 117 have
  hours in OpenStreetMap, and park restrooms often close at dusk or for the
  season. A restroom-friendly route could lead past restrooms that are shut.
  The app should show hours where they're known, say "hours unknown"
  otherwise, and never promise a restroom is open.
- **Coverage is uneven.** Restrooms are dense downtown and sparse elsewhere.
  Optimising can only choose among the routes found; in some neighbourhoods
  every route has long gaps.
- **Customers-only restrooms** (2 today) shouldn't count towards the route
  score, or at least should count for less.
- **Preferences can conflict.** The flattest route and the route with the
  most restrooms may be different routes. A priority order is needed; the
  proposal is: requested distance first (within tolerance), then restrooms if
  chosen, then hilliness.

---

## Decisions made since Revision 2

**Elevation is an input, taken from USGS rather than the routing engine.**
OpenRouteService returns elevation, but measured against USGS lidar it read
about 8 m high in Boston, with a noisy profile. Of four loops from Boston
Common it called the third-flattest the flattest. A 50 m grid of USGS heights
now picks the true flattest at both test start points, with heights within
0.5 m on average. Known limitation: routes over bridges across the Mass Pike,
I-93 or the Orange Line can read high.

**Route generation does more than the routing engine provides.** Every route
avoids ferries, which ORS's walking profile otherwise allows: one "5 km" loop
near the Seaport came back as 46 km, 36 of them by boat. Loops are chosen
from several candidates; out-and-backs aim, correct their length, and try
another direction when one is blocked by water. Test routes landed within
about 1–8% of the requested distance.

**One routing key serves every visitor.** Visitors never need a key. Rate
limits protect the shared quota, both overall and per visitor, and
openrouteservice is credited on the map as its terms require.

**Restroom labels.** Each restroom is labelled with its own name if it has a
real one. Otherwise it gets its nearest intersection ("Street A / Street B"),
or the place it's in when the nearest corner is over 300 m away. Brand names
like "City Toilet" don't count as names. Airport restrooms are excluded:
Logan's terminals held 48 of the 165 found, none reachable on a run.

**Restaurants default to free OpenStreetMap data,** with Google Places and
Yelp as optional extras (per `.env.example` on `main`, 13 October). PR #1's
description still describes Google and Yelp with a demo fallback — worth
confirming which is current.

---

## Restaurant quality: the scoring model

Unchanged from Revision 2. Weights live in `restaurants/scoring.py`, and
`restaurants/README.md` has the full model.

| Ingredient | Points |
| --- | --- |
| Michelin star / Bib Gourmand / Guide | 10 / 8 / 6 |
| Eater 38 / Eater Heatmap | 8 / 4 |
| City or tourism-board food guide | 4 each, max 2 |
| TheFork, Zomato | 2 |
| Local food creator list | 2 each, max 3 |
| Each extra independent publisher agreeing | +2 |
| Google rating, review-adjusted, above 4.4 | up to 3 |
| Yelp rating, review-adjusted, above 4.0 | up to 3 |

Ratings can't outrank critics: Google and Yelp together cap at 6, below a
single Michelin star.

---

## Cost and dependencies

- **No card is needed for the default setup.** Restaurants run on
  OpenStreetMap, and routing uses OpenRouteService's free tier. Google Places
  and Yelp are optional; Google needs a billing account if it's used.
- **The routing quota is shared by every visitor:** 2,000 requests a day and
  40 a minute on the free plan, guarded by rate limits. At up to 6 requests
  per route, that's ~330 routes a day. **Both new features need more
  candidate routes to choose from, so they raise the cost per route.**
  Applying for HeiGIT's free academic plan (10,000 a day, using an MIT email)
  is the cheapest fix.
- USGS elevation data and OpenStreetMap's Overpass service are free.
  Overpass is often slow, so restroom data is built ahead of time rather than
  fetched live.

---

## Curation

Revision 2 called the recognition data the most schedule-critical gap: 13
placeholders and no owner. **Pedro's branch now holds 230 entries in
`data/recognition/boston.json`, none of them placeholders** (checked 13
October). PR #1's description still says "placeholders only" and predates
that.

Still to confirm before the expert review: each entry is copied from its
published source with a link. Entries must not be written from memory.

---

## How it is built

- **One map, owned by `shared/`.** Features add layers through a contract
  (`view.addLayer`), with namespaced ids. Layers can now declare a credit line
  (`attribution`) that shows on the map.
- **One local server** (`python server/app.py`, standard library only). It
  serves the site and each feature's API. API keys live in `.env` and are read
  only on the server — the repo is public.
- **Features plug in without editing shared files:** `<feature>/api.py` adds
  endpoints, and `<feature>/layers.js` adds map layers and controls.
- **The new features are the first to cross between the two halves.** Run to
  a restaurant needs a shared hook from Restaurants to Routes; that's the one
  intended exception to keeping features separate.

---

## Success criteria

1. **Restaurants.** A local expert reviews the recommendation list. Success is
   approval of **at least 90%** of it.
2. **Routes.** A test runner runs a generated route and confirms it works as
   planned.
3. **Run to a restaurant.** *(new)* A test runner starts at a chosen point and
   reaches a chosen restaurant on a generated route. Proposed: the route is
   within 10% of the requested distance, with no impassable sections.
4. **Restroom-friendly routes.** *(new)* On a restroom-friendly route, the
   test runner checks that each restroom the app shows exists and is usable.
   Proposed: none missing, and any closed one was marked "hours unknown".

**Still open:** "90% of the list" has no list length. The expert and the test
runner are still to be recruited.

---

## Open questions and risks

| # | Question | Status |
| --- | --- | --- |
| 1 | A permanent name to replace "Map Website" | Open |
| 2 | Recruit the local food expert | Open |
| 3 | Recruit the test runner | Open |
| 4 | Is elevation an input or a display? | **Closed — input** |
| 5 | Who enters the recognition data, and by when? | **Largely resolved** — 230 entries on Pedro's branch; sources to verify |
| 6 | Monthly API spend cap | **Mostly moot** — no card needed by default |
| 7 | How many results does the expert review? | Open |
| 8 | Boston vs. global | Closed — global code, Boston data |
| 9 | Restroom data coverage | Closed — coverage fine; attributes thin |
| 10 | When distance, hilliness and restrooms conflict, which wins? | **New** — proposed: distance, then restrooms, then hilliness |
| 11 | How close must a restroom be to count? | **New** — proposed 150 m |
| 12 | How to present restroom hours, unknown for 93% | **New** |
| 13 | Which detour shapes are acceptable on a run to a restaurant? | **New** |
| 14 | What does a map click do once both features share the map? | **New** — Pedro's suggestion in PR #1 is a start |
| 15 | Pedro's agreement to the cross-feature scope change | **New** |
| 16 | Routing quota under candidate-heavy features | **New** — academic plan, route caching |

---

## Status at this revision

| | State |
| --- | --- |
| PR #2 — routes | Open, awaiting review: street routing, elevation grid, rate limits, attribution, 117 labelled restrooms. 76 tests |
| PR #1 — restaurants | Open, awaiting review: ranked recommendations, filters, map pins. 38 tests per its description |

**Next:**

1. Review and merge both PRs. They merge cleanly together.
2. Agree the cross-feature hook and the map-click behaviour with Pedro.
3. Build the run to a restaurant (week of 19 October), then restroom-friendly
   routes. Both share the candidate-and-score approach planned for the
   flattest / hilliest control, so build that first or alongside.
