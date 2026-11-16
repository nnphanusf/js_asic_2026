# Progress

Deadline 2027-01-18. See `PLAN.md` for the full schedule.

## Phase 0: Setup and spec (Nov 14–20)
- [x] Repo created from `ttihp-verilog-template` (`nnphanusf/js_asic_2026`)
- [x] Local toolchain: OSS CAD Suite 2026-11-16 in `~/tools/oss-cad-suite` (iverilog 14, Verilator 5.055,
      Yosys 0.71, SymbiYosys, cocotb 2.1)
- [x] `info.yaml` filled: title, author, description, 8x4 tiles, 60 MHz, top module, pinout
- [x] Skeleton top `tt_um_nnphanusf_bitweaver` (all pins idle) with 2 passing cocotb tests and clean lint
- [x] `docs/info.md` datasheet draft
- [ ] Push branch; `test`, `docs`, `gds` + precheck green at 8x4 / 60 MHz
- [ ] Spec docs: `docs/isa.md`, register map, pinout table
- [ ] Area spike: synthesize a skeleton engine + FIFO against the IHP cell library, record in `docs/results.md`
- [ ] Confirm how the demo board's RP2350 drives `ui_in` (TT MicroPython SDK) for the host driver

## Notes
- The template's first `docs` run failed only because `info.yaml` title/author/description were blank.
