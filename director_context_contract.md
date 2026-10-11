# Director context request contract

## Process boundary

Run `python context_solver.py`. Process one UTF-8 JSON object from stdin and write one UTF-8 JSON response followed by a newline to stdout. Exit 0 for a well-formed response, including `invalid` and `infeasible`. Send diagnostics to stderr; do not write a second JSON object or progress text to stdout. The program must work from an arbitrary writable working directory after clean extraction. No persistent state is needed between invocations.

Behavior depends on the request. Identifiers, array order, contradiction-pair orientation, file paths and benchmark IDs carry no semantic priority. Equivalent inputs may choose different tied optima, but use the same legality and utility rules.

## Input

All seven top-level keys are required. An empty session has this shape:

```json
{
  "versions": [{"id": "v0", "parents": []}],
  "cards": [],
  "features": {},
  "head": "v0",
  "budget": 0,
  "mandatory": [],
  "contradictions": []
}
```

Each card has these required keys and may additionally have `text`:

```json
{
  "id": "card7",
  "version": "v2",
  "supersedes": ["card2"],
  "requires": ["card4"],
  "conflict_group": "wardrobe",
  "tokens": 18,
  "covers": ["costume", "continuity"],
  "text": "A human-readable description."
}
```

An identifier is an ASCII string of 1–64 characters containing only letters, digits, `.`, `_`, `:` or `-`. Version, card, feature and conflict-group IDs occupy separate namespaces. Unknown object keys are invalid, including keys within version/card objects. Duplicate JSON object keys are invalid.

Types and limits:

| Field | Public requirement |
|---|---|
| Raw request | At most 16 MiB of UTF-8 input bytes, including whitespace; one JSON object |
| `versions` | 1–96 objects, each with exactly `id` and `parents`; unique IDs |
| `parents` | 0–8 distinct existing version IDs; version graph acyclic |
| `cards` | 0–500 card objects; unique card IDs |
| `version` | An existing version ID |
| `supersedes`, `requires` | Each an array of 0–8 distinct existing card IDs |
| `conflict_group` | Null or an identifier |
| `tokens` | Integer 1–512 |
| `covers` | 0–64 distinct existing feature IDs per card |
| `text` | Optional string of at most 2,048 Unicode characters; empty allowed |
| `features` | Object with 0–1,024 feature IDs, each mapped to integer weight 1–1,000 |
| `head` | An existing version ID |
| `budget` | Integer 0–8,192 |
| `mandatory` | Array of distinct existing card IDs; empty allowed |
| `contradictions` | 0–10,000 distinct unordered pairs of existing card IDs, as defined below |

Integer fields require JSON integer notation without a fractional or exponent part. Booleans are not integers. Non-finite numbers are invalid. Null is permitted only as `conflict_group`. All arrays/objects must have the stated types. Missing keys, unknown references, duplicate IDs/array entries, or graph/range/type violations make the request invalid. Input arrays need not be topologically sorted. `text` has no effect on the selection semantics or objective; do not retokenize it.

Requirements may form cycles, including a self-requirement. Their entire closure must be selected together. A card cannot supersede itself; it may supersede another card only if the older card's version is a **strict ancestor** of its own version. Being earlier in an array is not ancestry. Superseding within one version or across unrelated branches is invalid.

## Pairwise contradictions

`contradictions` is an array of two-element arrays, for example:

```json
"contradictions": [["coat", "sleeve_detail"], ["moon", "amber_arrival"]]
```

Each endpoint must be a distinct, existing card ID. A pair forbids selecting both endpoints. The relation is symmetric: `["a","b"]` and `["b","a"]` mean the same pair. Supplying both orientations, or repeating either orientation, is a duplicate and makes the request invalid. A self-pair, wrong pair length, non-array pair or unknown endpoint is invalid.

Contradictions are **not transitive**. Pairs `(a,b)` and `(b,c)` do not prohibit selecting `(a,c)` unless that pair is separately supplied. Pairs may connect cards within one conflict group, across groups, or with null groups; a redundant pair within a group is valid. There is no additional maximum degree, component size or required graph shape within the public limits.

Pairs referring to inactive or invisible cards remain structurally valid. They restrict selection only when both exact endpoints would be selected. They are never transferred to a newer card, inferred from text, or resolved by inventing a replacement. A contradiction inside a requirement closure makes that closure unusable; it does not make the input schema invalid. If the mandatory closure contains such a pair, return `infeasible`.

## Visibility and revisions

A version is visible from `head` when it is the head itself or a transitive ancestor. A card is visible when its version is visible.

A visible card is **active** if no other visible card supersedes it directly or through a chain. A visible superseder deactivates predecessors even if it has itself been superseded. An invisible card never suppresses a visible card. Requested branch ancestry, rather than timestamps or names, determines visibility.

Requirements and contradictions do not change visibility or revision semantics. A requirement on an inactive/invisible card refers to that exact card; do not redirect it to a successor. Selecting its dependent would require selecting the unusable card and is therefore impossible.

The **active-card count** used for quality and efficiency scopes is the count after visibility and supersession, before removing cards whose closures cannot be selected. It is recomputable from the request, independent of the implementation's search representation.

## Legal selected set

For selected card IDs `S`, all conditions must hold:

1. Every selected card is active and visible from `head`.
2. Every mandatory card is selected.
3. Every selected card's complete transitive `requires` closure is selected. Shared requirements are selected and charged once; cycles are taken together.
4. At most one selected card belongs to each nonnull `conflict_group`. Null-group cards impose no group exclusivity.
5. No supplied contradiction pair has both endpoints selected.
6. The sum of `tokens` over distinct selected cards is no greater than `budget`. Equality is allowed; overlap does not discount costs.

