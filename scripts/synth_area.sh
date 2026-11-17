#!/usr/bin/env bash
# Synthesize the design against the IHP SG13CMOS5L standard-cell library and report cells and area.
# Usage: scripts/synth_area.sh [liberty file]   (needs Yosys from OSS CAD Suite on PATH)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIB="${1:-${PDK_LIB:-$HOME/tools/pdk/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib}}"
SRCS=$(python3 -c "import yaml,sys; print(' '.join('$ROOT/src/'+s for s in yaml.safe_load(open('$ROOT/info.yaml'))['project']['source_files']))" 2>/dev/null \
  || ls "$ROOT"/src/*.v | tr '\n' ' ')
OUT="${OUT:-$ROOT/runs/synth}"
mkdir -p "$OUT"
cd "$OUT"
yosys -q -l synth.log -p "
read_verilog -sv $SRCS
synth -top tt_um_nnphanusf_bitweaver
tee -o hier.txt stat
flatten
synth -top tt_um_nnphanusf_bitweaver -run fine:
dfflibmap -liberty $LIB
abc -liberty $LIB
opt_clean
tee -o stat.txt stat -liberty $LIB
write_verilog -noattr netlist.v
"
grep -E "cells|Chip area" stat.txt | head -3
