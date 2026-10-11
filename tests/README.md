# Regression tests

Run `python -m unittest discover -s tests -v` from the package root. The independent checker does not import the production model or search. Set `RUN_FULL_CORPUS=1` to enable the optional fresh corpus test; the workflow always records all 28 requests separately.
