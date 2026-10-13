# Restaurants

The Restaurants tab recommends restaurants by **how many trusted sources vouch
for them**, not by star ratings alone, and shows which sources those are.

Owner: Pedro · Branch: `restaurants`

## Running it

```
python server/app.py
```

Open http://localhost:8000/ (or double-click `start.bat`). The Restaurants tab
is open by default; choose where and what, then press **Search**.

**Real data, free, no setup.** Restaurant listings come from
[OpenStreetMap](https://www.openstreetmap.org/) (via the free Overpass API) -
no account, key or card. Recognition comes from `data/recognition/boston.json`.

**Demo mode** (`RESTAURANTS_MOCK=1` in `.env`) uses fictional restaurants and
placeholder awards from `restaurants/fixtures/`, with a yellow banner, and
never touches the network. The tests always run in demo mode:

```
python -m unittest discover -s restaurants/tests -t .
```

## Data sources

| Source | Cost | Gives | Missing |
| --- | --- | --- | --- |
| **OpenStreetMap** (default) | Free | Name, location, cuisine, website, opening hours for about a third of places | Ratings; price almost never |
| Curated recognition file | Free | Michelin, Eater, James Beard, Best of Boston, Meet Boston; Michelin's price and cuisine | Only covers listed places |
| City of Boston licences ([data.boston.gov](https://data.boston.gov/dataset/active-food-establishment-licenses)) | Free | Green "Licensed" label when a place matches an active food licence | Boston city limits only; a miss proves nothing, so it never hides a place |
| Google Places (optional) | Card needed; free under ~1,000 requests/month | Ratings, price, hours for most places | - |
| Yelp (optional) | Paid after trial | Second rating source | Hours |

Without Google or Yelp, ranking is driven by recognition alone (it dominates
anyway - see below), non-recognised places are sorted by distance, the price
filter works for the Michelin-listed places, and "open now" works wherever OSM
or Google has hours. Places with unknown hours are hidden by "open now".

OpenStreetMap's Overpass servers are run by volunteers and are often busy, so
searches don't call them: one request downloads every named food place in the
city (~2,700, cached in `.cache/`), and each search filters that copy locally
in about a second. A copy older than a day is still used while a fresh one
downloads in the background; only the very first run waits for it.

**Adding Google or Yelp later** - put a key in `.env` and restart:

1. `.env` lives in the repo root (copy `.env.example`); it is gitignored -
   never commit a key, the repo is public.
2. **Google Places API (New)**: in Google Cloud Console create a project,
   attach billing, enable *Places API (New)*, create an API key restricted to
   that API, set its daily quota to ~30 requests (30 x 31 stays inside the
   free 1,000/month) and add a $1 budget alert. Set `GOOGLE_PLACES_API_KEY=...`.
3. **Yelp**: create an app at https://www.yelp.com/developers/v3/manage_app
   and set `YELP_API_KEY=...`.

Google and Yelp results are combined with OpenStreetMap's: the same
restaurant from several sources becomes one result. Nothing is fetched until
you press **Search**, and pressing it again after changing only price, cuisine
or the checkboxes re-filters what is already loaded - only a new search point
or distance makes requests. Searches round the point to ~100 m so nearby
searches share a cache entry.

## How ranking works

All of it lives in `restaurants/scoring.py`, with every weight as a named
constant at the top. Edit and restart the server; the tests in
`restaurants/tests/test_scoring.py` say what must stay true.

| Ingredient | Points (default) |
| --- | --- |
| Michelin star / Bib Gourmand / Guide | 10 / 8 / 6 |
| Eater 38 / Eater Heatmap | 8 / 4 |
| City or tourism-board food guide | 4 each, up to 2 guides |
| TheFork, Zomato | 2 |
| Local food creator list | 2 each, up to 3 lists |
| Each extra independent publisher agreeing | +2 |
| Google rating, review-adjusted, above 4.4 | up to 3 |
| Yelp rating, review-adjusted, above 4.0 | up to 3 |

- **One publisher counts once.** A Michelin star and a Michelin "Guide"
  mention don't both score.
- **Ratings are adjusted for review count** (Bayesian average): 4.9 from 14
  reviews scores nothing, 4.6 from 2,000 scores well. Under 50 reviews never
  scores.
- **Ratings can't outrank critics**: both platforms together max out at 6,
  below a single Michelin star or Eater 38.
- **Chains** in `data/chains.json` are removed. Edit that list freely.
- **"Recognised"** = at least one curated source. The tab shows only
  recognised places by default; untick the box to see everything ranked.

Each result's breakdown ("Local score 22.8" under a result) shows every point
it earned and why others earned nothing. Use it with your food expert: when
they disagree with a ranking, the breakdown says which weight to change.

## Adding recognition entries

Michelin, Eater, TheFork, Zomato, city guides and creators have no usable
public API, so recognition is a hand-curated file per city:
`data/recognition/boston.json`.

**What's in it now** - 230 entries for 180 restaurants, each copied from its
source page (checked 2026-10-13) and linking to it:

| Source | Entries | Weight | Notes |
| --- | --- | --- | --- |
| MICHELIN Guide Boston | 24 | 10 / 8 / 6 | 1 star (311 Omakase), 6 Bib Gourmand, 17 recommended |
| Eater 38 (Oct 8 2026) | 37 | 8 | Rubato's printed town (South Walpole) corrected to Quincy |
| James Beard Awards 2024-26 | 22 | 7 / 4 | 5 finalists, rest semifinalists; no MA winners those years; bar/drinks awards and closed Chickadee left out |
| Best of Boston 2026 | 48 | 4 / 2 | 39 restaurant + 9 dish awards; novelty categories left out |
| Meet Boston | 99 | 2, supporting | 8 selective "best of" articles; **counts only alongside another source** because its guides mostly feature paying members |

Left out because they are outside the city bounds: Thistle & Leek (Newton
Centre), Nightshade Noodle Bar (Lynn), Thaiger Den (Malden), and a few
suburban Best of Boston / James Beard picks.

Each entry has been located with the enrichment tool (step 3). Michelin
entries also carry Michelin's price and cuisine, which fill in for map data.

Demo mode does not use this file: its fictional entries live in
`restaurants/fixtures/recognition_demo.json`.

1. Open the source - e.g. the current Eater 38 Boston article - and for each
   restaurant on it add an entry:

   ```json
   {
     "name": "Restaurant Name",
     "address": "123 Street, Boston, MA",
     "source": "eater_38",
     "source_url": "https://link-to-the-list-or-article",
     "note": "Eater 38, updated Sept 2026"
   }
   ```

   One entry per source per restaurant: a place on Eater 38 *and* the Michelin
   Guide has two entries.

2. `source` must be one of:

   | id | For |
   | --- | --- |
   | `michelin_star` | Michelin star (any number) |
   | `michelin_bib_gourmand` | Michelin Bib Gourmand |
   | `michelin_selected` | Michelin Guide, recommended without a distinction |
   | `eater_38` | Eater Boston's Eater 38 |
   | `eater_heatmap` | Eater Boston heatmaps / other Eater lists |
   | `city_guide` | City or tourism-board food guides (e.g. Meet Boston) |
   | `thefork` | TheFork awards or features |
   | `zomato` | Zomato collections |
   | `influencer` | A local food creator's list or video |

   To add a new kind of source, add it to `SOURCES` in `scoring.py` first.

3. Run the enrichment tool. It looks each new entry up once and writes back
   its location, so matching is exact. With no Google key it uses
   OpenStreetMap: it finds the address with Nominatim, then snaps the entry to
   the OSM restaurant with a matching name within 150 m (`osm_id`). With a
   Google key it uses Google instead (`place_id`).

   ```
   python -m restaurants.tools.enrich_recognition --dry-run
   python -m restaurants.tools.enrich_recognition
   ```

   Check every line marked `CHECK` - the matched name differs from yours, or
   no mapped restaurant of that name was found and the entry was placed at its
   address (still fine for ranking; it just has no OSM hours or cuisine). Fix
   any `?` lines by hand by adding `lat` and `lon`.

4. Restart the server (close `start.bat`'s window and open it again).

Rules that keep the success test honest:
- **Copy, don't guess.** Only add what the source actually published, and
  link to it in `source_url`. Never add an award from memory.
- **Write entries by hand**, don't scrape - most of these sites' terms forbid
  it.
- Lists go stale. Put the edition or date in `note`, and re-check yearly.

Entries are matched to Google/Yelp results by place id, or else by name plus
distance (within 100 m). A recognised restaurant that the searches didn't
return is still added, so curated places always appear in range.

## Adding a city

1. Add an entry to `shared/cities.json`: name, centre `[lon, lat]`, a label
   for the centre, zoom, bounds `[[west, south], [east, north]]`, and a
   recognition file path.
2. Create that recognition file (copy the shape of `boston.json`) and fill it
   in as above.
3. The API takes `?city=<key>`. The page uses the default city today; a city
   picker is the remaining UI work.

## Files

| File | What |
| --- | --- |
| `layers.js`, `restaurants.css` | The tab: panel, filters, list, map layers |
| `api.py` | `GET /api/restaurants/config`, `/api/restaurants/search` |
| `service.py` | The pipeline: fetch → merge → recognise → exclude → score → rank |
| `scoring.py` | **Weights and thresholds.** Tune here |
| `matching.py` | When two records are the same restaurant |
| `recognition.py` | Loads and validates the curated file |
| `sources/osm.py` | OpenStreetMap: Overpass listings, Nominatim geocoding |
| `sources/google.py`, `sources/yelp.py` | Optional API clients, cached |
| `cuisines.py` | One cuisine vocabulary for Google and Yelp |
| `hours.py` | "Open now" from weekly hours |
| `tools/enrich_recognition.py` | Adds place ids and coordinates to entries |
| `fixtures/` | Demo-mode API responses (fictional) |
| `tests/` | Unit and end-to-end tests |
| `../data/recognition/<city>.json` | Curated recognition per city |
| `../data/chains.json` | Chain blocklist |

## Known limits

- Without Google/Yelp there are no ratings, so non-recognised places are not
  ranked by quality - they are hidden by default for that reason.
- Google returns at most 60 places per search and Yelp 100, so at 3-5 km
  unrecognised places are a sample, not everything. Recognised places are
  always included.
- Yelp search has no opening hours: a Yelp-only place is hidden by "Open now".
- Price comes from Google, else Yelp; places with neither are hidden when a
  price filter is on.