If the mandatory closure violates these constraints, no legal set exists: return `infeasible`. Otherwise that closure is itself a legal set. Empty mandatory allows an empty selection, including at budget zero. Do not claim infeasibility because a heuristic failed to find a valuable set.

## Objective and response

Covered features are the union of `covers` from selected cards. Utility is the sum of their weights, counting a repeated feature once. Neither selected-card count nor budget utilization is an independent reward.

For a valid feasible request, return:

```json
{"status":"ok","selected":[],"tokens":0,"covered":[],"utility":0}
```

Fill in actual selections/totals. `selected` and `covered` contain distinct IDs sorted in ascending ASCII order. `tokens` and `utility` are nonnegative integers, not booleans. Tied maximum-utility sets need not minimize cost or prefer particular IDs. The only permitted extra response field is `certificate` below.

Return exactly one of these shapes for other statuses:

```json
{"status":"invalid","reason":"A nonempty explanation."}
{"status":"infeasible","reason":"A nonempty explanation."}
```

Invalid-input validation takes precedence over infeasibility. Precise explanation wording is not prescribed. Reported totals are assertions independently recomputable from selected cards, not substitutes for them.

## Optional utility upper-bound certificate

An `ok` response may contain `certificate` with exactly:

- `scale`: integer 1–1,000,000.
- `budget_price`: nonnegative integer no greater than `scale` times the sum of all feature weights.
- `feature_prices`: object with exactly every feature ID, each mapped to a nonnegative integer no greater than that feature's weight times `scale`.

Booleans fail these integer types. With `p = budget_price`, `s = scale` and `z[f] = feature_prices[f]`, recompute with exact integers:

```text
U = budget * p
    + sum over every active visible card c of
        max(0, sum(z[f] for f in c.covers) - p * c.tokens)
    + sum over every feature f of (features[f] * s - z[f])
```

`U / s` is an upper bound. This relaxation ignores requirements, conflict groups, contradiction pairs and mandatory choices; it can be loose. A bound at most 5% above achieved legal utility satisfies `100 * U <= 105 * utility * s`. Valid prices alone do not establish that gap.

The optional tight-gap capability is assessed on requests where every card has an empty `requires` array and null `conflict_group`, the request's `contradictions` array is empty, and no feature is covered by more than one active card. The requirement/group conditions apply to all cards, including inactive and invisible ones; coverage uniqueness applies only to active cards. This profile is identifiable from the request and is not a promise that every request under that profile has a tight integral gap. The assessed instances must satisfy the stated gap using an actual certificate. No tight gap is demanded on arbitrary contradiction graphs.

Core selection is judged independently. Missing/malformed/loose certificates fail the corresponding optional capability, not otherwise legal core fields. Unrelated response fields still violate the core schema. No certificate is permitted on `invalid` or `infeasible` responses.

## Execution, quality and observations

The envelope is Linux, 2 vCPUs, Python 3.11, standard-library runtime dependencies, no network, 5 seconds wall time per invocation including startup, and 256 MiB peak RSS. The two-vCPU envelope concerns effective CPU capacity (an actual cgroup quota or restricted CPU affinity), rather than the count of logical CPUs visible to the container. Preserve the recorder's CPU-capacity metadata and describe any additional restriction. A larger visible CPU count alone does not establish a larger effective quota; an unverified estimate is not a measured two-vCPU result. Every input structure is bounded above; there is no hidden promise about degree, topology, connected components, pair ordering or coverage shape.

Expected quality is exact maximum utility for at most 24 active cards. For larger assessed requests, target at least 99% of a frozen independently verified **feasible** comparison utility: `100 * utility >= 99 * comparison_utility`. The comparison is a legal lower bound, not a requirement to prove the global optimum. A higher legal utility is welcome. The larger workload includes 250–500 active cards; requests can couple all selection constraints. Integer arithmetic determines thresholds, without ratio rounding. The optional 99.5% target uses `200 * utility >= 199 * comparison_utility`.

Efficiency uses every valid feasible request with at least 250 active cards in the measured corpus, including equivalent repeats/certificate requests. p95 is nearest rank `ceil(0.95 * n)` among sorted measured times; peak memory is the maximum measured RSS on the same workload. The optional limits are 750 ms p95 and 64 MiB maximum RSS, with retained main quality. If equivalent requests achieve utilities `L <= H`, the optional consistency threshold is `200 * (H - L) <= H`, in addition to legality and retained main quality.

The corpus has exactly `schema: "openart-context-inputs/2"` and `instances`, an array of `{ "id": ..., "request": ... }`. IDs match observations only; they are not passed to the solver. The supplied recorder produces `openart-context-observations/2`: raw stdout/stderr, parsed response, exit/timeout/parse status, wall time, RSS, environment, input/runner/entry-point hashes and before/after SHA-256 inventories of delivered Python sources. Preserve this recorder and its output. Its implementation/data contain no grading answers or case-purpose labels. Python-source inventory confirms observed source integrity; it does not itself prove result correctness or replace an independently isolated execution.

Each finite assessed quality group uses an all-member gate: a missing, malformed, timed-out, illegal or wrongly infeasible response fails its group rather than disappearing into a favorable mean. Quality comparisons inspect actual recorded selected sets and observed five-second/256MiB limits. Verified deployment-environment evidence is assessed separately; disclose unavailable environment verification instead of presenting an estimate as an actual run.
