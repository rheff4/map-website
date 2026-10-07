# Map Website

One interactive map for foodie runners new to a city: running routes, public
bathrooms, and authentic local restaurants.

Current spec: **[Map Website_Sprint 1_PRD 2.md](Map%20Website_Sprint%201_PRD%202.md)**.
`SilvaHefferon_ProjectBrief.pdf` is revision 1, kept as the original record;
where the two disagree, the PRD is current.

## Layout

| Folder         | What                                         | Owner | Branch        |
| -------------- | -------------------------------------------- | ----- | ------------- |
| `restaurants/` | Restaurant finder (price, local picks, quality) | Pedro | `restaurants` |
| `routes/`      | Running route generator, later bathrooms     | Rob   | `routes`      |
| `shared/`      | The map page and anything both features use  | both  | via `main`    |
| `server/`      | Local server: static files, feature APIs, `.env`, cache | both | via `main` |
| `spikes/`      | Early data-source experiments, kept for reference | both | `main`  |

## Running it

```
python server/app.py
```

Then open http://localhost:8000/. Python 3.9+, standard library only - nothing
to install. The server serves the site and each feature's API, and reads API
keys from `.env` (copy `.env.example`; `.env` is gitignored), so keys never
reach the browser. See `server/router.py` for how a feature adds endpoints.

## Workflow

1. Work on your own branch, inside your own folder. Push often.
2. Changes to `shared/` go in small commits; tell the other person.
3. When something works, open a pull request into `main`.
4. Pull `main` into your branch regularly: `git merge origin/main`.
