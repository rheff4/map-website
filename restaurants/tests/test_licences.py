"""City licence matching and city limits - no network."""

import unittest

from restaurants.sources import licences
from server import cities

BOSTON = cities.get("boston")


def licence(name, lat, lon, address, dba=None):
    return {"names": [n for n in (dba, name) if n], "lat": lat, "lon": lon,
            "address": address, "kind": "Eating & Drinking", "since": "2020-01-01"}


class CityLimitsTest(unittest.TestCase):
    def test_boston_neighbourhoods_are_inside(self):
        self.assertTrue(licences.in_city_limits(BOSTON, -71.0656, 42.3550))   # Boston Common
        self.assertTrue(licences.in_city_limits(BOSTON, -71.0335, 42.3790))   # East Boston
        self.assertTrue(licences.in_city_limits(BOSTON, -71.1490, 42.2860))   # West Roxbury

    def test_neighbouring_towns_are_outside(self):
        self.assertFalse(licences.in_city_limits(BOSTON, -71.0862, 42.3625))  # Cambridge
        self.assertFalse(licences.in_city_limits(BOSTON, -71.1214, 42.3424))  # Brookline
        self.assertFalse(licences.in_city_limits(BOSTON, -71.1222, 42.3967))  # Somerville

    def test_city_without_a_boundary(self):
        self.assertIsNone(licences.in_city_limits({"key": "x"}, -71.0, 42.3))


class AddressKeyTest(unittest.TestCase):
    def test_house_number_and_street_without_suffix(self):
        self.assertEqual(licences.address_key("1704 Washington St., Boston, MA 02118"), "1704 washington")
        self.assertEqual(licences.address_key("554-562 Columbia Road, Dorchester"), "554 columbia")
        self.assertEqual(licences.address_key("11 Stuart St"), "11 stuart")    # "St" only as a word

    def test_no_house_number(self):
        self.assertIsNone(licences.address_key("Temple Place"))
        self.assertIsNone(licences.address_key(None))


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.index = licences.Index([
            licence("Toro Restaurant Group LLC", 42.3405, -71.0710, "1704 Washington, Boston", dba="Toro"),
            licence("Acme Hospitality Inc", 42.3480, -71.0702, "605 Tremont, Boston"),
        ])

    def test_matches_trade_name_nearby(self):
        self.assertEqual(self.index.find("Toro", 42.3406, -71.0711)["names"][0], "Toro")

    def test_matches_by_street_address_when_the_licence_has_a_legal_name(self):
        found = self.index.find("311 Omakase", 42.3481, -71.0703, "605 Tremont St., Boston, MA 02118")
        self.assertEqual(found["names"], ["Acme Hospitality Inc"])

    def test_no_match_far_away_or_different_name(self):
        self.assertIsNone(self.index.find("Toro", 42.3600, -71.0710))        # 2 km away
        self.assertIsNone(self.index.find("Somewhere Else", 42.3406, -71.0711))


if __name__ == "__main__":
    unittest.main()
