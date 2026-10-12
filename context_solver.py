"""One-request, standard-library Director context selector.

Only the public response is written to stdout. Search and validation live in
local modules; neither module reads benchmark files or remembers earlier turns.
"""
from __future__ import annotations

import time

_BOOT = time.perf_counter()

import json
import sys

from context_model import InvalidRequest, InfeasibleRequest, parse_request, prepare
from context_search import LAST_STATS, select
from context_certificate import make_certificate

MAX_INPUT_BYTES = 16 * 1024 * 1024
SEARCH_SECONDS = 4.35


def solve(request: dict, deadline: float | None = None) -> dict:
    """Return the public response for a decoded request.

    Raw-JSON lexical constraints (duplicate keys, integer notation, byte limit)
    are additionally enforced by parse_request at the CLI boundary.
    """
    if deadline is None:
        deadline = time.perf_counter() + SEARCH_SECONDS
    try:
        model = prepare(request)
    except InvalidRequest as exc:
        return {"status": "invalid", "reason": str(exc)}
    except InfeasibleRequest as exc:
        return {"status": "infeasible", "reason": str(exc)}
    chosen = select(model, deadline)
    if not model.legal(chosen):
        raise RuntimeError("Internal search error: selected set is illegal")
    covered = model.coverage(chosen)
    utility = model.utility(covered)
    began_certificate = time.perf_counter()
    certificate, upper = make_certificate(model, utility, min(deadline + 0.15, began_certificate + 0.10))
    LAST_STATS.update(certificate_ms=round((time.perf_counter() - began_certificate) * 1000, 3),
                      certificate_upper_numerator=upper, certificate_scale=certificate["scale"],
                      certificate_within_5_percent=100 * upper <= 105 * utility * certificate["scale"],
                      certificate_proves_99_percent=99 * upper <= 100 * utility * certificate["scale"])
    return {
        "status": "ok",
        "selected": [name for i, name in enumerate(model.card_ids) if chosen >> i & 1],
        "tokens": model.cost(chosen),
        "covered": [name for i, name in enumerate(model.feature_ids) if covered >> i & 1],
        "utility": utility,
        "certificate": certificate,
    }


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        request = parse_request(raw)
    except InvalidRequest as exc:
        response = {"status": "invalid", "reason": str(exc)}
    else:
        response = solve(request, _BOOT + SEARCH_SECONDS)
    diagnostic = {
        "status": response["status"],
        "elapsed_ms": round((time.perf_counter() - _BOOT) * 1000, 3),
    }
    if response["status"] == "ok":
        diagnostic.update(selected_cards=len(response["selected"]),
                          tokens=response["tokens"], utility=response["utility"])
        diagnostic.update(LAST_STATS)
    sys.stderr.write(json.dumps(diagnostic, ensure_ascii=True) + "\n")
    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=True,
                                       allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
