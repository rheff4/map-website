import unittest

from restaurants import prices


def place(google=None, yelp=None, recognition=()):
    return {"google": google, "yelp": yelp, "osm": None, "stub": None,
            "recognition": list(recognition)}


def listings(p):
    return [p[s] for s in ("google", "yelp", "osm", "stub") if p.get(s)]


class RangeTextTest(unittest.TestCase):
    def test_closed_range(self):
        r = {"startPrice": {"currencyCode": "USD", "units": "20"},
             "endPrice": {"currencyCode": "USD", "units": "30"}}
        self.assertEqual(prices.range_text(r), "$20–30")

    def test_open_ended_range(self):
        r = {"startPrice": {"currencyCode": "USD", "units": "100"}}
        self.assertEqual(prices.range_text(r), "$100+")

    def test_other_currency_uses_its_code(self):
        r = {"startPrice": {"currencyCode": "EUR", "units": "10"},
             "endPrice": {"currencyCode": "EUR", "units": "20"}}
        self.assertEqual(prices.range_text(r), "EUR 10–20")

    def test_missing_or_broken(self):
        self.assertIsNone(prices.range_text(None))
        self.assertIsNone(prices.range_text({"endPrice": {"units": "30"}}))
        self.assertIsNone(prices.range_text({"startPrice": {"units": "lots"}}))


class DescribeTest(unittest.TestCase):
    def test_google_range_wins(self):
        p = place(google={"price": 2, "price_text": "$20–30"}, yelp={"price": 3},
                  recognition=[{"source": "michelin_selected", "price": 2}])
        d = prices.describe(p, listings(p))
        self.assertEqual((d["price"], d["price_text"]), (2, "$20–30"))

    def test_yelp_level_comes_with_yelp_band(self):
        p = place(google={"price": 3, "price_text": None}, yelp={"price": 2})
        d = prices.describe(p, listings(p))
        self.assertEqual((d["price"], d["price_text"]), (2, "$11–30"))

    def test_michelin_band(self):
        p = place(recognition=[{"source": "eater_38", "price": None},
                               {"source": "michelin_bib_gourmand", "price": 2}])
        d = prices.describe(p, listings(p))
        self.assertEqual((d["price"], d["price_text"]), (2, "$25–50"))
        self.assertIn("MICHELIN", d["price_note"])

    def test_google_level_alone_has_no_dollar_text(self):
        p = place(google={"price": 3, "price_text": None})
        d = prices.describe(p, listings(p))
        self.assertEqual((d["price"], d["price_text"]), (3, None))

    def test_no_price_anywhere(self):
        p = place()
        self.assertEqual(prices.describe(p, listings(p))["price"], None)


if __name__ == "__main__":
    unittest.main()
