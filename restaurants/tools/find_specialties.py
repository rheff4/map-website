"""
Label recognised restaurants with the local signature dishes they are known for.

    python -m restaurants.tools.find_specialties              # new or changed places
    python -m restaurants.tools.find_specialties --force      # relabel everything
    python -m restaurants.tools.find_specialties --only "Neptune Oyster" --dry-run

For every restaurant in the recognition file it gathers what is written about
it - the critics' and guides' pages it is listed on, and its own website from
OpenStreetMap - and asks Claude which of the city's signature dishes
(data/local_dishes/<city>.json) the text says it is *known for*. Serving a dish
is not enough. Every label must come with a word-for-word quote from the
text; labels whose quote is not found in the text are thrown away.

Writes data/specialties/<city>.json, which searches read. Only places whose
text changed are sent to Claude again, so reruns are cheap.

Needs ANTHROPIC_API_KEY (in .env or the environment) and the Anthropic SDK,
which the site itself does not use:

    pip install -r restaurants/tools/requirements.txt

Pages are fetched politely: robots.txt is obeyed (Michelin and Boston Magazine
disallow crawlers, so their pages are skipped), each page is downloaded once
and cached in .cache/ for a week, and only a short quote is stored.
"""

import argparse
import datetime
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from restaurants import matching, recognition, scoring, specialties  # noqa: E402
from restaurants.sources import osm  # noqa: E402
from server import cache, cities, env  # noqa: E402
from server.fetch import USER_AGENT  # noqa: E402

MODEL = "claude-opus-5-5"
PAGE_TTL_S = 7 * 24 * 3600
PAGE_PAUSE_S = 1.0            # between downloads from the same run
WEBSITE_CHARS = 4000          # of a restaurant's own page
MAX_QUOTE_WORDS = 30
GROUP_RADIUS_M = 150

SYSTEM = (
    "You label restaurants with the local signature dishes they are known for, "
    "using only the text you are given. You are careful: a label shown to the "
    "public must be backed by the text, word for word."
)

INSTRUCTIONS = """Restaurant: {name}

The city's signature dishes (key: label - other names):
{dishes}

Below is everything we found written about this restaurant, numbered by source.

Which of the signature dishes is this restaurant KNOWN FOR, according to the text?
Count a dish only when the text says the restaurant is famous or known for it, calls
it a signature, specialty, must-order or one of the best in town, or when the source
is a list of the best places for that dish (its title says so) and this restaurant
is on it. A dish that is merely on the menu, mentioned in passing, or praised only
for another restaurant on the same page does NOT count.

For each dish, give the source number and copy a short quote (at most {max_words}
words) exactly as written in that source that shows it. Return an empty list if
no dish qualifies - that is a common and correct answer.

{sources}"""


# ---------------------------------------------------------------------------
# Fetching pages
# ---------------------------------------------------------------------------

class _PageText(HTMLParser):
    """Visible text, the title, and links of an HTML page. Standard library only."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "template"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "section", "article", "br", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.title = [], [], ""
        self._skip, self._in_title, self._href, self._anchor = 0, False, None, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._href, self._anchor = dict(attrs).get("href"), []
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor).strip()))
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)
            if self._href:
                self._anchor.append(data)

    def text(self):
        return re.sub(r"[ \t]*\n\s*", "\n", re.sub(r"[ \t]+", " ", "".join(self.parts))).strip()


_robots = {}


def allowed(url):
    """robots.txt for our user agent. An unreadable robots.txt means no."""
    parts = urllib.parse.urlparse(url)
    host = "%s://%s" % (parts.scheme, parts.netloc)
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
        try:
            rp.read()
        except Exception:  # noqa: BLE001 - network or parse failure
            rp = None
        _robots[host] = rp
    rp = _robots[host]
    return rp is not None and rp.can_fetch(USER_AGENT, url)


def fetch_page(url):
    """{'url', 'title', 'text', 'links'} or {'url', 'error'}. Pages and robots
    refusals are cached for a week; failed downloads are retried next run."""
    hit = cache.get("specialty-pages", url, PAGE_TTL_S)
    if hit is not None:
        return hit
    if not allowed(url):
        page = {"url": url, "error": "robots.txt disallows crawlers"}
        cache.put("specialty-pages", url, page)
        return page

    time.sleep(PAGE_PAUSE_S)
    # Sites link to paths with spaces ("/Special Menu .pdf"); escape them.
    safe_url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")
    req = urllib.request.Request(safe_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if "html" not in resp.headers.get("Content-Type", "html"):
                return {"url": url, "error": "not an HTML page"}
            charset = resp.headers.get_content_charset() or "utf-8"
            html = resp.read(3_000_000).decode(charset, "replace")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        return {"url": url, "error": "download failed: %s" % e}
    parser = _PageText()
    parser.feed(html)
    page = {"url": url, "title": re.sub(r"\s+", " ", parser.title).strip(),
            "text": parser.text(), "links": parser.links[:400]}
    cache.put("specialty-pages", url, page)
    return page


def website_extra_page(page):
    """A menu or about page on the restaurant's own site, if the home page links one."""
    home = urllib.parse.urlparse(page["url"])
    for href, label in page.get("links", []):
        target = urllib.parse.urljoin(page["url"], href or "")
        parts = urllib.parse.urlparse(target)
        if parts.netloc != home.netloc or parts.path in ("", "/", home.path):
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|docx?)$", parts.path.lower()):
            continue
        if re.search(r"\b(menu|about|our story)\b", (label + " " + parts.path).lower()):
            return target.split("#")[0]
    return None


