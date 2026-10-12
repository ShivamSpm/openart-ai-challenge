"""Local independent regression: python -m unittest discover -s tests -v.

Set RUN_FULL_CORPUS=1 to additionally invoke the solver on all public requests.
That optional smoke test is not the unchanged Linux recorder measurement.
"""
from __future__ import annotations

import copy
import collections
import hashlib
import json
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from independent_check import (brute_force_optimum, check_response, inspect_request,
                               request_status, check_observations, _strict_json)


def card(card_id, tokens=1, covers=None, requires=None, supersedes=None,
         version="v0", group=None):
    return {"id": card_id, "version": version, "supersedes": supersedes or [],
            "requires": requires or [], "conflict_group": group, "tokens": tokens,
            "covers": ["f_" + card_id] if covers is None else covers}


def request(cards=None, features=None, budget=100, mandatory=None, contradictions=None):
    cards = [] if cards is None else cards
    if features is None:
        features = {f: 1 for item in cards for f in item["covers"]}
    return {"versions": [{"id": "v0", "parents": []}], "cards": cards,
            "features": features, "head": "v0", "budget": budget,
            "mandatory": mandatory or [], "contradictions": contradictions or []}


def solve(value):
    # The oracle above never imports the implementation; only tests invoke it.
    from context_solver import solve as implementation
    return implementation(value)


def reordered(value):
    value = copy.deepcopy(value)
    value["cards"].reverse()
    value["versions"].reverse()
    value["mandatory"].reverse()
    value["contradictions"] = [pair[::-1] for pair in value["contradictions"][::-1]]
    for version in value["versions"]:
        version["parents"].reverse()
    for item in value["cards"]:
        for key in ("requires", "supersedes", "covers"):
            item[key].reverse()
    value["features"] = dict(reversed(list(value["features"].items())))
    return value


def relabeled(value):
    value = copy.deepcopy(value)
    cards = {item["id"]: "c:" + str(i) for i, item in enumerate(value["cards"][::-1])}
    versions = {item["id"]: "v:" + str(i) for i, item in enumerate(value["versions"][::-1])}
    features = {name: "f:" + str(i) for i, name in enumerate(reversed(value["features"]))}
    groups = {item["conflict_group"] for item in value["cards"] if item["conflict_group"] is not None}
    groups = {name: "g:" + str(i) for i, name in enumerate(sorted(groups))}
    for item in value["versions"]:
        item["id"] = versions[item["id"]]
        item["parents"] = [versions[x] for x in item["parents"]]
    for item in value["cards"]:
        item["id"] = cards[item["id"]]
        item["version"] = versions[item["version"]]
        item["supersedes"] = [cards[x] for x in item["supersedes"]]
        item["requires"] = [cards[x] for x in item["requires"]]
        item["covers"] = [features[x] for x in item["covers"]]
        if item["conflict_group"] is not None:
            item["conflict_group"] = groups[item["conflict_group"]]
    value["head"] = versions[value["head"]]
    value["mandatory"] = [cards[x] for x in value["mandatory"]]
    value["contradictions"] = [[cards[x] for x in pair] for pair in value["contradictions"]]
    value["features"] = {features[name]: weight for name, weight in value["features"].items()}
    return reordered(value)


