# Director context picker

A stateless Python 3.11 standard-library CLI selects supplied memory cards under
the public Director contract. It does not extract memories, interpret card text,
retokenize, adjust feature weights, or call an LLM. The five supplied resource
files are preserved byte-for-byte. Read `director_context_contract.md` for the
authoritative semantics and limits.

## Clean extraction and invocation

The final measured archive is named `director-context.zip`. Required files are
at its root, without an enclosing package directory. A source-only handoff is
named `director-context-source.zip` and is not a completed submission.

On Linux with Python 3.11:

```sh
python3.11 -m zipfile -e director-context.zip extracted
cd extracted
python3.11 context_solver.py < director_session.json
python3.11 -m unittest discover -s tests -v
```

Expected sample utility is 61, with cost 77 or 79 depending on the tied lighting
choice. This is a manually understood example, not benchmark evidence.

The entrypoint also works from another writable working directory:

```sh
task_package=$(pwd)
cd /tmp
python3.11 "$task_package/context_solver.py" < "$task_package/director_session.json"
```

Send exactly one UTF-8 JSON request on stdin. One newline-terminated public JSON
response goes to stdout; diagnostic JSON goes to stderr. `ok`, `invalid`, and
`infeasible` responses all exit zero. Unexpected internal errors exit nonzero.
Each invocation is independent and does not need persistent files.

Windows development can use `py -3.12` for local tests, but a Windows/Python 3.12
run is not evidence of the contract's Linux/Python 3.11 execution requirements.

## Method

1. Validate raw UTF-8, duplicate object keys, JSON integer notation, every schema
   limit, identifiers, exact references, version acyclicity and strict ancestry
   for supersession. Structural validation precedes feasibility. A constant-space
   lexical preflight rejects structures larger/deeper than any valid schema
   instance before the JSON decoder can allocate unbounded collections.
2. Determine visible versions from `head`; deactivate cards superseded by any
   visible card. No invisible replacement suppresses a visible predecessor.
3. Represent card sets and coverage with Python integer bitsets. Compute complete
   requirement closures (cycles included), combine group exclusions with exact
   symmetric contradiction pairs, and check the mandatory closure. Optional
   unusable closures are removed only after recording the active-card count.
4. Search for maximum weighted union coverage, charging shared requirements once.
   At most 24 active cards use exact branch-and-bound, without a heuristic search
   cutoff. Larger requests also use this path when at most 24 eligible optional
   decisions remain. Structurally independent cards with disjoint coverage use exact
   budget-indexed knapsack. General larger requests use bounded closure-aware
   greedy starts, feasible removal/refill improvements and fixed-seed closure
   exchanges. Structural card/feature refinement defines candidate order without
   semantic priority from identifier spelling. Unresolved structural ties may
   use IDs. Benchmark labels and card text do not define scoring rules. Only
   supplied feature weights determine utility.
5. Recheck legality and independently compute response totals from the selected
   cards. General large-request search stops before the five-second process limit,
   preserving a legal incumbent. An unsuccessful search never implies infeasibility.

6. Emit the contract's integer-price certificate on every `ok` response. The
   bound includes **every active visible card**, including unusable ones, and
   prices for every declared feature. Invalid/infeasible responses omit it.

## Recomputable quality bounds

For certificate scale `s`, budget price `p`, and feature prices `z`, independently
recompute the following using the original request and active-card semantics:

```text
U = budget*p
    + sum_active_cards max(0, sum_covered_features z[f] - p*tokens)
    + sum_all_features (weight[f]*s - z[f])
```

The legal returned utility is a lower bound on the optimum, while `U/s` is an
upper bound. Since utility is integral, `floor(U/s)` is also a valid upper bound.
For any legal selection, its scaled feature value is at most its selected-card
price sum plus all feature slack. Subtract and add `p` times selected cost;
replace cost by budget and each card surplus by its positive part, summed over
all active cards. This gives exactly the expression above. Requirements,
mandatory choices, groups and contradictions are relaxed, so they cannot make
the bound invalid, but they can make it loose.

Disjoint coverage has an analytic fractional-knapsack dual: set feature prices
to their full weights and find the budget-price breakpoint by exact rational
sorting. Evaluate both adjacent integer prices at scale 1,000,000 to preserve
validity after rounding. Overlapping coverage also tries up to 80 projected
subgradient updates in a 100 ms search window, retaining the best prices as
evaluated with exact integers. The search window excludes initial bound setup
and is additionally limited by the solver's remaining deadline allowance.
Optimization uses floating point only to propose new prices; certificate
validity and threshold comparisons use exact integers. No optimization success
is assumed: the fallback bound is total reachable feature weight.

`independent_check.py` recomputes each bound without importing the solver and
reports these separate properties:

- Valid prices and upper numerator/scale.
- The optional 5% gap: `100*U <= 105*utility*s`.
- A proof of at least 99% of the true optimum: `99*U <= 100*utility*s`.
- A proof of exact optimum when `floor(U/s) == utility`.

A 5% upper-bound gap does **not** establish 99% quality. A loose certificate
does not establish that the selection is poor either. The contract's relaxation
can have an integrality gap even for disjoint coverage, so tightness cannot be
guaranteed on every permitted input. The workflow independently gates valid
certificates for every successful request and the 5% gap on every public request
matching the specified optional profile. Other optional capabilities remain
unclaimed. The recorder and its observations are unchanged.

## Tests and independent evaluation