# ---------------------------------------------------------------------------
# Gathering text per restaurant
# ---------------------------------------------------------------------------

def group_places(entries):
    """Recognition entries grouped into restaurants: same name, within 150 m."""
    places = []
    for entry in entries:
        for place in places:
            if matching.names_match(place["name"], entry["name"]) and matching.distance_m(
                    place["lat"], place["lon"], entry["lat"], entry["lon"]) <= GROUP_RADIUS_M:
                place["entries"].append(entry)
                break
        else:
            places.append({"name": entry["name"], "lat": entry["lat"], "lon": entry["lon"],
                           "entries": [entry]})
    return places


def gather_sources(place, websites):
    """[(label, url, text)] for one restaurant, and [{'url', 'why'}] skipped."""
    sources, skipped, seen = [], [], set()

    def add(label, url, text):
        if text.strip():
            sources.append({"label": label, "url": url, "text": text.strip()})

    for entry in place["entries"]:
        url = entry.get("source_url")
        if not url or url in seen:
            continue
        seen.add(url)
        page = fetch_page(url)
        if page.get("error"):
            skipped.append({"url": url, "why": page["error"]})
            continue
        label = "%s - %s" % (scoring.SOURCES[entry["source"]]["label"], page["title"] or url)
        text = specialties.passages(place["name"], page["text"])
        if text:
            add(label, url, text)
        else:
            skipped.append({"url": url, "why": "restaurant not named on the page"})

    osm_ids = {e["osm_id"] for e in place["entries"] if e.get("osm_id")}
    for osm_id in sorted(osm_ids):
        url = websites.get(osm_id)
        if not url or url in seen:
            continue
        seen.add(url)
        page = fetch_page(url)
        if page.get("error"):
            skipped.append({"url": url, "why": page["error"]})
            continue
        add("The restaurant's own website - %s" % (page["title"] or url), url,
            page["text"][:WEBSITE_CHARS])
        extra = website_extra_page(page)
        if extra and extra not in seen:
            seen.add(extra)
            sub = fetch_page(extra)
            if not sub.get("error"):
                add("The restaurant's own website - %s" % (sub["title"] or extra), extra,
                    sub["text"][:WEBSITE_CHARS])
    return sources, skipped


def input_hash(name, sources, dishes):
    blob = json.dumps([name, MODEL, sorted(dishes), INSTRUCTIONS,
                       [(s["url"], s["text"]) for s in sources]], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Asking Claude
# ---------------------------------------------------------------------------

def schema(dish_keys, n_sources):
    return {
        "type": "object",
        "properties": {
            "known_for": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "dish": {"type": "string", "enum": sorted(dish_keys)},
                        "source": {"type": "integer", "enum": list(range(1, n_sources + 1))},
                        "quote": {"type": "string"},
                    },
                    "required": ["dish", "source", "quote"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["known_for"],
        "additionalProperties": False,
    }


def prompt(name, dishes, sources):
    dish_lines = "\n".join(
        "- %s: %s%s" % (d["key"], d["label"],
                        " - " + ", ".join(d["also_called"]) if d.get("also_called") else "")
        for d in dishes.values())
    source_blocks = "\n\n".join(
        "<source number=\"%d\" title=\"%s\">\n%s\n</source>" % (i, s["label"], s["text"])
        for i, s in enumerate(sources, 1))
    return INSTRUCTIONS.format(name=name, dishes=dish_lines, sources=source_blocks,
                               max_words=MAX_QUOTE_WORDS)


def classify(client, name, dishes, sources):
    """Claude's labels for one restaurant: [{'dish', 'source', 'quote'}]."""
    import anthropic

    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium",
                           "format": {"type": "json_schema",
                                      "schema": schema(dishes, len(sources))}},
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt(name, dishes, sources)}],
        )
    except anthropic.APIStatusError as e:
        raise RuntimeError("Claude API error %s: %s" % (e.status_code, e.message))
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Could not reach the Claude API: %s" % e)

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to label this restaurant")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("answer was cut off")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)["known_for"]


