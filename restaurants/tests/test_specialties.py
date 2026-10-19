import json
import os
import tempfile
import unittest
from unittest import mock

from restaurants import scoring, specialties
from restaurants.tools import find_specialties

ARTICLE = (
    "The 38 best restaurants in Boston.\n"
    "Neptune Oyster. This North End raw bar is famous for its hot buttered lobster roll, "
    "and the line out the door proves it.\n"
    "Toro. Tapas in the South End; the corn is a must."
)


class TextHelpersTest(unittest.TestCase):
    def test_quote_must_be_word_for_word(self):
        self.assertTrue(specialties.quote_in("famous for its hot buttered lobster roll", ARTICLE))
        self.assertTrue(specialties.quote_in("FAMOUS  for its hot\nbuttered lobster roll.", ARTICLE))
        self.assertFalse(specialties.quote_in("known for its lobster roll", ARTICLE))
        self.assertFalse(specialties.quote_in("roll", ARTICLE))   # too short to prove anything

    def test_curly_quotes_match_straight_ones(self):
        self.assertTrue(specialties.quote_in("Boston’s best chowder", "boston's best chowder here"))

    def test_passages_cut_out_the_restaurant(self):
        text = specialties.passages("Neptune Oyster", ARTICLE, before=0, after=60)
        self.assertTrue(text.startswith("Neptune Oyster"))
        self.assertNotIn("Toro", text)

    def test_passages_empty_when_not_named(self):
        self.assertEqual(specialties.passages("Mooncusser", ARTICLE), "")

    def test_name_inside_another_word_does_not_count(self):
        self.assertEqual(specialties.passages("Tor", ARTICLE), "")

    def test_passages_find_names_without_the_or_accents(self):
        self.assertIn("Toro", specialties.passages("The Toro", ARTICLE))
        self.assertIn("Cafe Polonia", specialties.passages("Café Polonia", "Try Cafe Polonia."))


class VerifiedTest(unittest.TestCase):
    SOURCES = [{"label": "Eater 38 - The 38 best", "url": "https://eater.example/38", "text": ARTICLE}]

    def test_keeps_real_quotes_and_drops_invented_ones(self):
        kept, dropped = find_specialties.verified([
            {"dish": "lobster_roll", "source": 1, "quote": "famous for its hot buttered lobster roll"},
            {"dish": "oysters", "source": 1, "quote": "the best oysters in New England"},
        ], self.SOURCES)
        self.assertEqual([k["dish"] for k in kept], ["lobster_roll"])
        self.assertEqual(kept[0]["source"], "Eater 38")
        self.assertEqual(kept[0]["source_url"], "https://eater.example/38")
        self.assertEqual(len(dropped), 1)

    def test_one_label_per_dish(self):
        quote = "famous for its hot buttered lobster roll"
        kept, _ = find_specialties.verified([{"dish": "lobster_roll", "source": 1, "quote": quote}] * 2,
                                            self.SOURCES)
        self.assertEqual(len(kept), 1)


def _entry(name, lat=42.3633, lon=-71.0560):
    return {"name": name, "lat": lat, "lon": lon, "source": "eater_38"}


class LoadAndMatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        dishes = {"dishes": [{"key": "lobster_roll", "label": "Lobster roll"}]}
        generated = {"places": [
            {"name": "Neptune Oyster", "lat": 42.3633, "lon": -71.0560, "known_for": [
                {"dish": "lobster_roll", "quote": "famous for its lobster roll",
                 "source": "Eater 38", "source_url": "https://eater.example/38"},
                {"dish": "no_longer_listed", "quote": "x", "source": "Eater 38"}]},
            {"name": "Toro", "lat": 42.3381, "lon": -71.0763, "known_for": []},
        ]}
        for name, data in (("dishes.json", dishes), ("specialties.json", generated)):
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                json.dump(data, f)
        self.city = {"local_dishes": "dishes.json", "specialties": "specialties.json"}
        patcher = mock.patch("server.cities.data_path", lambda rel: os.path.join(self.dir, rel))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_load_drops_unknown_dishes_and_empty_places(self):
        records = specialties.load(self.city)
        self.assertEqual([r["name"] for r in records], ["Neptune Oyster"])
        self.assertEqual([k["dish"] for k in records[0]["known_for"]], ["lobster_roll"])
        self.assertEqual(records[0]["known_for"][0]["label"], "Lobster roll")

    def test_found_through_recognition_entries(self):
        records = specialties.load(self.city)
        place = {"recognition": [_entry("Neptune Oyster")]}
        self.assertEqual([k["dish"] for k in specialties.for_place(place, records)], ["lobster_roll"])

    def test_same_name_far_away_is_another_restaurant(self):
        records = specialties.load(self.city)
        place = {"recognition": [_entry("Neptune Oyster", lat=42.40, lon=-71.10)]}
        self.assertEqual(specialties.for_place(place, records), [])

    def test_city_without_files(self):
        self.assertEqual(specialties.load({}), [])
        self.assertEqual(specialties.load_dishes({}), {})


class ScoringTest(unittest.TestCase):
    ITEM = {"dish": "lobster_roll", "label": "Lobster roll", "quote": "famous for its lobster roll",
            "source": "Eater 38", "source_url": "https://eater.example/38"}

    def test_points_once_however_many_dishes(self):
        second = dict(self.ITEM, dish="oysters", label="Local oysters")
        result = scoring.score_place({"recognition": [], "specialties": [self.ITEM, second]})
        self.assertEqual(result["score"], scoring.SPECIALTY_POINTS)
        rows = [r for r in result["breakdown"] if r["kind"] == "specialty"]
        self.assertEqual([r["points"] for r in rows], [scoring.SPECIALTY_POINTS, 0])
        self.assertIn("famous for its lobster roll", rows[0]["note"])

    def test_specialty_alone_does_not_make_a_place_recognised(self):
        result = scoring.score_place({"recognition": [], "specialties": [self.ITEM]})
        self.assertFalse(result["recognised"])


if __name__ == "__main__":
    unittest.main()
