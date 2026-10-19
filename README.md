# Map Website

One interactive map for foodie runners new to a city: running routes, public
bathrooms, and authentic local restaurants.

Current spec: **[Map Website_Sprint 1_PRD 3.md](Map%20Website_Sprint%201_PRD%203.md)**.
Earlier revisions are kept as the record: `SilvaHefferon_ProjectBrief.pdf`
(revision 1) and `Map Website_Sprint 1_PRD 2.md` (revision 2). Where they
disagree, revision 3 is current.

## Layout

| Folder         | What                                         | Owner | Branch        |
| -------------- | -------------------------------------------- | ----- | ------------- |
| `restaurants/` | Restaurant finder (price, local picks, quality) | Pedro | `restaurants` |
| `routes/`      | Running route generator, later bathrooms     | Rob   | `routes`      |
| `shared/`      | The map page and anything both features use  | both  | via `main`    |
| `server/`      | Local server: static files, feature APIs, `.env`, cache | both | via `main` |
| `spikes/`      | Early data-source experiments, kept for reference | both | `main`  |

## Running it

On Windows, double-click **`start.bat`**: it starts the server and opens the
site in your browser. Close its window to stop the server.

Or from a terminal:

```
python server/app.py
```

Then open http://localhost:8000/ - not the HTML file itself, which cannot load
its scripts or data without the server. Python 3.9+, standard library only - nothing
to install. The server serves the site and each feature's API, and reads API
keys from `.env` (copy `.env.example`; `.env` is gitignored), so keys never
reach the browser. See `server/router.py` for how a feature adds endpoints.

Restaurant setup, ranking and data: [`restaurants/README.md`](restaurants/README.md).

## Deploying to Vercel

`vercel.json` serves the static folders directly and sends every `/api/*`
request to `api/index.py`, which runs the same feature handlers as the local
server. `.vercelignore` keeps `.env` from being uploaded.

1. Run a restaurant search locally, then `python -m server.seed_cache` and
   commit `data/cache-seed/`. A fresh serverless instance starts with an empty
   cache, and fetching the city's restaurants from Overpass takes longer than
   Vercel allows a request.
2. In the Vercel project, set `ORS_API_KEY` (Settings → Environment Variables).
3. `npx vercel --prod`.

Limits on Vercel: rate limits are counted per serverless instance, not
site-wide, so the shared routing quota is guarded less tightly than locally.

## Workflow

1. Work on your own branch, inside your own folder. Push often.
2. Changes to `shared/` go in small commits; tell the other person.
3. When something works, open a pull request into `main`.
4. Pull `main` into your branch regularly: `git merge origin/main`.
