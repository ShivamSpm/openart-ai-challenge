"""Strict public-request validation and bit-mask selection semantics.

Only supplied metadata determines legality. Card text is retained unchanged and
is never interpreted or retokenized.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


MAX_REQUEST_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z", re.ASCII)
_REQUEST_KEYS = {
    "versions", "cards", "features", "head", "budget", "mandatory",
    "contradictions",
}
_CARD_KEYS = {
    "id", "version", "supersedes", "requires", "conflict_group", "tokens",
    "covers",
}


class InvalidRequest(ValueError):
    """The input violates the public schema or graph rules."""


class InfeasibleRequest(ValueError):
    """The validated input has no legal mandatory closure."""


def _invalid(message: str) -> None:
    raise InvalidRequest(message)


def _identifier(value: Any, where: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        _invalid(f"{where} must be an ASCII identifier of 1–64 permitted characters")
    return value


def _integer(value: Any, low: int, high: int, where: str) -> int:
    if type(value) is not int or not low <= value <= high:
        _invalid(f"{where} must be an integer from {low} through {high}")
    return value


def _object(value: Any, where: str) -> dict:
    if type(value) is not dict:
        _invalid(f"{where} must be an object")
    return value


def _array(value: Any, maximum: int, where: str, minimum: int = 0) -> list:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        _invalid(f"{where} must be an array with {minimum}–{maximum} entries")
    return value


def _keys(value: dict, required: set[str], where: str,
          optional: set[str] | None = None) -> None:
    actual = set(value)
    allowed = required | (optional or set())
    if not required <= actual or not actual <= allowed:
        _invalid(f"{where} has missing or unknown keys")


def _identifier_array(value: Any, maximum: int, where: str) -> list[str]:
    values = _array(value, maximum, where)
    seen: set[str] = set()
    for position, item in enumerate(values):
        item = _identifier(item, f"{where}[{position}]")
        if item in seen:
            _invalid(f"{where} contains duplicate identifier {item}")
        seen.add(item)
    return values


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_noninteger(value: str) -> None:
    _invalid(f"JSON fractional, exponent, or non-finite number is forbidden: {value}")


def _preflight(raw: bytes) -> None:
    """Bound decoder allocations using consequences of the public schema.

    The schema admits at most 70,112 unquoted commas plus colons, nesting depth
    four, and 2,048 characters in its longest string. A character needs at most
    twelve raw JSON bytes (an escaped UTF-16 surrogate pair). The deliberately
    generous ceilings below cannot reject a schema-valid request. They prevent
    a small but structurally invalid JSON stream from allocating millions of
    Python containers before ordinary validation can inspect it.

    This constant-memory lexical scan tracks quoted/escaped bytes, not values.
    It does not decode escapes, infer schema fields, or replace JSON parsing.
    Whitespace counts only toward the separate 16 MiB raw-input limit.
    """
    quoted = False
    escaped = False
    string_bytes = 0
    delimiters = 0
    depth = 0
    for byte in raw:
        if quoted:
            if byte == 34 and not escaped:  # unescaped closing double quote
                quoted = False
                continue
            string_bytes += 1
            if string_bytes > 24576:
                _invalid("quoted JSON content exceeds the public string-size envelope")
            if escaped:
                escaped = False
            elif byte == 92:  # backslash escapes precisely the following byte
                escaped = True
            continue
        if byte == 34:
            quoted = True
            escaped = False
            string_bytes = 0
        elif byte == 44 or byte == 58:  # commas and colons outside strings
            delimiters += 1
            if delimiters > 100000:
                _invalid("JSON structure exceeds the public cardinality envelope")
        elif byte == 91 or byte == 123:  # opening bracket or brace
            depth += 1
            if depth > 32:
                _invalid("JSON nesting exceeds the public schema envelope")
        elif byte == 93 or byte == 125:
            depth -= 1
            if depth < 0:
                _invalid("JSON contains an unmatched closing bracket or brace")


def parse_request(raw: bytes) -> dict:
    """Parse one UTF-8 JSON object and validate all public input rules.

    A raw-byte cap must include whitespace. The CLI should read at most one
    byte beyond this limit so an oversized stream cannot exhaust its memory.
    """
    if type(raw) is not bytes:
        _invalid("request input must be UTF-8 bytes")
    if len(raw) > MAX_REQUEST_BYTES:
        _invalid("request exceeds 16 MiB of UTF-8 input bytes")
    _preflight(raw)
    try:
        source = raw.decode("utf-8", errors="strict")
        request = json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_float=_reject_noninteger,
            parse_constant=_reject_noninteger,
        )
    except InvalidRequest:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise InvalidRequest(f"invalid UTF-8 JSON request: {exc}") from None
    _validate(request)
    return request


def _validate(request: Any) -> dict[str, int]:
    """Validate everything before deriving any feasibility judgment.

    Return each version's strict-ancestor mask, indexed by sorted version IDs.
    """
    request = _object(request, "request")
    _keys(request, _REQUEST_KEYS, "request")
    versions = _array(request["versions"], 96, "versions", minimum=1)
    cards = _array(request["cards"], 500, "cards")
    features = _object(request["features"], "features")
    if len(features) > 1024:
        _invalid("features has more than 1,024 entries")
    for feature, weight in features.items():
        _identifier(feature, "feature ID")
        _integer(weight, 1, 1000, f"features[{feature}]")
    _identifier(request["head"], "head")
    _integer(request["budget"], 0, 8192, "budget")

    version_map = {}
    for position, version in enumerate(versions):
        where = f"versions[{position}]"
        version = _object(version, where)
        _keys(version, {"id", "parents"}, where)
        version_id = _identifier(version["id"], f"{where}.id")
        if version_id in version_map:
            _invalid(f"duplicate version ID: {version_id}")
        _identifier_array(version["parents"], 8, f"{where}.parents")
        version_map[version_id] = version
    if request["head"] not in version_map:
        _invalid("head refers to an unknown version")
    for version_id, version in version_map.items():
        for parent in version["parents"]:
            if parent not in version_map:
                _invalid(f"version {version_id} has unknown parent {parent}")

    version_indices = {value: i for i, value in enumerate(sorted(version_map))}
    ancestors: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(version_id: str) -> int:
        if version_id in ancestors:
            return ancestors[version_id]
        if version_id in visiting:
            _invalid("version graph contains a cycle")
        visiting.add(version_id)
        mask = 0
        for parent in version_map[version_id]["parents"]:
            mask |= (1 << version_indices[parent]) | visit(parent)
        visiting.remove(version_id)
        ancestors[version_id] = mask
        return mask

    for version_id in version_map:
        visit(version_id)

    card_map = {}
    for position, card in enumerate(cards):
        where = f"cards[{position}]"
        card = _object(card, where)
        _keys(card, _CARD_KEYS, where, {"text"})
        card_id = _identifier(card["id"], f"{where}.id")
        if card_id in card_map:
            _invalid(f"duplicate card ID: {card_id}")
        version_id = _identifier(card["version"], f"{where}.version")
        if version_id not in version_map:
            _invalid(f"card {card_id} refers to an unknown version")
        _identifier_array(card["supersedes"], 8, f"{where}.supersedes")
        _identifier_array(card["requires"], 8, f"{where}.requires")
        _identifier_array(card["covers"], 64, f"{where}.covers")
        if card["conflict_group"] is not None:
            _identifier(card["conflict_group"], f"{where}.conflict_group")
        _integer(card["tokens"], 1, 512, f"{where}.tokens")
        if "text" in card:
            if type(card["text"]) is not str or len(card["text"]) > 2048:
                _invalid(f"{where}.text must be a string of at most 2,048 characters")
        for feature in card["covers"]:
            if feature not in features:
                _invalid(f"card {card_id} covers unknown feature {feature}")
        card_map[card_id] = card

    for card_id, card in card_map.items():
        for relation in ("requires", "supersedes"):
            for target in card[relation]:
                if target not in card_map:
                    _invalid(f"card {card_id} has unknown {relation} target {target}")
        for target in card["supersedes"]:
            older_version = card_map[target]["version"]
            if not ancestors[card["version"]] & (1 << version_indices[older_version]):
                _invalid(f"card {card_id} may supersede {target} only in a strict ancestor version")

    mandatory = _identifier_array(request["mandatory"], 500, "mandatory")
    for card_id in mandatory:
        if card_id not in card_map:
            _invalid(f"unknown mandatory card: {card_id}")

    pairs = _array(request["contradictions"], 10000, "contradictions")
    seen_pairs: set[tuple[str, str]] = set()
    for position, pair in enumerate(pairs):
        where = f"contradictions[{position}]"
        pair = _array(pair, 2, where, minimum=2)
        a = _identifier(pair[0], f"{where}[0]")
        b = _identifier(pair[1], f"{where}[1]")
        if a == b:
            _invalid(f"{where} cannot contradict a card with itself")
        if a not in card_map or b not in card_map:
            _invalid(f"{where} has an unknown card endpoint")
        unordered_pair = tuple(sorted((a, b)))
        if unordered_pair in seen_pairs:
            _invalid(f"duplicate unordered contradiction pair: {a}, {b}")
        seen_pairs.add(unordered_pair)
    return ancestors


@dataclass(frozen=True)
class Model:
    """All-card bit positions follow ascending ASCII card IDs."""

    card_ids: tuple[str, ...]
    feature_ids: tuple[str, ...]
    feature_weights: tuple[int, ...]
    cards: tuple[dict, ...]
    costs: tuple[int, ...]
    cover_masks: tuple[int, ...]
    closures: tuple[int, ...]
    conflicts: tuple[int, ...]
    active_mask: int
    usable_mask: int
    mandatory_mask: int
    active_count: int
    budget: int
    request: dict

    def cost(self, mask: int) -> int:
        total = 0
        while mask:
            bit = mask & -mask
            total += self.costs[bit.bit_length() - 1]
            mask ^= bit
        return total

    def coverage(self, mask: int) -> int:
        result = 0
        while mask:
            bit = mask & -mask
            result |= self.cover_masks[bit.bit_length() - 1]
            mask ^= bit
        return result

    def utility(self, feature_mask: int) -> int:
        total = 0
        while feature_mask:
            bit = feature_mask & -feature_mask
            total += self.feature_weights[bit.bit_length() - 1]
            feature_mask ^= bit
        return total

    def legal(self, mask: int) -> bool:
        if type(mask) is not int or mask < 0 or mask & ~self.active_mask:
            return False
        if self.mandatory_mask & ~mask or self.cost(mask) > self.budget:
            return False
        rest = mask
        while rest:
            bit = rest & -rest
            index = bit.bit_length() - 1
            if self.closures[index] & ~mask or self.conflicts[index] & mask:
                return False
            rest ^= bit
        return True


def prepare(request: dict) -> Model:
    """Build exact metadata semantics; reject only mandatory infeasibility.

    Validation also runs here so callers constructing a request dictionary
    directly receive the same precedence of invalid over infeasible.
    """
    ancestors = _validate(request)
    card_map = {card["id"]: card for card in request["cards"]}
    card_ids = tuple(sorted(card_map))
    cards = tuple(card_map[card_id] for card_id in card_ids)
    card_indices = {card_id: i for i, card_id in enumerate(card_ids)}
    feature_ids = tuple(sorted(request["features"]))
    feature_indices = {feature_id: i for i, feature_id in enumerate(feature_ids)}
    version_indices = {version_id: i for i, version_id in enumerate(sorted(ancestors))}
    visible_versions = ancestors[request["head"]] | (1 << version_indices[request["head"]])
    active_mask = 0
    superseded_mask = 0
    for i, card in enumerate(cards):
        if visible_versions & (1 << version_indices[card["version"]]):
            active_mask |= 1 << i
            for older in card["supersedes"]:
                superseded_mask |= 1 << card_indices[older]
    active_mask &= ~superseded_mask

    costs = tuple(card["tokens"] for card in cards)
    cover_masks = []
    closures = []
    groups: dict[str, int] = {}
    for i, card in enumerate(cards):
        feature_mask = 0
        for feature in card["covers"]:
            feature_mask |= 1 << feature_indices[feature]
        cover_masks.append(feature_mask)
        closure = 1 << i
        for required in card["requires"]:
            closure |= 1 << card_indices[required]
        closures.append(closure)
        group = card["conflict_group"]
        if group is not None:
            groups[group] = groups.get(group, 0) | (1 << i)

    # Bitset Warshall computes transitive reachability, including requirement
    # cycles and self-requirements, without assuming a topological order.
    for k in range(len(cards)):
        bit = 1 << k
        reachable = closures[k]
        for i in range(len(cards)):
            if closures[i] & bit:
                closures[i] |= reachable

    conflicts = [0] * len(cards)
    for i, card in enumerate(cards):
        group = card["conflict_group"]
        if group is not None:
            conflicts[i] = groups[group] & ~(1 << i)
    for a, b in request["contradictions"]:
        i, j = card_indices[a], card_indices[b]
        conflicts[i] |= 1 << j
        conflicts[j] |= 1 << i

    mandatory_mask = 0
    for card_id in request["mandatory"]:
        mandatory_mask |= closures[card_indices[card_id]]
    usable_mask = 0
    for i, closure in enumerate(closures):
        if closure & ~active_mask:
            continue
        remaining = closure
        closure_cost = 0
        compatible = True
        while remaining:
            bit = remaining & -remaining
            j = bit.bit_length() - 1
            closure_cost += costs[j]
            if conflicts[j] & closure:
                compatible = False
                break
            remaining ^= bit
        if compatible and closure_cost <= request["budget"]:
            usable_mask |= 1 << i

    model = Model(
        card_ids=card_ids,
        feature_ids=feature_ids,
        feature_weights=tuple(request["features"][feature] for feature in feature_ids),
        cards=cards,
        costs=costs,
        cover_masks=tuple(cover_masks),
        closures=tuple(closures),
        conflicts=tuple(conflicts),
        active_mask=active_mask,
        usable_mask=usable_mask,
        mandatory_mask=mandatory_mask,
        active_count=active_mask.bit_count(),
        budget=request["budget"],
        request=request,
    )
    if not model.legal(mandatory_mask):
        if mandatory_mask & ~active_mask:
            reason = "mandatory requirement closure contains an inactive or invisible card"
        elif model.cost(mandatory_mask) > model.budget:
            reason = "mandatory requirement closure exceeds the token budget"
        else:
            reason = "mandatory requirement closure contains mutually incompatible cards"
        raise InfeasibleRequest(reason)
    return model
