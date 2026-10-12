"""Bounds are checked independently against exhaustive integer optima."""
import copy
import json
import random
import time
import unittest

from test_context import ROOT, card, request, solve
from independent_check import brute_force_optimum, check_response, certificate_profile


class CertificateTests(unittest.TestCase):
    def checked(self, value):
        response = solve(value)
        result = check_response(value, response)
        self.assertEqual(result["core_errors"], [])
        self.assertTrue(result["certificate"]["valid"])
        return response, result["certificate"]

    def test_fractional_knapsack_exact_integer_bound(self):
        value = request([card("a", tokens=3), card("b", tokens=2), card("c", tokens=4)],
                        {"f_a": 12, "f_b": 6, "f_c": 4}, budget=5)
        _, bound = self.checked(value)
        self.assertEqual(bound["upper_numerator"], 18 * bound["scale"])
        self.assertTrue(bound["proves_exact_optimum"])
        self.assertTrue(bound["proves_99_percent_of_optimum"])

    def test_fractional_gap_is_reported_honestly(self):
        value = request([card("a", tokens=2)], {"f_a": 100}, budget=1)
        response, bound = self.checked(value)
        self.assertEqual(response["utility"], 0)
        self.assertEqual(bound["upper_numerator"], 50 * bound["scale"])
        self.assertFalse(bound["within_5_percent"])
        self.assertFalse(bound["proves_99_percent_of_optimum"])

    def test_fractional_price_rounding_never_underbounds(self):
        value = request([card("a", tokens=3)], {"f_a": 1}, budget=1)
        _, bound = self.checked(value)
        # Relaxed optimum is 1/3; fixed-point rounding must stay above it.
        self.assertGreaterEqual(3 * bound["upper_numerator"], bound["scale"])
        self.assertLessEqual(3 * bound["upper_numerator"], bound["scale"] + 3)

    def test_all_features_and_all_active_cards_including_unusable(self):
        value = request([card("a", requires=["b"]), card("b", version="right")],
                        {"f_a": 100, "f_b": 7, "uncovered": 1000}, budget=10)
        value["versions"].append({"id": "right", "parents": ["v0"]})
        response, bound = self.checked(value)
        self.assertEqual(response["utility"], 0)
        self.assertEqual(set(response["certificate"]["feature_prices"]), set(value["features"]))
        self.assertEqual(bound["upper_numerator"], 100 * bound["scale"])
        self.assertFalse(bound["proves_exact_optimum"])

    def test_conflicts_are_relaxed_not_silently_removed(self):
        value = request([card("a"), card("b")], {"f_a": 10, "f_b": 10},
                        budget=2, contradictions=[["a", "b"]])
        response, bound = self.checked(value)
        self.assertEqual(response["utility"], 10)
        self.assertEqual(bound["upper_numerator"], 20 * bound["scale"])
        self.assertFalse(bound["within_5_percent"])

    def test_generated_overlapping_bounds_dominate_exhaustive_optima(self):
        for seed in range(60):
            rng = random.Random(seed)
            weights = {"f" + str(i): rng.randint(1, 1000) for i in range(7)}
            cards = [card("c" + str(i), tokens=rng.randint(1, 6),
                          covers=rng.sample(list(weights), rng.randint(0, 5))) for i in range(8)]
            value = request(cards, weights, budget=rng.randint(0, 20))
            with self.subTest(seed=seed):
                response, bound = self.checked(value)
                optimum = brute_force_optimum(value)
                self.assertEqual(response["utility"], optimum)
                self.assertGreaterEqual(bound["upper_numerator"], optimum * bound["scale"])

    def test_expired_certificate_search_still_returns_valid_prices(self):
        from context_model import prepare
        from context_certificate import make_certificate
        value = request([card("a", covers=["f"]), card("b", covers=["f"])], {"f": 9}, budget=1)
        response = solve(value)
        certificate, upper = make_certificate(prepare(value), response["utility"], time.perf_counter() - 1)
        response["certificate"] = certificate
        bound = check_response(value, response)["certificate"]
        self.assertTrue(bound["valid"])
        self.assertEqual(bound["upper_numerator"], upper)

    def test_public_tight_gap_profile(self):
        corpus = json.loads((ROOT / "benchmark_inputs.json").read_text(encoding="utf-8"))
        count = 0
        for item in corpus["instances"]:
            from independent_check import request_status
            if request_status(item["request"]) == "ok" and certificate_profile(item["request"]):
                count += 1
                _, bound = self.checked(item["request"])
                self.assertTrue(bound["within_5_percent"], item["id"])
        self.assertEqual(count, 2)

    def test_profile_checks_inactive_card_constraints(self):
        value = request([card("old", requires=["old"]), card("new", version="v1", supersedes=["old"])])
        value["versions"].append({"id": "v1", "parents": ["v0"]})
        value["head"] = "v1"
        self.assertFalse(certificate_profile(value))

    def test_tampered_certificate_rejected_separately(self):
        value = request([card("a")])
        response = solve(value)
        for mutate in [lambda c: c["feature_prices"].clear(),
                       lambda c: c.update(budget_price=-1),
                       lambda c: c["feature_prices"].update(f_a=2_000_000),
                       lambda c: c.update(scale=True)]:
            broken = copy.deepcopy(response)
            mutate(broken["certificate"])
            result = check_response(value, broken)
            self.assertEqual(result["core_errors"], [])
            self.assertFalse(result["certificate"]["valid"])


if __name__ == "__main__":
    unittest.main()