class ContextTests(unittest.TestCase):
    def assert_result(self, value, expected_utility=None):
        before = json.dumps(value, sort_keys=True)
        response = solve(value)
        checked = check_response(value, response)
        self.assertEqual(checked["core_errors"], [], (value, response, checked))
        self.assertEqual(json.dumps(value, sort_keys=True), before, "solver mutated the input")
        if expected_utility is not None:
            self.assertEqual(response["utility"], expected_utility)
        if response["status"] == "ok":
            self.assertTrue(checked["certificate"] and checked["certificate"]["valid"])
            if expected_utility is not None:
                self.assertGreaterEqual(checked["certificate"]["upper_numerator"],
                                        expected_utility * checked["certificate"]["scale"])
        else:
            self.assertNotIn("certificate", response)
        return response

    def test_empty_and_zero_budget(self):
        self.assert_result(request(budget=0), 0)
        self.assert_result(request([card("a")], budget=0), 0)
        value = request([card("a")], budget=0, mandatory=["a"])
        self.assertEqual(self.assert_result(value)["status"], "infeasible")

    def test_sample_optimum(self):
        value = json.loads((ROOT / "director_session.json").read_text(encoding="utf-8"))
        self.assertEqual(brute_force_optimum(value), 61)
        self.assert_result(value, 61)

    def test_shared_requirements_and_exact_budget(self):
        value = request([card("base", tokens=3, covers=["identity"]),
                         card("a", tokens=2, covers=["common"], requires=["base"]),
                         card("b", tokens=2, covers=["common", "extra"], requires=["base"])],
                        {"identity": 2, "common": 10, "extra": 5}, budget=7, mandatory=["a", "b"])
        result = self.assert_result(value, 17)
        self.assertEqual(result["tokens"], 7)
        self.assertEqual(result["selected"], ["a", "b", "base"])
        value["budget"] = 6
        self.assertEqual(self.assert_result(value)["status"], "infeasible")

    def test_feature_union_is_objective(self):
        value = request([card("a", tokens=2, covers=["same"]),
                         card("b", tokens=2, covers=["same"]),
                         card("c", tokens=2, covers=["other"])],
                        {"same": 10, "other": 9}, budget=4)
        self.assert_result(value, 19)

    def test_requirement_cycles_and_self_requirement(self):
        value = request([card("a", tokens=2, requires=["b"]),
                         card("b", tokens=2, requires=["a"]), card("c", tokens=1)],
                        {"f_a": 5, "f_b": 7, "f_c": 8}, budget=4)
        self.assert_result(value, 12)
        self.assert_result(request([card("self", requires=["self"])], budget=1), 1)

    def test_contradictions_are_symmetric_not_transitive(self):
        value = request([card("a", tokens=10), card("b", tokens=10), card("c", tokens=10)],
                        {"f_a": 20, "f_b": 30, "f_c": 40}, contradictions=[["a", "b"], ["b", "c"]])
        self.assertEqual(self.assert_result(value, 60)["selected"], ["a", "c"])
        self.assert_result(reordered(value), 60)

    def test_groups_and_cross_group_pairs(self):
        value = request([card("a", group="one"), card("b", group="one"),
                         card("c", group="two"), card("d", group="two")],
                        {"f_a": 10, "f_b": 7, "f_c": 9, "f_d": 6},
                        contradictions=[["a", "c"], ["a", "b"]])
        self.assert_result(value, 16)

    def test_mandatory_infeasibility(self):
        values = [request([card("a", group="g"), card("b", group="g")], mandatory=["a", "b"]),
                  request([card("a", requires=["b"]), card("b", requires=["a"])],
                          mandatory=["a"], contradictions=[["a", "b"]]),
                  request([card("a", requires=["b"]), card("b", requires=["c"]), card("c")],
                          mandatory=["a"], contradictions=[["a", "c"]])]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(self.assert_result(value)["status"], "infeasible")

    def test_invalid_validation_precedes_infeasibility(self):
        value = request([card("a", group="g"), card("b", group="g")], mandatory=["a", "b"])
        value["unknown"] = "invalid"
        self.assertEqual(self.assert_result(value)["status"], "invalid")

    def test_supersession_chain_and_invisible_superseder(self):
        value = request([card("old"), card("middle", version="v1", supersedes=["old"]),
                         card("new", version="v2", supersedes=["middle"])])
        value["versions"] += [{"id": "v1", "parents": ["v0"]}, {"id": "v2", "parents": ["v1"]}]
        value["head"] = "v2"
        self.assertEqual(self.assert_result(value, 1)["selected"], ["new"])
        value["cards"][2]["tokens"] = 101
        self.assert_result(value, 0)  # Unselected successors still deactivate predecessors.
        value = request([card("old"), card("new", version="right", supersedes=["old"])])
        value["versions"] += [{"id": "left", "parents": ["v0"]}, {"id": "right", "parents": ["v0"]}]
        value["head"] = "left"
        value["contradictions"] = [["old", "new"]]
        self.assertEqual(self.assert_result(value, 1)["selected"], ["old"])

    def test_requirements_never_redirect_to_successor(self):
        value = request([card("old"), card("new", version="v1", supersedes=["old"]),
                         card("dependent", version="v1", requires=["old"])])
        value["versions"].append({"id": "v1", "parents": ["v0"]})
        value["head"] = "v1"
        self.assertEqual(self.assert_result(value, 1)["selected"], ["new"])
        value["mandatory"] = ["dependent"]
        self.assertEqual(self.assert_result(value)["status"], "infeasible")

    def test_invisible_requirement_and_exact_contradiction_endpoints(self):
        value = request([card("a", requires=["b"]), card("b", version="right")])
        value["versions"] += [{"id": "left", "parents": ["v0"]}, {"id": "right", "parents": ["v0"]}]
        value["head"] = "left"
        self.assert_result(value, 0)
        value["mandatory"] = ["a"]
        self.assertEqual(self.assert_result(value)["status"], "infeasible")
        value = request([card("old"), card("new", version="v1", supersedes=["old"]), card("peer")],
                        contradictions=[["old", "peer"]])
        value["versions"].append({"id": "v1", "parents": ["v0"]})
        value["head"] = "v1"
        self.assertEqual(self.assert_result(value, 2)["selected"], ["new", "peer"])

    def test_24_active_cards_exact_with_mandatory_baseline(self):
        cards = [card("fixed_" + str(i)) for i in range(21)] + [card("a", tokens=2), card("b", tokens=2), card("c", tokens=2)]
        value = request(cards, budget=23, mandatory=[item["id"] for item in cards[:21]])
        value["features"].update(f_a=2, f_b=5, f_c=7)
        self.assertEqual(len(inspect_request(value)["active"]), 24)
        self.assertEqual(brute_force_optimum(value), 28)
        self.assert_result(value, 28)

    def test_order_relabeling_and_repeat(self):
        value = json.loads((ROOT / "director_session.json").read_text(encoding="utf-8"))
        for variant in [value, reordered(value), relabeled(value), value]:
            with self.subTest(head=variant["head"]):
                self.assert_result(variant, 61)

    def test_generated_small_instances_against_independent_oracle(self):
        for seed in range(45):
            rng = random.Random(seed)
            count = rng.randint(1, 9)
            names = ["c" + str(i) for i in range(count)]
            features = {"f" + str(i): rng.randint(1, 15) for i in range(5)}
            cards = []
            for name in names:
                cards.append(card(name, tokens=rng.randint(1, 7),
                                  covers=rng.sample(list(features), rng.randint(0, 3)),
                                  requires=rng.sample(names, rng.randint(0, min(2, count))),
                                  version=rng.choice(["v0", "left", "right"]),
                                  group=rng.choice([None, None, "g0", "g1"])))
            pairs = [[a, b] for i, a in enumerate(names) for b in names[i + 1:] if rng.random() < .18]
            value = request(cards, features, budget=rng.randint(0, 20),
                            mandatory=rng.sample(names, rng.randint(0, min(2, count))), contradictions=pairs)
            value["versions"] += [{"id": "left", "parents": ["v0"]}, {"id": "right", "parents": ["v0"]},
                                  {"id": "merge", "parents": ["left", "right"]}]
            value["head"] = rng.choice(["v0", "left", "right", "merge"])
            with self.subTest(seed=seed):
                optimum = brute_force_optimum(value)
                result = self.assert_result(value, optimum)
                self.assertEqual(result["status"], request_status(value))
                if seed % 9 == 0:
                    self.assert_result(relabeled(value), optimum)

    @unittest.skipUnless(os.environ.get("RUN_FULL_CORPUS") == "1", "set RUN_FULL_CORPUS=1 for a fresh all-request smoke test")
    def test_full_public_corpus(self):
        corpus = json.loads((ROOT / "benchmark_inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(len(corpus["instances"]), 28)
        for item in corpus["instances"]:
            with self.subTest(instance=item["id"]):
                value = item["request"]
                result = self.assert_result(value)
                if result["status"] == "ok" and len(inspect_request(value)["active"]) <= 24:
                    self.assertEqual(result["utility"], brute_force_optimum(value))


class CliBoundaryTests(unittest.TestCase):
    def call_cli(self, raw, cwd=None):
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        run = subprocess.run([sys.executable, str(ROOT / "context_solver.py")], input=raw,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", errors="replace"))
        self.assertTrue(run.stdout.endswith(b"\n"))
        result = _strict_json(run.stdout.decode("utf-8", errors="strict"))
        self.assertIsInstance(result, dict)
        return result

    def test_arbitrary_writable_working_directory(self):
        value = request([card("a")])
        with tempfile.TemporaryDirectory() as temporary:
            result = self.call_cli(json.dumps(value), cwd=temporary)
            self.assertEqual(check_response(value, result)["core_errors"], [])
            self.assertEqual(list(pathlib.Path(temporary).iterdir()), [])

    def test_raw_json_lexical_errors(self):
        empty = json.dumps(request(budget=0), separators=(",", ":"))
        cases = [b"\xff", "", "[]", "null", "{", empty + empty,
                 empty.replace('"budget":0', '"budget":0,"budget":0'),
                 empty.replace('"id":"v0"', '"id":"v0","id":"v0"'),
                 empty.replace('"budget":0', '"budget":0.0'),
                 empty.replace('"budget":0', '"budget":0e0'),
                 empty.replace('"budget":0', '"budget":false'),
                 empty.replace('"budget":0', '"budget":NaN'),
                 empty.replace('"budget":0', '"budget":Infinity'),
                 empty.replace('"budget":0', '"budget":1e999')]
        for raw in cases:
            with self.subTest(raw=str(raw)[:80]):
                response = self.call_cli(raw)
                self.assertEqual(response.get("status"), "invalid")
                self.assertEqual(set(response), {"status", "reason"})
                self.assertTrue(response["reason"])
        with_card = json.dumps(request([card("a")]), separators=(",", ":"))
        for raw in [with_card.replace('"tokens":1', '"tokens":1,"tokens":1'),
                    with_card.replace('"f_a":1', '"f_a":1,"f_a":1'),
                    with_card.replace('"tokens":1', '"tokens":1e0'),
                    with_card.replace('"f_a":1', '"f_a":1.0')]:
            self.assertEqual(self.call_cli(raw)["status"], "invalid")

    def test_structural_type_reference_and_range_errors(self):
        base = request([card("a"), card("b")])
        cases = []

        def changed(action):
            value = copy.deepcopy(base)
            action(value)
            cases.append(value)

        changed(lambda r: r.pop("mandatory"))
        changed(lambda r: r.update(extra=True))
        changed(lambda r: r.update(head="missing"))
        changed(lambda r: r.update(budget=-1))
        changed(lambda r: r.update(budget=8193))
        changed(lambda r: r.update(mandatory=["a", "a"]))
        changed(lambda r: r.update(contradictions=[["a", "a"]]))
        changed(lambda r: r.update(contradictions=[["a", "b"], ["b", "a"]]))
        changed(lambda r: r.update(contradictions=[["a", "missing"]]))
        changed(lambda r: r.update(contradictions=[["a", "b", "a"]]))
        changed(lambda r: r.update(contradictions=["ab"]))
        changed(lambda r: r.update(versions=[]))
        changed(lambda r: r["versions"][0].update(parents=["v0"]))
        changed(lambda r: r["versions"][0].update(extra=0))
        changed(lambda r: r["cards"][0].update(tokens=True))
        changed(lambda r: r["cards"][0].update(tokens=0))
        changed(lambda r: r["cards"][0].update(tokens=513))
        changed(lambda r: r["cards"][0].update(id="\u00e9"))
        changed(lambda r: r["cards"][0].update(id="x" * 65))
        changed(lambda r: r["cards"][0].update(requires=["missing"]))
        changed(lambda r: r["cards"][0].update(requires=["b", "b"]))
        changed(lambda r: r["cards"][0].update(supersedes=["a"]))
        changed(lambda r: r["cards"][0].update(supersedes=["b"]))
        changed(lambda r: r["cards"][0].update(covers=["missing"]))
        changed(lambda r: r["cards"][0].update(conflict_group=""))
        changed(lambda r: r["cards"][0].update(text=None))
        changed(lambda r: r["cards"][0].update(text="x" * 2049))
        changed(lambda r: r["cards"][0].update(extra="unknown"))
        changed(lambda r: r["features"].update(f_a=False))
        changed(lambda r: r["features"].update(f_a=1001))
        changed(lambda r: r.update(cards=r["cards"] * 251))
        changed(lambda r: r.update(features={"f" + str(i): 1 for i in range(1025)}))
        changed(lambda r: r.update(versions=[{"id": "v" + str(i), "parents": []} for i in range(97)]))
        across_branches = copy.deepcopy(base)
        across_branches["versions"] += [{"id": "left", "parents": ["v0"]}, {"id": "right", "parents": ["v0"]}]
        across_branches["cards"][0].update(version="left", supersedes=["b"])
        across_branches["cards"][1]["version"] = "right"
        cases.append(across_branches)
        cycle = copy.deepcopy(base)
        cycle["versions"] = [{"id": "v0", "parents": ["v1"]}, {"id": "v1", "parents": ["v0"]}]
        cases.append(cycle)
        wide = request([card("c" + str(i)) for i in range(10)])
        wide["cards"][0]["requires"] = ["c" + str(i) for i in range(1, 10)]
        cases.append(wide)
        many_features = request([card("a", covers=["f" + str(i) for i in range(65)])])
        cases.append(many_features)
        many_parents = copy.deepcopy(base)
        many_parents["versions"] = [{"id": "v" + str(i), "parents": []} for i in range(10)]
        many_parents["versions"][0]["parents"] = ["v" + str(i) for i in range(1, 10)]
        cases.append(many_parents)
        for value in cases:
            with self.subTest(value=str(value)[:120]):
                response = self.call_cli(json.dumps(value))
                self.assertEqual(response["status"], "invalid")
                self.assertEqual(check_response(value, response)["core_errors"], [])

    def test_raw_byte_limit_including_whitespace(self):
        raw = json.dumps(request(budget=0)).encode("utf-8")
        padded = raw + b" " * (16 * 1024 * 1024 - len(raw))
        self.assertEqual(self.call_cli(padded)["status"], "ok")
        self.assertEqual(self.call_cli(padded + b" ")["status"], "invalid")

    def test_optional_text_is_ignored_not_retokenized(self):
        value = request([card("a", tokens=1)], budget=1)
        value["cards"][0]["text"] = "\U0001f3ac" * 2048
        result = self.call_cli(json.dumps(value, ensure_ascii=False))
        self.assertEqual(check_response(value, result)["core_errors"], [])
        self.assertEqual(result["tokens"], 1)
        self.assertEqual(result["utility"], 1)


class CheckerSelfTests(unittest.TestCase):
    def test_public_corpus_independent_statuses(self):
        corpus = json.loads((ROOT / "benchmark_inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(len(corpus["instances"]), 28)
        statuses = collections.Counter(request_status(item["request"]) for item in corpus["instances"])
        self.assertEqual(statuses, {"ok": 24, "invalid": 2, "infeasible": 2})
        large = [item for item in corpus["instances"] if request_status(item["request"]) == "ok"
                 and len(inspect_request(item["request"])["active"]) >= 250]
        self.assertEqual(len(large), 15)

    def test_checker_rejects_fabricated_totals_and_invalid_selections(self):
        value = request([card("a"), card("b")], budget=1, contradictions=[["a", "b"]])
        malformed = [{"status": "ok", "selected": ["a", "b"], "tokens": 2, "covered": ["f_a", "f_b"], "utility": 2},
                     {"status": "ok", "selected": ["a"], "tokens": 0, "covered": ["f_a"], "utility": 9},
                     {"status": "ok", "selected": ["a", "a"], "tokens": 1, "covered": ["f_a"], "utility": 1},
                     {"status": "infeasible", "reason": "heuristic failed"}]
        for response in malformed:
            self.assertTrue(check_response(value, response)["core_errors"])

    def test_optional_certificate_checked_separately(self):
        value = request([card("a")], budget=1)
        response = {"status": "ok", "selected": ["a"], "tokens": 1, "covered": ["f_a"], "utility": 1,
                    "certificate": {"scale": 1, "budget_price": 0, "feature_prices": {"f_a": 1}}}
        result = check_response(value, response)
        self.assertFalse(result["core_errors"])
        self.assertTrue(result["certificate"]["within_5_percent"])
        response["certificate"]["scale"] = True
        result = check_response(value, response)
        self.assertFalse(result["core_errors"])
        self.assertFalse(result["certificate"]["valid"])

    def test_observation_checker_retains_unsuccessful_requests(self):
        value = request([card("a")], budget=1)
        response = {"status": "ok", "selected": ["a"], "tokens": 1, "covered": ["f_a"], "utility": 1}
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        corpus = {"schema": "openart-context-inputs/2", "instances": [{"id": "example", "request": value}]}
        observations = {"schema": "openart-context-observations/2", "python_sources_before": {"context_solver.py": "source"},
                        "python_sources_after": {"context_solver.py": "source"}, "python_sources_unchanged": True,
                        "entrypoint_sha256": "source", "instances": [{"id": "example", "input_sha256": hashlib.sha256(payload).hexdigest(),
                        "stdout": json.dumps(response) + "\n", "stderr": "", "response": response,
                        "response_parse_error": None, "exit_code": 0, "timed_out": False,
                        "wall_ms": 20.0, "peak_rss_kib": 10000}]}
        before = copy.deepcopy(observations)
        result = check_observations(observations, corpus)
        self.assertTrue(result["passed"])
        self.assertFalse(result["official_large_quality_verified"])
        self.assertEqual(observations, before)
        observations["instances"][0]["exit_code"] = 1
        result = check_observations(observations, corpus)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["instances"]), 1)
        self.assertFalse(result["instances"][0]["passed"])
        observations["instances"] = []
        self.assertFalse(check_observations(observations, corpus)["passed"])


if __name__ == "__main__":
    unittest.main()
