import unittest

from restaurants import scoring


def place(recognition=(), google=None, yelp=None):
    return {"recognition": [{"source": s, "source_url": None} for s in recognition],
            "google": google, "yelp": yelp}


def rated(rating, reviews):
    return {"rating": rating, "review_count": reviews, "url": None}


class RecognitionTest(unittest.TestCase):
    def test_one_critic_beats_perfect_ratings_alone(self):
        critic = scoring.score_place(place(["eater_38"]))
        ratings_only = scoring.score_place(place(google=rated(4.9, 5000), yelp=rated(5.0, 3000)))
        self.assertGreater(critic["score"], ratings_only["score"])
        self.assertTrue(critic["recognised"])
        self.assertFalse(ratings_only["recognised"])

    def test_same_publisher_counts_once(self):
        both = scoring.score_place(place(["michelin_star", "michelin_selected"]))
        star = scoring.score_place(place(["michelin_star"]))
        self.assertEqual(both["score"], star["score"])
        ignored = [r for r in both["breakdown"] if r.get("source") == "michelin_selected"]
        self.assertEqual(ignored[0]["points"], 0)

    def test_independent_publishers_earn_agreement_bonus(self):
        two = scoring.score_place(place(["eater_38", "city_guide"]))
        expected = (scoring.SOURCES["eater_38"]["weight"]
                    + scoring.SOURCES["city_guide"]["weight"] + scoring.AGREEMENT_BONUS)
        self.assertEqual(two["score"], expected)

    def test_influencer_lists_stack_up_to_the_limit(self):
        many = scoring.score_place(place(["influencer"] * 5))
        limit = scoring.GROUP_LIMITS["influencer"]
        self.assertEqual(many["score"], limit * scoring.SOURCES["influencer"]["weight"])

    def test_unknown_source_is_ignored(self):
        self.assertEqual(scoring.score_place(place(["my_blog"]))["score"], 0)


class RatingTest(unittest.TestCase):
    def test_many_reviews_beat_few_perfect_ones(self):
        popular = scoring.score_place(place(google=rated(4.6, 900)))
        tiny = scoring.score_place(place(google=rated(5.0, 60)))
        self.assertGreater(popular["score"], tiny["score"])

    def test_too_few_reviews_never_score(self):
        result = scoring.score_place(place(google=rated(5.0, 10)))
        self.assertEqual(result["score"], 0)
        self.assertIn("fewer than", result["breakdown"][0]["why"])

    def test_ratings_are_capped(self):
        result = scoring.score_place(place(google=rated(5.0, 100000)))
        self.assertEqual(result["score"], scoring.RATINGS["google"]["max_points"])

    def test_both_platforms_together_stay_below_top_recognition(self):
        best_ratings = sum(cfg["max_points"] for cfg in scoring.RATINGS.values())
        self.assertLess(best_ratings, scoring.SOURCES["michelin_star"]["weight"])


class ChainTest(unittest.TestCase):
    def test_chain_names_and_variants(self):
        chains = scoring.load_chains()
        self.assertTrue(scoring.is_chain("Chipotle Mexican Grill", chains))
        self.assertTrue(scoring.is_chain("McDonalds", chains))
        self.assertTrue(scoring.is_chain("The Cheesecake Factory", chains))
        self.assertFalse(scoring.is_chain("Saffron Alley", chains))
        # A prefix only matches whole words.
        self.assertFalse(scoring.is_chain("Subwayside Diner", chains))


if __name__ == "__main__":
    unittest.main()
