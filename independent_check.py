"""Independent contract checks; never imports the selector or its model.

This reports legal recorded selections and exact small-instance quality. The
supplied resources do not include frozen large-instance comparison utilities,
so these checks cannot establish the official 99% quality threshold.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import pathlib
import re
import sys


class CheckError(ValueError):
    """A request fails an independently checked structural rule."""


def _need(condition, message):
    if not condition:
        raise CheckError(message)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", value) is not None


def _id_array(value, domain, limit, label):
    _need(isinstance(value, list) and len(value) <= limit, label + " must be a bounded array")
    _need(all(_identifier(x) for x in value), label + " contains a malformed ID")
    _need(len(value) == len(set(value)), label + " contains a duplicate")
    _need(set(value) <= domain, label + " contains an unknown reference")


def _closure(cards, seeds, relation):
    result, pending = set(), list(seeds)
    while pending:
        card_id = pending.pop()
        if card_id not in result:
            result.add(card_id)
            pending.extend(cards[card_id][relation])
    return result


def inspect_request(request):
    """Validate a decoded request and independently derive its active cards.

Raw byte count, duplicate JSON object keys and numeric lexical notation must
be tested at the CLI boundary; decoded corpus objects cannot retain those.
"""
    keys = {"versions", "cards", "features", "head", "budget", "mandatory", "contradictions"}
    _need(isinstance(request, dict) and set(request) == keys, "invalid request keys")
    versions, cards, weights = request["versions"], request["cards"], request["features"]
    _need(isinstance(versions, list) and 1 <= len(versions) <= 96, "invalid versions array")
    version_map = {}
    for version in versions:
        _need(isinstance(version, dict) and set(version) == {"id", "parents"}, "invalid version keys")
        _need(_identifier(version["id"]) and version["id"] not in version_map, "invalid/duplicate version ID")
        version_map[version["id"]] = version
    for version in versions:
        _id_array(version["parents"], set(version_map), 8, "parents")
    ancestors, visiting = {}, set()

    def ancestry(version_id):
        if version_id in ancestors:
            return ancestors[version_id]
        _need(version_id not in visiting, "version graph has a cycle")
        visiting.add(version_id)
        result = set()
        for parent in version_map[version_id]["parents"]:
            result.add(parent)
            result.update(ancestry(parent))
        visiting.remove(version_id)
        ancestors[version_id] = result
        return result

    for version_id in version_map:
        ancestry(version_id)
    _need(_identifier(request["head"]) and request["head"] in version_map, "unknown head")
    _need(_integer(request["budget"], 0, 8192), "invalid budget")
    _need(isinstance(weights, dict) and len(weights) <= 1024, "invalid features object")
    for feature, weight in weights.items():
        _need(_identifier(feature) and _integer(weight, 1, 1000), "invalid feature/weight")
    _need(isinstance(cards, list) and len(cards) <= 500, "invalid cards array")
    card_map = {}
    required = {"id", "version", "supersedes", "requires", "conflict_group", "tokens", "covers"}
    for card in cards:
        _need(isinstance(card, dict) and required <= set(card) <= required | {"text"}, "invalid card keys")
        _need(_identifier(card["id"]) and card["id"] not in card_map, "invalid/duplicate card ID")
        _need(_identifier(card["version"]) and card["version"] in version_map, "unknown card version")
        _need(card["conflict_group"] is None or _identifier(card["conflict_group"]), "invalid conflict group")
        _need(_integer(card["tokens"], 1, 512), "invalid token cost")
        if "text" in card:
            _need(isinstance(card["text"], str) and len(card["text"]) <= 2048, "invalid card text")
        _id_array(card["covers"], set(weights), 64, "covers")
        card_map[card["id"]] = card
    for card in cards:
        _id_array(card["requires"], set(card_map), 8, "requires")
        _id_array(card["supersedes"], set(card_map), 8, "supersedes")
        for older in card["supersedes"]:
            _need(card_map[older]["version"] in ancestors[card["version"]], "supersession is not strict ancestry")
    _id_array(request["mandatory"], set(card_map), 500, "mandatory")
    pairs = request["contradictions"]
    _need(isinstance(pairs, list) and len(pairs) <= 10000, "invalid contradictions array")
    pair_set = set()
    for pair in pairs:
        _need(isinstance(pair, list) and len(pair) == 2, "contradiction must have two endpoints")
        _need(all(_identifier(x) and x in card_map for x in pair), "unknown contradiction endpoint")
        _need(pair[0] != pair[1], "self contradiction")
        normalized = tuple(sorted(pair))
        _need(normalized not in pair_set, "duplicate unordered contradiction")
        pair_set.add(normalized)
    visible_versions = ancestors[request["head"]] | {request["head"]}
    visible = {card["id"] for card in cards if card["version"] in visible_versions}
    superseded = set()
    for card_id in visible:
        # Walk chains explicitly, independently of the selector's representation.
        for older in card_map[card_id]["supersedes"]:
            superseded.update(_closure(card_map, [older], "supersedes"))
    active = visible - superseded
    mandatory = _closure(card_map, request["mandatory"], "requires")
    return {"request": request, "cards": card_map, "weights": weights, "pairs": pair_set,
            "visible": visible, "active": active, "mandatory_closure": mandatory}


def _selection_errors(state, selected):
    cards, request = state["cards"], state["request"]
    errors = []
    if not selected <= set(cards):
        return ["selection contains unknown cards"]
    if not selected <= state["active"]:
        errors.append("selection contains inactive or invisible cards")
    if not set(request["mandatory"]) <= selected:
        errors.append("selection omits mandatory cards")
    groups = set()
    for card_id in selected:
        card = cards[card_id]
        if not set(card["requires"]) <= selected:
            errors.append("selection omits requirements of " + card_id)
        group = card["conflict_group"]
        if group is not None:
            if group in groups:
                errors.append("selection repeats a conflict group")
            groups.add(group)
    if any(a in selected and b in selected for a, b in state["pairs"]):
        errors.append("selection contains a contradiction pair")
    if sum(cards[x]["tokens"] for x in selected) > request["budget"]:
        errors.append("selection exceeds budget")
    return errors


def request_status(request):
    """Return invalid/infeasible/ok without consulting the implementation."""
    try:
        state = inspect_request(request)
    except (CheckError, TypeError, KeyError, RecursionError):
        return "invalid"
    return "infeasible" if _selection_errors(state, state["mandatory_closure"]) else "ok"


def _certificate_check(state, certificate, utility):
    try:
        _need(isinstance(certificate, dict) and set(certificate) == {"scale", "budget_price", "feature_prices"}, "invalid certificate keys")
        scale, price, prices = certificate["scale"], certificate["budget_price"], certificate["feature_prices"]
        _need(_integer(scale, 1, 1000000), "invalid certificate scale")
        _need(_integer(price, 0, scale * sum(state["weights"].values())), "invalid budget price")
        _need(isinstance(prices, dict) and set(prices) == set(state["weights"]), "invalid feature price keys")
        for feature, weight in state["weights"].items():
            _need(_integer(prices[feature], 0, weight * scale), "invalid feature price")
        upper = state["request"]["budget"] * price
        for card_id in state["active"]:
            card = state["cards"][card_id]
            upper += max(0, sum(prices[f] for f in card["covers"]) - price * card["tokens"])
        upper += sum(weight * scale - prices[f] for f, weight in state["weights"].items())
        return {"valid": True, "upper_numerator": upper, "scale": scale,
                "within_5_percent": 100 * upper <= 105 * utility * scale,
                "proves_99_percent_of_optimum": 99 * upper <= 100 * utility * scale,
                "proves_exact_optimum": upper // scale == utility}
    except (CheckError, TypeError, KeyError):
        return {"valid": False, "within_5_percent": False}


def check_response(request, response):
    """Return an independent report with core_errors and recomputed totals."""
    expected = request_status(request)
    result = {"expected_status": expected, "core_errors": [], "certificate": None}
    errors = result["core_errors"]
    if not isinstance(response, dict):
        errors.append("response must be a JSON object")
        return result
    if response.get("status") != expected:
        errors.append("response status does not match independently derived status")
        return result
    if expected != "ok":
        if set(response) != {"status", "reason"} or not isinstance(response.get("reason"), str) or not response["reason"]:
            errors.append("invalid non-ok response shape/reason")
        return result
    required = {"status", "selected", "tokens", "covered", "utility"}
    if not required <= set(response) <= required | {"certificate"}:
        errors.append("invalid ok response keys")
        return result
    for field in ("selected", "covered"):
        value = response[field]
        if not isinstance(value, list) or not all(_identifier(x) for x in value):
            errors.append(field + " must be an array of IDs")
        elif value != sorted(set(value)):
            errors.append(field + " must be distinct and sorted in ASCII order")
    if not _integer(response["tokens"], 0, 500 * 512) or not _integer(response["utility"], 0, 1024 * 1000):
        errors.append("invalid reported integer totals")
    if errors:
        return result
    state = inspect_request(request)
    selected = set(response["selected"])
    errors.extend(_selection_errors(state, selected))
    if not selected <= set(state["cards"]):
        return result
    covered = {f for card_id in selected for f in state["cards"][card_id]["covers"]}
    tokens = sum(state["cards"][x]["tokens"] for x in selected)
    utility = sum(state["weights"][f] for f in covered)
    result.update(tokens=tokens, utility=utility, covered=sorted(covered), active_cards=len(state["active"]))
    if response["tokens"] != tokens:
        errors.append("reported cost differs from selected-card cost")
    if response["covered"] != sorted(covered):
        errors.append("reported covered features differ from union")
    if response["utility"] != utility:
        errors.append("reported utility differs from distinct-feature utility")
    if "certificate" in response:
        # A bad optional certificate does not invalidate otherwise legal core fields.
        result["certificate"] = _certificate_check(state, response["certificate"], utility)
    return result


def certificate_profile(request):
    """Identify the public optional tight-gap profile independently."""
    state = inspect_request(request)
    if request["contradictions"] or any(c["requires"] or c["conflict_group"] is not None
                                         for c in request["cards"]):
        return False
    covered = set()
    for card_id in state["active"]:
        features = set(state["cards"][card_id]["covers"])
        if features & covered:
            return False
        covered.update(features)
    return True


def brute_force_optimum(request, max_optional=24):
    """Exact independent oracle; enumerate subsets outside mandatory closure.

