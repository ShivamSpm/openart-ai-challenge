# Regression tests

Run `python -m unittest discover -s tests -v` from the package root. The independent checker does not import the production model or search. Set `RUN_FULL_CORPUS=1` to enable the optional fresh corpus test; the workflow always records all 28 requests separately.
Certificate tests independently recompute bounds, compare them with exhaustive
optima, and check both public disjoint-coverage requests against the 5% gap.
They also exercise valid loose bounds, rounding, missing/invalid prices and the
requirement to include active cards even when their closures are unusable.
