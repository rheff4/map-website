# Restaurants

The Restaurants tab recommends restaurants by **how many trusted sources vouch
for them**, not by star ratings alone, and shows which sources those are.

Owner: Pedro · Branch: `restaurants`

## Running it

```
python server/app.py
```

Open http://localhost:8000/. The Restaurants tab is open by default.

Without API keys it runs in **demo mode**: fictional restaurants and
placeholder awards, served from `restaurants/fixtures/`, with a yellow banner
saying so. That is enough to work on the UI and the ranking without spending
anything.

Tests (no network, no keys):

```
python -m unittest discover -s restaurants/tests -t .
```

## Setting up API keys

1. Copy `.env.example` to `.env` in the repo root. `.env` is gitignored; never
   commit a key - the repo is public.
2. **Google Places API (New)**: in Google Cloud Console create a project,
   enable *Places API (New)*, attach a billing account, create an API key and
   restrict it to that API. Set `GOOGLE_PLACES_API_KEY=...`.
3. **Yelp** (optional): create an app at
   https://www.yelp.com/developers/v3/manage_app and set `YELP_API_KEY=...`.
   Yelp's API is paid after a free trial.
4. Restart the server. The banner disappears; the startup log lists which keys
   were read.

Either key alone works - the missing source is skipped and the page says so.
`RESTAURANTS_MOCK=1` forces demo mode even with keys.

**Cost control.** Keys are only read by the server. Every Google and Yelp
response is cached in `.cache/` for `CACHE_TTL_HOURS` (default 7 days; capped
at 30 days, the limit Google's terms allow for most Places content). Searches
round the point to ~100 m so nearby searches share a cache entry. Changing
filters never calls an API - only moving the search point or the distance
does. A cold search costs up to 3 Google requests and 2 Yelp requests.

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

**Right now it contains only placeholders** for fictional restaurants (used by
demo mode, ignored with real keys). With real keys and no real entries,
nothing counts as recognised and the page tells you so.

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

3. Run the enrichment tool. It looks each new entry up on Google once and
   writes back its place id and coordinates, so matching is exact:

   ```
   python -m restaurants.tools.enrich_recognition --dry-run
   python -m restaurants.tools.enrich_recognition
   ```

   Check every line marked `CHECK` (Google's name differs from yours) and
   fix any `?` lines by hand by adding `lat` and `lon`.

4. Delete the placeholder entries once you have real ones.

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
| `sources/google.py`, `sources/yelp.py` | API clients, cached |
| `cuisines.py` | One cuisine vocabulary for Google and Yelp |
| `hours.py` | "Open now" from weekly hours |
| `tools/enrich_recognition.py` | Adds place ids and coordinates to entries |
| `fixtures/` | Demo-mode API responses (fictional) |
| `tests/` | Unit and end-to-end tests |
| `../data/recognition/<city>.json` | Curated recognition per city |
| `../data/chains.json` | Chain blocklist |

## Known limits

- Google returns at most 60 places per search and Yelp 100, so at 3-5 km
  unrecognised places are a sample, not everything. Recognised places are
  always included.
- Yelp search has no opening hours: a Yelp-only place is hidden by "Open now".
- Price comes from Google, else Yelp; places with neither are hidden when a
  price filter is on.
