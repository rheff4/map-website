"""Tests for toilet labelling (routes/tools/build_toilets.py) and the
toilets endpoint. No network: OpenStreetMap data is made up here.

    python -m unittest discover -s routes/tests -t .
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from routes import api
from routes.tools import build_toilets as bt
from server.router import ApiError, Request

# A tiny made-up street grid near Boston Common, as (lon, lat) per node.
NODES = {
    1: (-71.0650, 42.3550),   # Tremont / Park
    2: (-71.0640, 42.3550),   # Tremont / Winter
    3: (-71.0650, 42.3560),   # Park, north end
    4: (-71.0660, 42.3550),   # Tremont, west
    5: (-71.0640, 42.3540),   # Winter, south end
}
STREETS = [
    {"name": "Tremont Street", "highway": "primary", "nodes": [4, 1, 2]},
    {"name": "Park Street", "highway": "residential", "nodes": [1, 3]},
    {"name": "Winter Street", "highway": "tertiary", "nodes": [2, 5]},
]


def toilet(lon, lat, name=None):
    return {"lon": lon, "lat": lat, "name": name}


class GenericNames(unittest.TestCase):

    def test_names_that_only_say_what_it_is(self):
        for name in ["City Toilet", "Public Toilets", "Restroom", "rest rooms", "WC",
                     "Comfort Station", "The Portland Loo", "Portland Loo", "Toilet"]:
            self.assertTrue(bt.is_generic(name), name)

    def test_real_names(self):
        for name in ["Government Center Toilets", "Kittie Knox Community Cycle Center Restroom",
                     "Boston Common Visitor Center"]:
            self.assertFalse(bt.is_generic(name), name)


class Intersections(unittest.TestCase):

    def test_found_where_different_streets_meet(self):
        found = {tuple(names) for _, names in bt.intersections(STREETS, NODES)}
        self.assertEqual(found, {("Tremont Street", "Park Street"),
                                 ("Tremont Street", "Winter Street")})

    def test_bigger_road_first(self):
        crossing = [n for p, n in bt.intersections(STREETS, NODES) if p == NODES[1]][0]
        self.assertEqual(crossing, ["Tremont Street", "Park Street"])   # primary before residential

    def test_a_street_split_into_pieces_does_not_meet_itself(self):
        pieces = [{"name": "Tremont Street", "highway": "primary", "nodes": [4, 1]},
                  {"name": "Tremont Street", "highway": "primary", "nodes": [1, 2]}]
        self.assertEqual(bt.intersections(pieces, NODES), [])

    def test_footpaths_and_motorways_are_not_corners(self):
        streets = STREETS[:1] + [
            {"name": "Some Path", "highway": "footway", "nodes": [1, 3]},
            {"name": "Storrow Drive", "highway": "motorway", "nodes": [2, 5]},
        ]
        self.assertEqual(bt.intersections(streets, NODES), [])


class Labels(unittest.TestCase):

    def setUp(self):
        self.crossings = bt.intersections(STREETS, NODES)

    def test_a_real_name_wins(self):
        label, how, _ = bt.label_for(toilet(-71.0645, 42.3551, "Government Center Toilets"),
                                     self.crossings, [])
        self.assertEqual((label, how), ("Government Center Toilets", "name"))

    def test_no_name_gives_the_nearest_intersection(self):
        label, how, metres = bt.label_for(toilet(-71.0641, 42.3551), self.crossings, [])
        self.assertEqual((label, how), ("Tremont Street / Winter Street", "intersection"))
        self.assertLess(metres, 20)

    def test_a_generic_name_is_treated_as_none(self):
        label, how, _ = bt.label_for(toilet(-71.0649, 42.3551, "City Toilet"), self.crossings, [])
        self.assertEqual((label, how), ("Tremont Street / Park Street", "intersection"))

    def test_without_intersections_it_names_the_nearest_street(self):
        points = [(NODES[3], "Park Street", 5)]
        label, how, _ = bt.label_for(toilet(-71.0650, 42.3561), [], points)
        self.assertEqual((label, how), ("near Park Street", "street"))


class Places(unittest.TestCase):

    def test_most_specific_place_first(self):
        areas = [{"name": "Massachusetts", "boundary": "administrative"},
                 {"name": "Logan Airport", "aeroway": "aerodrome"},
                 {"name": "Terminal B", "building": "yes"}]
        self.assertEqual(bt.place_name(areas), "Terminal B")

    def test_administrative_areas_are_never_used(self):
        self.assertIsNone(bt.place_name([{"name": "Boston", "boundary": "administrative"}]))

    def test_island(self):
        self.assertEqual(bt.place_name([{"name": "Spectacle Island", "place": "island"}]),
                         "Spectacle Island")


class Build(unittest.TestCase):
    """build() end to end, on made-up Overpass elements."""

    def elements(self, *toilets):
        els = [{"type": "node", "id": nid, "lon": lon, "lat": lat} for nid, (lon, lat) in NODES.items()]
        els += [{"type": "way", "id": 100 + i, "nodes": s["nodes"],
                 "tags": {"highway": s["highway"], "name": s["name"]}} for i, s in enumerate(STREETS)]
        for i, (lon, lat, tags) in enumerate(toilets):
            els.append({"type": "node", "id": 900 + i, "lon": lon, "lat": lat,
                        "tags": dict({"amenity": "toilets"}, **tags)})
        return els

    def test_private_toilets_are_left_out(self):
        feats, _, skipped = bt.build(self.elements(
            (-71.0641, 42.3551, {}),
            (-71.0642, 42.3551, {"access": "private"})), find_places=lambda pts: [])
        self.assertEqual(len(feats), 1)
        self.assertEqual(skipped, 1)

    def test_far_from_any_corner_it_uses_the_place_instead(self):
        # ~3 km east, out in the harbour.
        feats, counts, _ = bt.build(
            self.elements((-71.0300, 42.3550, {})),
            find_places=lambda pts: [[{"name": "Spectacle Island", "place": "island"}]])
        p = feats[0]["properties"]
        self.assertEqual((p["label"], p["label_source"]), ("Spectacle Island", "place"))
        self.assertGreater(p["label_distance_m"], bt.PLACE_FALLBACK_M)
        self.assertEqual(counts["place"], 1)

    def test_far_with_no_named_place_keeps_the_intersection(self):
        feats, _, _ = bt.build(self.elements((-71.0300, 42.3550, {})), find_places=lambda pts: [[]])
        self.assertEqual(feats[0]["properties"]["label_source"], "intersection")

    def test_nearby_toilets_never_trigger_a_place_lookup(self):
        asked = []
        bt.build(self.elements((-71.0641, 42.3551, {})),
                 find_places=lambda pts: asked.append(pts) or [])
        self.assertEqual(asked, [])

    def test_popup_details_are_kept(self):
        feats, _, _ = bt.build(self.elements(
            (-71.0641, 42.3551, {"access": "customers", "fee": "no", "wheelchair": "yes",
                                 "name": "City Toilet"})), find_places=lambda pts: [])
        p = feats[0]["properties"]
        self.assertEqual((p["access"], p["fee"], p["wheelchair"], p["name"]),
                         ("customers", "no", "yes", "City Toilet"))


class Endpoint(unittest.TestCase):

    def request(self):
        return Request("GET", "/api/routes/toilets", {}, None)

    def test_serves_the_built_file(self):
        with tempfile.TemporaryDirectory() as d:
            data = {"type": "FeatureCollection", "features": [{"type": "Feature"}]}
            with open(os.path.join(d, "boston.json"), "w", encoding="utf-8") as f:
                json.dump(data, f)
            with mock.patch.object(api, "TOILETS_DIR", d), mock.patch.dict(api._toilets, clear=True):
                self.assertEqual(api.toilets(self.request()), data)

    def test_missing_file_says_how_to_build_it(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(api, "TOILETS_DIR", d), mock.patch.dict(api._toilets, clear=True):
                with self.assertRaises(ApiError) as cm:
                    api.toilets(self.request())
        self.assertEqual(cm.exception.status, 404)
        self.assertIn("build_toilets", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
