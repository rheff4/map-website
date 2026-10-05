import unittest

from restaurants import matching


def listing(id_, name, lat, lon):
    return {"id": id_, "name": name, "lat": lat, "lon": lon}


class NormalizeNameTest(unittest.TestCase):
    def test_strips_articles_punctuation_and_accents(self):
        self.assertEqual(matching.normalize_name("The Lantern & Ladle!"), "lantern ladle")
        self.assertEqual(matching.normalize_name("Café Pamplona"), "cafe pamplona")
        self.assertEqual(matching.normalize_name("Nonna Pina’s"), "nonna pinas")


class NamesMatchTest(unittest.TestCase):
    def test_same_name_different_spelling(self):
        self.assertTrue(matching.names_match("The Gilded Spoon", "Gilded Spoon"))
        self.assertTrue(matching.names_match("Lantern & Ladle", "Lantern and Ladle"))

    def test_one_name_extends_the_other(self):
        self.assertTrue(matching.names_match("Nonna Pina's", "Nonna Pina's Kitchen"))
        self.assertTrue(matching.names_match("Saffron Alley", "Saffron Alley Indian Cuisine"))

    def test_small_typo(self):
        self.assertTrue(matching.names_match("Nonna Pina Kitchen", "Nonna Pinas Kitchen"))

    def test_different_restaurants(self):
        self.assertFalse(matching.names_match("Saffron Alley", "Saffron Garden"))
        self.assertFalse(matching.names_match("Oyster Bar", "Bar"))
        self.assertFalse(matching.names_match("", "Anything"))


class MergeTest(unittest.TestCase):
    def test_pairs_google_and_yelp_records_of_the_same_place(self):
        g = [listing("g1", "The Gilded Spoon", 42.35450, -71.06900)]
        y = [listing("y1", "Gilded Spoon", 42.35470, -71.06890)]   # ~25 m away
        places = matching.merge_listings(g, y)
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0]["yelp"]["id"], "y1")

    def test_same_name_too_far_apart_stays_separate(self):
        g = [listing("g1", "Back Bay Noodle Bar", 42.3500, -71.0760)]
        y = [listing("y1", "Back Bay Noodle Bar", 42.3515, -71.0760)]  # ~170 m
        self.assertEqual(len(matching.merge_listings(g, y)), 2)

    def test_close_but_differently_named_stays_separate(self):
        g = [listing("g1", "Saffron Alley", 42.3510, -71.0640)]
        y = [listing("y1", "Tiny Dumpling Room", 42.3511, -71.0640)]
        self.assertEqual(len(matching.merge_listings(g, y)), 2)


class RecognitionMatchTest(unittest.TestCase):
    def setUp(self):
        self.places = matching.merge_listings(
            [listing("g1", "Lantern & Ladle", 42.3601, -71.0540),
             listing("g2", "Saffron Alley", 42.3510, -71.0640)], [])

    def test_matches_by_place_id_even_if_name_differs(self):
        entry = {"name": "L&L", "lat": None, "lon": None, "place_id": "g1"}
        self.assertEqual(matching.find_place_for_entry(self.places, entry)["key"], "g:g1")

    def test_matches_by_name_and_proximity(self):
        entry = {"name": "Lantern and Ladle", "lat": 42.3603, "lon": -71.0541, "place_id": None}
        self.assertEqual(matching.find_place_for_entry(self.places, entry)["key"], "g:g1")

    def test_unmatched_entries_are_returned(self):
        far = {"name": "Lantern and Ladle", "lat": 42.40, "lon": -71.0541, "place_id": None}
        unmatched = matching.attach_recognition(self.places, [far])
        self.assertEqual(unmatched, [far])
        self.assertEqual(self.places[0]["recognition"], [])


if __name__ == "__main__":
    unittest.main()