def verified(labels, sources):
    """Keep labels whose quote really is in the cited source; one per dish."""
    kept, dropped, seen = [], [], set()
    for label in labels:
        source = sources[label["source"] - 1]
        quote = re.sub(r"\s+", " ", label["quote"]).strip()
        if label["dish"] in seen:
            continue
        if not specialties.quote_in(quote, source["text"]):
            dropped.append(label)
            continue
        words = quote.split()
        if len(words) > MAX_QUOTE_WORDS:
            quote = " ".join(words[:MAX_QUOTE_WORDS]) + " …"
        seen.add(label["dish"])
        kept.append({"dish": label["dish"], "quote": quote, "source_url": source["url"],
                     "source": source["label"].split(" - ")[0]})
    return kept, dropped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _existing(path):
    try:
        with open(path, encoding="utf-8") as f:
            return {p["name"]: p for p in json.load(f).get("places", [])}
    except (FileNotFoundError, ValueError):
        return {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--city", default=None)
    parser.add_argument("--only", help="one restaurant, by name")
    parser.add_argument("--force", action="store_true", help="relabel places whose text is unchanged")
    parser.add_argument("--dry-run", action="store_true", help="print, don't write the file")
    args = parser.parse_args(argv)

    env.load(os.path.join(ROOT, ".env"))
    city = cities.get(args.city)
    dishes = specialties.load_dishes(city)
    if not dishes:
        sys.exit("No signature dishes for %s - add %s first." % (city["name"], city.get("local_dishes")))
    out_path = cities.data_path(city["specialties"])

    try:
        import anthropic
    except ImportError:
        sys.exit("Install the Anthropic SDK first: pip install -r restaurants/tools/requirements.txt")
    key = env.secret("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Set ANTHROPIC_API_KEY in .env (see .env.example).")
    client = anthropic.Anthropic(api_key=key)

    entries = [e for e in recognition.load(city)["entries"] if e.get("lat") is not None]
    places = group_places(entries)
    if args.only:
        places = [p for p in places if matching.names_match(p["name"], args.only)]
        if not places:
            sys.exit("No recognised restaurant called %r." % args.only)

    print("Downloading the city's OpenStreetMap data for restaurant websites...")
    websites = {l["id"]: l["url"] for l in osm.food_places_in_bounds(city)
                if "openstreetmap.org" not in (l.get("url") or "")}

    old = _existing(out_path)
    records, asked, labelled, failed = dict(old), 0, 0, 0
    for i, place in enumerate(places, 1):
        sources, skipped = gather_sources(place, websites)
        digest = input_hash(place["name"], sources, dishes)
        record = {"name": place["name"], "lat": place["lat"], "lon": place["lon"],
                  "known_for": [], "sources_read": [s["url"] for s in sources],
                  "sources_skipped": skipped, "input_hash": digest}

        previous = old.get(place["name"])
        if previous and previous.get("input_hash") == digest and not args.force:
            status = "unchanged"
            record = previous
        elif not sources:
            status = "no text to read"
        else:
            asked += 1
            try:
                labels, dropped = verified(classify(client, place["name"], dishes, sources), sources)
            except (RuntimeError, ValueError, KeyError) as e:
                failed += 1
                print("  [%d/%d] %s: FAILED - %s" % (i, len(places), place["name"], e))
                continue
            record["known_for"] = labels
            status = ", ".join(dishes[l["dish"]]["label"] for l in labels) or "no signature dish"
            if dropped:
                status += "  (CHECK: dropped %d label(s) whose quote was not in the text)" % len(dropped)
        labelled += bool(record["known_for"])
        records[place["name"]] = record
        print("  [%d/%d] %s: %s" % (i, len(places), place["name"], status))
        for item in record["known_for"] if status != "unchanged" else []:
            print("        %s - \"%s\" (%s)" % (item["dish"], item["quote"], item["source"]))

    print("\n%d places, %d sent to Claude, %d with a local specialty, %d failed."
          % (len(places), asked, labelled, failed))
    if args.dry_run:
        print("Dry run - %s not written." % city["specialties"])
        return

    data = {
        "_readme": [
            "Generated by python -m restaurants.tools.find_specialties - do not edit by hand; "
            "change data/local_dishes/%s.json or the tool and rerun." % city["key"],
            "known_for: signature dishes the restaurant's own descriptions say it is known for, "
            "each with a word-for-word quote and its source. sources_read / sources_skipped: "
            "what the labels are based on.",
        ],
        "city": city["key"],
        "model": MODEL,
        "generated": datetime.date.today().isoformat(),
        "places": sorted(records.values(), key=lambda p: p["name"].lower()),
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("Wrote %s." % city["specialties"])


if __name__ == "__main__":
    main()