Returns maximum utility, or None for invalid/infeasible requests. Its bound is
on optional cards, allowing a tractable 24-active test with many mandatory cards.
"""
    if request_status(request) != "ok":
        return None
    state = inspect_request(request)
    fixed = state["mandatory_closure"]
    optional = sorted(state["active"] - fixed)
    if len(optional) > max_optional:
        raise ValueError("independent exhaustive oracle optional-card limit exceeded")
    best = -1
    for bits in itertools.product((False, True), repeat=len(optional)):
        selected = fixed | {card for card, take in zip(optional, bits) if take}
        if _selection_errors(state, selected):
            continue
        covered = {f for card in selected for f in state["cards"][card]["covers"]}
        best = max(best, sum(state["weights"][f] for f in covered))
    return best


def _strict_json(raw):
    def pairs_hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite JSON constant " + value)

    return json.loads(raw, object_pairs_hook=pairs_hook, parse_constant=constant)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def check_observations(observations, corpus, corpus_bytes=None):
    """Check saved recorder observations without changing or executing them."""
    global_errors = []
    if observations.get("schema") != "openart-context-observations/2":
        global_errors.append("unexpected observations schema")
    if corpus.get("schema") != "openart-context-inputs/2":
        global_errors.append("unexpected corpus schema")
    if corpus_bytes is not None and observations.get("inputs_sha256") != _sha(corpus_bytes):
        global_errors.append("original corpus hash does not match")
    if observations.get("python_sources_before") != observations.get("python_sources_after") or observations.get("python_sources_unchanged") is not True:
        global_errors.append("recorded Python source inventory changed")
    inventory = observations.get("python_sources_before", {})
    if not isinstance(inventory, dict) or not inventory:
        global_errors.append("recorded Python source inventory is missing")
    else:
        if inventory.get("context_solver.py") != observations.get("entrypoint_sha256"):
            global_errors.append("entrypoint hash does not match its recorded source inventory")
        if "benchmark_runner.py" in inventory and inventory["benchmark_runner.py"] != observations.get("runner_sha256"):
            global_errors.append("runner hash does not match its recorded source inventory")
    requests = {item["id"]: item["request"] for item in corpus["instances"]}
    expected_large_count = sum(request_status(value) == "ok" and len(inspect_request(value)["active"]) >= 250
                               for value in requests.values())
    records, seen, large_times, large_rss = [], set(), [], []
    for observed in observations.get("instances", []):
        record_id = observed.get("id")
        if record_id not in requests or record_id in seen:
            global_errors.append("unknown or duplicate observation ID: " + str(record_id))
            continue
        seen.add(record_id)
        request = requests[record_id]
        payload = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        errors = []
        if observed.get("input_sha256") != _sha(payload):
            errors.append("canonical request hash differs")
        if observed.get("exit_code") != 0 or observed.get("timed_out") is not False:
            errors.append("solver failed or timed out")
        if not isinstance(observed.get("stdout"), str) or not observed["stdout"].endswith("\n"):
            errors.append("stdout lacks required final newline")
        try:
            response = _strict_json(observed.get("stdout", ""))
        except (ValueError, TypeError) as exc:
            errors.append("raw stdout is invalid JSON: " + str(exc))
            response = None
        checked = check_response(request, response)
        errors.extend(checked["core_errors"])
        if observed.get("response_parse_error") is not None or observed.get("response") != response:
            errors.append("recorder parsed response differs from raw stdout")
        wall, rss = observed.get("wall_ms"), observed.get("peak_rss_kib")
        if type(wall) not in (int, float) or not math.isfinite(wall) or not 0 <= wall <= 5000:
            errors.append("wall time exceeds the five-second envelope or is missing")
        if type(rss) is not int or not 0 <= rss <= 256 * 1024:
            errors.append("RSS exceeds the 256 MiB envelope or is missing")
        optimum = None
        tight_profile = False
        if checked["expected_status"] == "ok":
            state = inspect_request(request)
            tight_profile = certificate_profile(request)
            if len(state["active"]) <= 24:
                optimum = brute_force_optimum(request)
                if checked.get("utility") != optimum:
                    errors.append("small-instance utility is not the exact optimum")
            if len(state["active"]) >= 250 and type(wall) in (int, float) and type(rss) is int:
                large_times.append(wall)
                large_rss.append(rss)
        records.append({"id": record_id, "passed": not errors, "errors": errors,
                        "expected_status": checked["expected_status"], "utility": checked.get("utility"),
                        "tokens": checked.get("tokens"), "exact_optimum": optimum,
                        "certificate": checked["certificate"], "certificate_tight_gap_profile": tight_profile})
    missing = set(requests) - seen
    if missing:
        global_errors.append("missing observations: " + ", ".join(sorted(missing)))
    ordered_times = sorted(large_times)
    complete_large_measurements = len(large_times) == expected_large_count
    successful = [r for r in records if r["expected_status"] == "ok"]
    profiled = [r for r in successful if r["certificate_tight_gap_profile"]]
    certificate_valid = lambda r: bool(r["certificate"] and r["certificate"].get("valid"))
    return {"schema": "openart-context-independent-checks/1", "passed": not global_errors and all(r["passed"] for r in records),
            "global_errors": global_errors, "requests": len(requests), "instances": records,
            "large_workload": {"expected_count": expected_large_count, "count": len(large_times),
                               "complete_measurements": complete_large_measurements,
                               "p95_wall_ms": ordered_times[math.ceil(.95 * len(ordered_times)) - 1] if ordered_times and complete_large_measurements else None,
                               "maximum_rss_kib": max(large_rss) if large_rss and complete_large_measurements else None},
            "official_large_quality_verified": False,
            "certificates": {"expected_count": len(successful),
                             "valid_count": sum(certificate_valid(r) for r in successful),
                             "all_valid": not missing and not global_errors and all(certificate_valid(r) for r in successful),
                             "tight_gap_profile_count": len(profiled),
                             "tight_gap_profile_passed": not missing and not global_errors and all(
                                 certificate_valid(r) and r["passed"] and r["certificate"]["within_5_percent"] for r in profiled),
                             "proven_99_percent_count": sum(certificate_valid(r) and r["passed"] and
                                 r["certificate"]["proves_99_percent_of_optimum"] for r in successful),
                             "proven_exact_count": sum(certificate_valid(r) and r["passed"] and
                                 r["certificate"]["proves_exact_optimum"] for r in successful)},
            "quality_note": "Frozen independently verified large-instance comparison utilities are not supplied; legality and observations alone cannot establish official 99% quality.",
            "environment_note": "CPU/network/isolation restrictions and Linux/Python 3.11 must be verified separately; recorded metadata is not itself enforcement."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--output", help="Optional separate check report; recorder observations remain unchanged")
    args = parser.parse_args()
    corpus_bytes = pathlib.Path(args.inputs).read_bytes()
    corpus = _strict_json(corpus_bytes)
    observations = _strict_json(pathlib.Path(args.observations).read_bytes())
    report = check_observations(observations, corpus, corpus_bytes)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        if pathlib.Path(args.output).resolve() in {pathlib.Path(args.observations).resolve(), pathlib.Path(args.inputs).resolve()}:
            parser.error("check report must not overwrite observations or corpus")
        pathlib.Path(args.output).write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
