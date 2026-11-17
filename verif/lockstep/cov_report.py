#!/usr/bin/env python3
"""Functional coverage report: every reachable (instruction, option, outcome) bin, hit or missed.

Reads coverage/functional.json written by run_lockstep.py --coverage.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "sw"))
from bitweaver import regmap as R  # noqa: E402


def expected_bins():
    bins = []
    for c in R.JMP_CONDS:
        bins.append(("JMP", c, "taken"))
        if c != "always":
            bins.append(("JMP", c, "not taken"))
    for src in ("pin", "irq"):
        for pol in (0, 1):
            bins += [("WAIT", src, pol, "untimed", o) for o in ("met", "stall")]
            bins += [("WAIT", src, pol, "timed", o) for o in ("met", "stall", "timeout")]
    for s in R.SOURCES:
        bins += [("IN", s, "raw", o) for o in ("ok", "autopush", "stall")]
        bins += [("IN", s, "bau", o) for o in ("ok", "autopush", "stall", "destuff")]
    for d in R.OUT_DESTS:
        bins += [("OUT", d, "raw", o) for o in ("ok", "autopull", "refill", "stall")]
        bins += [("OUT", d, "bau", o) for o in ("ok", "autopull", "refill", "stall", "stuff", "half")]
    for name, cond in (("PUSH", "iffull"), ("PULL", "ifempty")):
        fail = "drop" if name == "PUSH" else "empty"
        for c in ("always", cond):
            for blk in ("block", "noblock"):
                outs = ["ok", "stall" if blk == "block" else fail] + (["skip"] if c != "always" else [])
                bins += [(name, c, blk, o) for o in outs]
    for d in R.MOV_DESTS:
        for op in R.MOV_OPS:
            bins += [("MOV", d, op, s) for s in R.SOURCES]
    for o in ("set", "clear", "set+wait", "waiting", "wait done"):
        bins += [("IRQ", o, "abs"), ("IRQ", o, "rel")]
    bins += [("SET", d) for d in ("pins", "x", "y", "bau", "pindirs", "reserved")]
    bins += [("SIDE", "pins"), ("SIDE", "pindirs")]
    return ["|".join(map(str, b)) for b in bins]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "coverage", "functional.json")
    hits = json.load(open(path))
    exp = expected_bins()
    missed = [b for b in exp if b not in hits]
    extra = [b for b in hits if b not in exp]
    print(f"functional coverage: {len(exp) - len(missed)}/{len(exp)} bins "
          f"({100.0 * (len(exp) - len(missed)) / len(exp):.1f} %)")
    for b in missed:
        print("  missed:", b)
    for b in extra:
        print("  unexpected bin (update cov_report.py):", b)
    return 0 if not extra else 1


if __name__ == "__main__":
    sys.exit(main())
