# Regenerate the files derived from bitweaver/regmap.py and run the Python unit tests.
PY ?= python3

.PHONY: gen test check-gen
gen:
	$(PY) -m bitweaver.regmap --vh ../src/bw_defs.vh --md ../docs/regmap.md

test:
	$(PY) -m pytest -q .

check-gen: gen
	git diff --exit-code ../src/bw_defs.vh ../docs/regmap.md
