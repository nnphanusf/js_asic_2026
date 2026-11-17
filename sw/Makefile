# Regenerate the files derived from bitweaver/regmap.py and run the Python unit tests.
PY ?= python3

.PHONY: gen test check-gen programs
gen:
	$(PY) -m bitweaver.regmap --vh ../src/bw_defs.vh --md ../docs/regmap.md

test:
	$(PY) -m pytest -q .

check-gen: gen
	git diff --exit-code ../src/bw_defs.vh ../docs/regmap.md

# Assemble every program into build/<name>.json, build/<name>.hex and build/<file>.lst.
PROGRAMS := $(wildcard programs/*.bw)
programs: $(PROGRAMS)
	mkdir -p build
	for f in $(PROGRAMS); do $(PY) -m bitweaver.asm $$f -o build && $(PY) -m bitweaver.asm $$f > build/`basename $$f .bw`.lst; done
