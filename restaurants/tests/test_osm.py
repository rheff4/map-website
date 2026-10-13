"""OpenStreetMap parsing, hours and time zone - no network."""

import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

from restaurants import hours, matching, scoring
from restaurants.sources import osm

BOSTON = {"timezone": "America/New_York", "utc_offset_minutes": -300, "dst": "us"}


def element(**tags):
    return {"type": "node", "id": 42, "lat": 42.3601, "lon": -71.0540, "tags": tags}


class ParseTest(unittest.TestCase):
    def test_reads_name_address_cuisine_hours_and_website(self):
        listing = osm.parse(element(**{
            "name": "Lantern & Ladle", "cuisine": "seafood;oyster",
            "addr:housenumber": "1", "addr:street": "Mock Wharf", "addr:city": "Boston",
            "opening_hours": "Mo-Su 17:00-22:00", "website": "example.com"}))
        self.assertEqual(listing["id"], "node/42")
        self.assertEqual(listing["address"], "1 Mock Wharf, Boston")
        self.assertEqual([c["key"] for c in listing["cuisines"]], ["seafood"])
        self.assertEqual(len(listing["hours"]), 7)
        self.assertEqual(listing["url"], "https://example.com")
        self.assertIsNone(listing["rating"])

    def test_ways_use_their_centre_and_unnamed_places_are_dropped(self):
        way = {"type": "way", "id": 7, "center": {"lat": 42.35, "lon": -71.06},
               "tags": {"name": "Somewhere"}}
        self.assertEqual(osm.parse(way)["lat"], 42.35)
        self.assertIsNone(osm.parse(element(cuisine="pizza")))

    def test_without_a_website_links_to_the_osm_object(self):
        self.assertEqual(osm.parse(element(name="X"))["url"], "https://www.openstreetmap.org/node/42")


class OsmHoursTest(unittest.TestCase):
    def evening(self, day):   # 19:00 Boston time, October (EDT)
        return datetime(2026, 10, day, 19, 0, tzinfo=timezone(timedelta(hours=-4)))

    def test_common_formats(self):
        periods = hours.parse_osm("Mo-Fr 11:00-22:00; Sa,Su 12:00-23:00")
        self.assertTrue(hours.is_open(periods, -240, self.evening(5)))      # Monday

    def test_later_rule_overrides_and_off_days(self):
        periods = hours.parse_osm("Mo-Su 17:00-22:00; Mo off")
        self.assertFalse(hours.is_open(periods, -240, self.evening(5)))     # Monday
        self.assertTrue(hours.is_open(periods, -240, self.evening(6)))      # Tuesday

    def test_past_midnight_and_holidays(self):
        periods = hours.parse_osm("Fr-Sa 18:00-02:00; PH off")
        sat_1am = datetime(2026, 10, 10, 1, 0, tzinfo=timezone(timedelta(hours=-4)))
        self.assertTrue(hours.is_open(periods, -240, sat_1am))

    def test_unreadable_hours_are_unknown_not_closed(self):
        self.assertIsNone(hours.parse_osm("Jan-Mar Mo 10:00-12:00"))
        self.assertIsNone(hours.parse_osm("sunrise-sunset"))
        self.assertIsNone(hours.parse_osm(""))


class CityOffsetTest(unittest.TestCase):
    def at(self, iso):
        return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    def test_us_daylight_saving_boundaries(self):
        # The fallback rule, as on Windows without a time-zone database.
        city = dict(BOSTON, timezone="Not/AZone")
        self.assertEqual(hours.city_utc_offset(city, self.at("2026-03-08T06:59")), -300)
        self.assertEqual(hours.city_utc_offset(city, self.at("2026-03-08T07:00")), -240)
        self.assertEqual(hours.city_utc_offset(city, self.at("2026-11-01T05:59")), -240)
        self.assertEqual(hours.city_utc_offset(city, self.at("2026-11-01T06:00")), -300)

    def test_unconfigured_city(self):
        self.assertIsNone(hours.city_utc_offset({"timezone": "Not/AZone"}))


class OsmMatchingTest(unittest.TestCase):
    def test_osm_record_joins_the_google_record_of_the_same_place(self):
        g = {"id": "g1", "name": "The Gilded Spoon", "lat": 42.35450, "lon": -71.06900}
        o = {"id": "node/1", "name": "Gilded Spoon", "lat": 42.35460, "lon": -71.06905}
        places = matching.merge_listings([g], [], [o])
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0]["osm"]["id"], "node/1")

    def test_curated_entry_matches_by_osm_id(self):
        o = {"id": "way/5", "name": "Totally Different Name", "lat": 42.35, "lon": -71.06}
        places = matching.merge_listings([], [], [o])
        entry = {"name": "Three 1 One", "lat": None, "lon": None, "place_id": None, "osm_id": "way/5"}
        self.assertIs(matching.find_place_for_entry(places, entry), places[0])

    def test_brand_tag_catches_chains_with_local_names(self):
        chains = scoring.load_chains()
        self.assertTrue(scoring.is_chain("Starbucks", chains))
        self.assertFalse(scoring.is_chain("Boston Common Coffee", chains))


if __name__ == "__main__":
    unittest.main()


class SnapshotSearchTest(unittest.TestCase):
    """Searches are answered from the cached city-wide snapshot."""

    def setUp(self):
        places = [
            dict(osm.parse(element(name="Near Restaurant", amenity="restaurant")), id="node/1"),
            dict(osm.parse(element(name="Near Cafe", amenity="cafe")), id="node/2"),
            dict(osm.parse(element(name="Far Restaurant", amenity="restaurant")), id="node/3",
                 lat=42.40, lon=-71.0540),
        ]
        patcher = unittest.mock.patch.object(osm, "city_snapshot", return_value=places)
        patcher.start()
        self.addCleanup(patcher.stop)

    def names(self, **kw):
        return sorted(p["name"] for p in osm.search(42.3601, -71.0540, 1000, {"key": "x"}, **kw))

    def test_only_restaurants_within_reach(self):
        self.assertEqual(self.names(), ["Near Restaurant"])

    def test_recognised_places_count_whatever_their_tag(self):
        self.assertEqual(self.names(extra_ids=["node/2"]), ["Near Cafe", "Near Restaurant"])
