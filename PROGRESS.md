# Progress

Deadline 2027-01-18. See `PLAN.md` for the full schedule.

## Phase 0: Setup and spec (Nov 14–20)
- [x] Repo created from `ttihp-verilog-template` (`nnphanusf/js_asic_2026`)
- [x] Local toolchain: OSS CAD Suite 2026-11-16 in `~/tools/oss-cad-suite` (iverilog 14, Verilator 5.055,
      Yosys 0.71, SymbiYosys, cocotb 2.1)
- [x] `info.yaml` filled: title, author, description, 8x4 tiles, 60 MHz, top module, pinout
- [x] Skeleton top `tt_um_nnphanusf_bitweaver` (protocol pins idle) plus a read-only SPI bring-up probe on the host
      pins; 7 passing cocotb tests and clean lint
- [x] `docs/info.md` datasheet draft
- [x] Push branch; `test` and `docs` green on `phase0-setup` (4eb41f6)
- [x] `gds` + precheck green at 8x4 / 60 MHz on 3627c90 (4eb41f6 had failed with 19 critical disconnected pins;
      fixed by the probe). 300 cells, no timing violations; numbers in `docs/results.md`
- [x] `viewer` job green after setting the GitHub Pages source to GitHub Actions (2026-11-17); layout viewer at
      https://nnphanusf.github.io/js_asic_2026/
- [ ] Spec docs: `docs/isa.md`, register map, pinout table
- [ ] Area spike: synthesize a skeleton engine + FIFO against the IHP cell library, record in `docs/results.md`
- [ ] Confirm how the demo board's RP2350 drives `ui_in` (TT MicroPython SDK) for the host driver

## Notes
- The template's first `docs` run failed only because `info.yaml` title/author/description were blank.
- LibreLane's `Checker.DisconnectedPins` fails the GDS build if any input pad drives no logic. A design whose outputs
  are all constant gets optimized to nothing, so every input becomes disconnected. Keep at least one real path from
  every input pin (including `ena`, `clk`, `rst_n`) to an output.
- The bring-up probe (ID `0xB7`, pin readback, cycle counter) is temporary; the Phase 1 host SPI port replaces it.
