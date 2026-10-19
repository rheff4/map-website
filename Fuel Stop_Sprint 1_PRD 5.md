# Fuel Stop — Sprint 1 PRD, Revision 5: Launch

**Pedro Silva · Rob Hefferon** · AI Builder Space Proseminar
Revision 5, 19 October 2026. Supersedes Revision 4 (19 October 2026).

> **Fuel Stop launched on 19 October 2026** at
> **https://map-website-snowy.vercel.app**.
>
> Revisions 1 (`SilvaHefferon_ProjectBrief.pdf`), 2 and 3
> (`Map Website_Sprint 1_PRD 2.md`, `… 3.md`) and 4
> (`Fuel Stop_Sprint 1_PRD 4.md`) are unchanged on disk as the record of
> earlier plans. Where they disagree with this document, this one is current.

---

## What changed since Revision 4

| Area | Revision 4 | Now |
| --- | --- | --- |
| **Status** | Live, still building | **Launched** |
| **Restroom-friendly routes** | Must-have, not built | **Deprioritized for time;** no longer a must-have |
| **Local specialties** | — | **Built** (PR #4): restaurants known for a Boston signature dish are labelled |
| **Brand** | Colour logo | Plus an **all-white** version; the browser-tab icon uses it |

Every remaining must-have is built.

---

## The idea and the user

Fuel Stop is an interactive web map for "foodie runners", people new to a city
who care about both their run and their next meal. It brings running routes,
public restrooms and authentic local restaurants together on one map, and runs
from one to the other.

---

## Scope for this sprint

### Must-haves: all built

| # | Feature | State |
| --- | --- | --- |
| 1 | Route generator: adjustable distance, start point, loop or run to a point | **Built** |
| 2 | Elevation as an input: flattest / balanced / hilliest | **Built** |
| 3 | Restaurant finder, filtered by price and "recognised" picks | **Built**, prices in dollars |
| 4 | Restaurant quality from established sources, not star ratings alone | **Built**, plus local specialties |
| 5 | Public restroom finder | **Built** (117 restrooms) |
| 6 | Run to a restaurant | **Built** |

### Deprioritized: restroom-friendly routes

Restroom-friendly routes were a must-have in Revisions 3 and 4. **We
deprioritized them for time** to launch this sprint. Restrooms are still on
the map (feature 5); routes just don't choose themselves by them yet.

The design stays on record in Revisions 3 and 4, for whoever picks it up:
- A restroom serves the route within about 150 m.
- Rank routes by the longest stretch without one, then by how many they pass.
- Never promise a restroom is open. OpenStreetMap has opening hours for only
  8 of the 117.

The code now has a natural place for it: hilliness already ranks candidate
routes near the requested distance, and restrooms would rank them first.

### Out of scope

Recommending restaurants *along* a route, live navigation or GPS tracking,
user accounts and saved routes, user-submitted reviews, and a mobile app (the
page works on a phone-sized screen).

---

## What launched

### Routes

- **Loop or run to a point.** *Loop* starts and ends at the start point. *To a
  point* runs one way to a destination picked on the map, or to a restaurant
  via **Run here** in the Restaurants tab. Out-and-back left the page in
  Revision 4; the server still supports it.
- **Distance within 500 m** at every length, not a percentage, so a 20 km run
  can't drift 2 km. The results panel shows the difference in metres, and
  says so when no route lands within 500 m.
- **Flattest / balanced / hilliest** choose among the candidates within 500 m
  by their climb on a USGS elevation grid. Distance comes first. The page says
  when routes here barely differ.
- **Runs to a point loop round** through an arc of waypoints, not out and
  back along a hairpin. Within 500 m of the direct route, the direct route is
  the run. More than 500 m short of it, the page warns that the point is too
  far for that distance.
- **Results:** distance, estimated time, arrival time for runs to a point,
  and climb.
- **Map clicks** move the start only on the Routes tab, set the destination
  while picking, and never act on restaurant pins.

### Restaurants

- **Ranked recommendations** from recognised local sources. Scoring is below.
- **Prices in dollars**, e.g. `$$$ ($50–75)`, from the same source as the
  level: Google's range, Yelp's band or Michelin's band (PR #3).
- **Local specialties** (PR #4). A restaurant gets a "Local specialty:
  Lobster roll" tag when what's written about it says it is *known for* one of
  Boston's 14 signature dishes, not merely that it serves one.
  - 19 restaurants are labelled, e.g. Neptune Oyster, Saltie Girl and Union
    Oyster House.
  - Each label keeps a word-for-word quote from its source. The tag shows the
    quote on hover and links to the source.
  - Labels were made offline with Claude and checked by hand. The site itself
    never calls Claude.
  - A specialty adds 3 points but never makes a place "recognised" on its own,
    because a famous lobster spot can still be a tourist trap.

### Restrooms

117 public restrooms in Boston, each labelled with its name or its nearest
intersection. Airport restrooms are excluded.

### Brand

| Colour | Hex | Used for |
| --- | --- | --- |
| Green | `#1b4436` | Header text, tabs, buttons, the route line |
| Orange | `#e8743f` | Recognised restaurants, run destinations |
| Cream | `#f8f6f1` | Page and panel backgrounds |

The logo, an orange runner's dot and the green path that becomes a fork, is in
`shared/brand/` as transparent SVG:
- colour, with and without the name: `logo.svg`, `mark.svg`
- all white: `logo-white.svg`, `mark-white.svg`

The header uses the colour mark; only the browser-tab icon uses the white one.

---

## Restaurant quality: the scoring model

Weights live in `restaurants/scoring.py`, and `restaurants/README.md` has the
full model.

| Ingredient | Points |
| --- | --- |
| Michelin star / Bib Gourmand / Guide | 10 / 8 / 6 |
| Eater 38 / Eater Heatmap | 8 / 4 |
| City or tourism-board food guide | 4 each, max 2 |
| TheFork, Zomato | 2 |
| Local food creator list | 2 each, max 3 |
| Each extra independent publisher agreeing | +2 |
| **Local specialty** *(new)* | **3**, once per restaurant |
| Google rating, review-adjusted, above 4.4 | up to 3 |
| Yelp rating, review-adjusted, above 4.0 | up to 3 |

Ratings can't outrank critics: Google and Yelp together cap at 6, below a
single Michelin star.

---

## How it is built

- **One map, owned by `shared/`.** Features add layers through `view.addLayer`.
  They talk to each other only through `view.emit` / `view.on`. The one
  message so far is `restaurants:run-to`, sent by **Run here**.
- **One small Python server**, standard library only. It runs locally as
  `python server/app.py` and on Vercel through `api/index.py`. Static files
  are served directly. API keys live in `.env` locally and in the Vercel
  project's settings, never in the repo.
- **Vercel** deploys from `main`. The city's restaurant data ships with the
  site (`data/cache-seed/`), because fetching it fresh takes longer than
  Vercel allows a request.
- **Tests:** 189, all passing at launch.

---

## Cost and dependencies

- **No card is needed.** Restaurants use OpenStreetMap; routing uses
  OpenRouteService's free tier; Vercel's free plan hosts the site. Local
  specialties were labelled without the paid Claude API.
- **The routing quota is the constraint.** OpenRouteService's free plan allows
  2,000 requests a day, shared by every visitor, at up to 6 per route. It ran
  out on launch day. **HeiGIT's free academic plan (10,000 a day, with an MIT
  email) is the fix.**

---

## Success criteria

Measured after launch:

1. **Restaurants.** A local expert approves **at least 90%** of the
   recommendation list.
2. **Routes.** A test runner runs a generated route and confirms it works as
   planned.
3. **Run to a restaurant.** A test runner reaches a chosen restaurant on a
   generated route within **500 m** of the requested distance, with no
   impassable sections.

Restroom-friendly routes' criterion is dropped with the feature.

**Still open:** "90% of the list" has no list length; the expert and the test
runner are still to be recruited.

---

## Known issues at launch

| Issue | Impact | Fix |
| --- | --- | --- |
| Routing quota spent on 19 October | No routes until it resets; the page says so | HeiGIT academic plan; find what spent it |
| Rate limits are counted per Vercel instance | The shared quota is guarded less tightly than on one server | A shared counter, or the academic plan's headroom |
| Vercel twice missed or stalled deploys on 19 October | Pushes reached the site only after a manual deploy | Check Settings → Git; watch the next pushes |
| White tab icon on light-theme browsers | Hard to see | A theme-aware icon, if wanted |
| 500 m tolerance on short runs | 400 m out on a 2 km run passes | "500 m or 10%, whichever is smaller" |
| Long runs to nearby places | Can land outside 500 m; the page warns | More attempts, at more quota |
| Elevation over bridges (Mass Pike, I-93) | Reads high | Follow the deck where OpenStreetMap marks a bridge |

---

## Open questions

| # | Question | Status |
| --- | --- | --- |
| 1 | A permanent name | Closed — Fuel Stop |
| 2 | Recruit the local food expert | Open |
| 3 | Recruit the test runner | Open |
| 5 | Recognition data: sources to verify | Open — each entry copied from its published source with a link |
| 7 | How many results does the expert review? | Open |
| 10 | When distance and hilliness conflict, which wins? | Closed — distance (within 500 m), then hills |
| 11, 12 | Restroom distance and hours | Deferred with restroom-friendly routes |
| 13 | Acceptable detour shapes | Closed — loops, not hairpins |
| 14 | What a map click does | Closed |
| 15 | Pedro's agreement to the cross-feature hook and colour changes | Open |
| 16 | Routing quota | Open — academic plan |
| 17 | A public link | Closed — launched 19 October |
| 18 | Tolerance on short runs | Open |
| 19 | Vercel's per-instance rate limits | Open |

---

## Status at launch

| | State |
| --- | --- |
| Live site | https://map-website-snowy.vercel.app, running `main` |
| `main` | PRs #1–#4 merged. 189 tests passing |
| Routing | Down until OpenRouteService's daily quota resets |

**After launch:**

1. Apply for HeiGIT's academic plan, and check what spent the quota.
2. Confirm with Pedro: the cross-feature hook and the colour changes in his
   files.
3. Recruit the food expert and the test runner, set the review list's length,
   and measure the success criteria.
4. Restroom-friendly routes, when there's time.
