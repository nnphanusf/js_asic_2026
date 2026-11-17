# Bitweaver: Jane Street protocol-emulator ASIC entry

Read `PLAN.md` first (the full brief), then `PROGRESS.md` (where things stand). Update `PROGRESS.md` at the end of
every working session.

## Facts that don't change
- Tiny Tapeout, IHP 130 nm CMOS5L, **8x4 tiles**, top module `tt_um_nnphanusf_bitweaver`, target clock 60 MHz
  (`src/config.json` CLOCK_PERIOD 16.667 ns; fallback 50 MHz / 20 ns).
- Submission deadline **2027-01-18**. RTL freeze 2027-01-08.
- Pin plan is fixed (see `src/project.v` header and `info.yaml`): host SPI on `ui_in[7:5]` / `uo_out[7]`, IRQ on
  `uo_out[6]`, 19 protocol pins PIN0..PIN18.

## Toolchain
- Local tools come from OSS CAD Suite: `source ~/tools/oss-cad-suite/environment` before any make, verilator,
  yosys, sby or python command. It provides its own Python 3.11 with cocotb.
- Tests: `cd test && make clean && make && python3 -m cocotb_tools.check_results results.xml`
  (make alone returns success even when tests fail).
- Lint: `verilator --lint-only -Wall -Wno-DECLFILENAME --relative-includes src/*.v` (LibreLane resolves
  `` `include `` relative to the including file, so the generated `src/bw_defs.vh` needs this flag).
- Python unit tests: `make -C sw test`; regenerate derived files with `make -C sw gen`.
- Area: `scripts/synth_area.sh` (needs the IHP liberty files in `~/tools/pdk`).
- CI (`.github/workflows`): `test`, `gds` (LibreLane + precheck), `docs`, `fpga`. CI is the referee: a phase is not
  done while anything is red.

## Rules
- Every RTL change ships with a test. Tests and lint pass before every commit.
- Keep the Python ISS (`sw/bitweaver/iss.py`, from Phase 1) cycle-accurate; it is the reference model.
- `sw/bitweaver/regmap.py` is the single source of truth for the register map; `src/bw_defs.vh` and the doc tables
  are generated from it. Never edit generated files by hand.
- New source files must be added to both `info.yaml` `source_files` and `PROJECT_SOURCES` in `test/Makefile`.
- Synthesize and check cell count after any structural change; record area and timing numbers in
  `docs/results.md`.
- Use a cocotb clock period in whole even picoseconds (16666 ps), since the testbench timescale is 1 ns / 1 ps.
- No Claude / AI attribution lines in commit messages or PR descriptions. The required AI-usage disclosure lives in
  `docs/ai_usage.md` and must stay accurate.