The local regression command is:

```sh
python3.11 -m unittest discover -s tests -v
```

Tests include malformed raw input, schema limits, branching and supersession,
requirements and cycles, shared costs, exact contradictions, mandatory feasibility,
the sample, and small generated cases checked with a separate exhaustive oracle.
Certificate regressions cover fractional rounding, legitimate loose bounds,
unusable active cards, inactive-card profile conditions, missing/invalid prices,
expired price-search deadlines and generated overlapping-coverage bounds checked
against exhaustive optima.
The independent checker uses sets and its own validation/state logic; it does
not import the solver's model or search implementation.

For a portable fresh corpus smoke run (not the official recorder):

```sh
python3.11 local_smoke.py
```

For actual original-recorder observations on Linux:

```sh
python3.11 benchmark_runner.py --solution context_solver.py \
  --inputs benchmark_inputs.json --output benchmark_results.json \
  > benchmark_run.log 2>&1
python3.11 independent_check.py --inputs benchmark_inputs.json \
  --observations benchmark_results.json --output validation_report.json
```

The recorder measures and retains every request, including unsuccessful requests;
it is not a legality checker or sandbox. Do not edit `benchmark_results.json`,
`benchmark_run.log`, the supplied corpus, or the supplied recorder. The checker
writes additional findings separately. There are no supplied frozen comparison
utilities, so passing the recorder or legality checks alone does not establish
official large-instance 99% quality. A sufficiently tight certificate proves
99% of the true optimum for its individual request; the independent report
counts those proofs separately. Exact small-case utility and achieved large-case
utility are checked/reported separately.

## Remote Linux workflow and measurement environment

The included `.github/workflows/benchmark.yml` has a manual `workflow_dispatch`
trigger. Put the source files at the root of an authorized GitHub
repository, including `.github/workflows/benchmark.yml`, then run **Director
context benchmark** from the Actions tab. It needs no personal secrets.

The workflow uses `ubuntu-24.04` and a pulled `python:3.11-slim` image resolved
to its actual repository digest. It runs tests and the unchanged recorder inside
a container with `--cpus=2`, `--memory=256m`, `--memory-swap=256m`, and
`--network=none`. The source mount is read-only; only an isolated result directory
and temporary filesystem are writable. It drops Linux capabilities, sets
no-new-privileges and limits process count. The two-vCPU quota is measured rather
than inferred from the visible logical CPU count.

`run_benchmark.py` checks Python 3.11, cgroup CPU quota/affinity, memory cap and
network interfaces before recording. It preserves extra environment evidence in
`measurement_environment.json`; the original recorder independently retains its
CPU-capacity metadata. The memory cgroup cap covers the recorder, meter and solver
together, a stricter additional restriction than solver-only peak RSS. Startup
counts toward the recorder's five-second wall limit. Image pulls and GitHub
artifact uploads occur outside the disconnected measurement container.

The uploaded `director-context-run` artifact contains the final
`director-context.zip` and a `package/` directory with original observations,
captured recorder log, regressions, independent validation and environment evidence.
Observations remain available if solver requests fail; recorder infrastructure
failure is reported and its execution log retained rather than inventing results.
An artifact may contain failed checks and is not automatically a successful
submission. Review its validation report before submitting.

Current source handoff has not yet run the remote workflow. Consequently it
contains no fabricated `benchmark_results.json` or `benchmark_run.log`.
In a final workflow-produced ZIP, those files are actual measurements from that
run; the reported environment in those files supersedes this source-stage note.

Official runner and isolation references:

- [GitHub hosted runner selection](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)
- [Docker CPU and memory restrictions](https://docs.docker.com/engine/containers/resource_constraints/)
- [Docker container run options](https://docs.docker.com/reference/cli/docker/container/run/)

## Operational integration and limitations

The caller assembles the seven contract fields from upstream memory metadata,
reserves prompt overhead before setting the card budget, invokes the CLI, checks
exit status and parses the single response. On `ok`, resolve the returned card
IDs to their original text and include those records in Director's next prompt.
Do not expect selected ASCII order to be an optimal narrative presentation order.

Invalid metadata must be surfaced for correction rather than silently repaired.
If mandatory facts are incompatible or cannot fit, surface `infeasible` and ask
the upstream caller to resolve the revision/budget/mandatory choices. A production
fallback for timeouts or infrastructure failure may independently validate and
use the mandatory closure, with reduced-context telemetry; it must never drop
mandatory facts or revive superseded records silently. An unusable mandatory
closure requires an explicit product recovery path.

The weighted union objective models only declared coverage. It cannot establish
semantic completeness, model obedience, rendered prompt costs, scene continuity,
production drift or conflicts absent from upstream metadata. The sample's sleeve
card illustrates this: its jacket language does not invent a dependency on the
inactive jacket. General large-request optimization is heuristic; the 99% target
is an assessment target, not an approximation guarantee for arbitrary permitted
graphs. Exact search can be costly on difficult 24-card inputs and is measured
against the five-second limit, not exempt from it. Synthetic corpus sizes and
measurements are not statements about OpenArt's production distribution.

## Build and integrity

```sh
python3.11 build_package.py --output director-context.zip --require-results
```

This requires observations for every corpus request and checks corpus/recorder
hashes plus the measured Python-source inventory against the delivered source.
It does not rewrite observations or hide failures. A source-only handoff uses:

```sh
python3.11 build_package.py --output director-context-source.zip
```
