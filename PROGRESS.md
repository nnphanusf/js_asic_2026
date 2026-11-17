# Progress

Deadline 2027-01-18 (RTL freeze 2027-01-08). See `PLAN.md` for the full brief. This file is the running log:
what is done, what is next, and every decision with its reason.

## Status at a glance
- Phase 0 (setup, spec): done.
- Phase 1 (ISS, assembler, first RTL): done locally; waiting for the full-design GDS run.
- Phase 2 (full cluster, core protocols): cluster RTL done (4 engines, BAU, open drain, WAIT timeout, timestamp,
  FIFO join/chain, IRQs); UART done; SPI and I2C next.

## Phase 0: Setup and spec (Nov 14–20)
- [x] Repo created from `ttihp-verilog-template` (`nnphanusf/js_asic_2026`)
- [x] Local toolchain: OSS CAD Suite 2026-11-16 in `~/tools/oss-cad-suite` (iverilog 14, Verilator 5.055,
      Yosys 0.71, SymbiYosys, cocotb 2.1)
- [x] IHP SG13CMOS5L liberty and Verilog cell models in `~/tools/pdk/ihp-sg13cmos5l/libs.ref` (from the IHP open
      PDK repo) for local area estimates and gate-level simulation
- [x] `info.yaml` filled: title, author, description, 8x4 tiles, 60 MHz, top module, pinout
- [x] `gds` + precheck green at 8x4 / 60 MHz on 3627c90 (probe design, 300 cells)
- [x] `viewer` job green; layout viewer at https://nnphanusf.github.io/js_asic_2026/
- [x] Spec: `sw/bitweaver/regmap.py` (single source of truth) generates `src/bw_defs.vh` and `docs/regmap.md`
- [x] Area check against the IHP library: `scripts/synth_area.sh`; numbers in `docs/results.md`
- [ ] Confirm how the demo board's RP2040/RP2350 drives `ui_in` (TT MicroPython SDK) for the host driver

## Phase 1: Executable spec and first silicon path
- [x] `sw/bitweaver`: register map, assembler/disassembler (labels, .side_set, .wrap, .define, .pins, .shift,
      .clkdiv, .fifo, .timeout, .bau, .init, .entry), loader, cycle-accurate ISS. 62 pytest tests.
- [x] RTL: `bw_host_spi`, `bw_imem`, `bw_sm`, `bw_fifo`, `bw_clkdiv`, `bw_bau`, top level with pin mux,
      synchronizers and glitch filters. Verilator `-Wall` lint clean.
- [x] Lock-step cocotb testbench (`test/lib/bwtb.py`): the RTL and the ISS get the same sampled inputs and must
      produce identical `uo_out/uio_out/uio_oe` on every cycle, in every test.
- [x] 32 smoke and per-instruction RTL tests, all in lock-step.
- [x] UART TX/RX (`sw/programs/uart.bw`) against `cocotbext-uart`: 9600, 115200, 230400, 460800, 921600 baud,
      8N1 and 8E1, framing error, full-duplex loopback. 5 tests.
- [ ] Full GDS + precheck + GL test green on the full design

## Phase 2: Full cluster and core protocols
- [x] 4 engines, FIFO join and chaining, IRQ flags, open drain, WAIT timeout, timestamp, BAU (in the RTL above)
- [x] UART
- [ ] SPI controller modes 0–3, SPI target
- [ ] I2C controller (100/400 kHz, ACK/NACK, repeated start, clock stretching), I2C target
- [ ] Concurrency demo: UART + SPI + I2C on 3 engines

## Phase 3: Verification depth and timing closure
- [ ] Constrained-random lock-step (Verilator), 10M+ cycles over 100+ seeds
- [ ] Formal: FIFO, host SPI, open drain, PC wrap bounds, opcode covers
- [ ] Coverage: opcode x option functional coverage, Verilator line/toggle coverage
- [ ] GL simulation of the full suite
- [ ] Timing: read LibreLane STA, confirm 60 MHz

## Phase 4: Stretch protocols
- [ ] WS2812, JTAG, SWD, PS/2, CAN, USB LS, 10BASE-T (in that order)

## Phase 5: Write-up
- [ ] architecture.md, isa.md, programming_guide.md, verification.md, results.md, ai_usage.md

## Decisions
- **2026-11-17 Reset:** asynchronous assert, synchronous release through a 2-FF reset synchronizer in the top
  level. IHP has no flop without a reset pin, so async reset costs no area, and the synchronizer keeps release
  clean. The ISS models the two release cycles.
- **2026-11-17 Area guardrail:** the full design maps to 27.4K IHP cells (39 % of the 8x4 core). That is over the
  PLAN's 24K cell guardrail, but the guardrail exists to leave at least 25 % headroom for CTS and routing, and 39 %
  utilisation leaves far more. The IMEM read ports (12K generic cells) dominate; if routing or timing fails, the
  first cut is a latch-based IMEM or 32 entries, as PLAN orders. No feature cut so far.
- **2026-11-17 Includes:** RTL files `` `include "bw_defs.vh" ``. LibreLane's linter resolves includes relative to
  the including file (`LINTER_RELATIVE_INCLUDES`, default on) and Yosys does the same, so no include path is needed
  in the flow; local lint needs `--relative-includes`.
- **2026-11-17 Instruction fetch:** IR is loaded at the end of each cycle from memory[next PC] (write-first), so an
  instruction executes on the cycle after its PC is computed with no extra pipeline stage and no branch penalty.
- **2026-11-17 Jump targets** are 5 bits relative to the engine's ORG register, so programs are position independent
  and up to 32 instructions long. Four engines share the 64 words.
- **2026-11-17 Virtual pins 19..31** expose TX-empty, RX-full, BAU stuff/error and the eight IRQ flags to
  `WAIT PIN`, `JMP PIN` and `IN PINS`. This replaces a separate WAIT source for status and costs a 32:1 mux.
- **2026-11-17 Parity** (8E1/8O1) is done by the host and a 9-bit program variant, not by hardware.
- **2026-11-17 UART pins** default to PIN3 (`ui_in[3]`) and PIN9 (`uo_out[4]`), the pins wired to the TT demo
  board's UART.

## Bugs found by lock-step
- ISS applied a `TS_PRESCALE` write to the timestamp in the same cycle; the RTL (correctly) applies it from the next
  cycle. Fixed in the ISS.

## Notes
- LibreLane's `Checker.DisconnectedPins` fails the GDS build if any input pad drives no logic. Keep at least one real
  path from every input pin (including `ena`, `clk`, `rst_n`) to an output. `ena` gates the timestamp counter.
- `make` in `test/` returns success even when tests fail; always run `cocotb_tools.check_results`.
- Waveforms are off by default; `make WAVES=1` dumps `tb.fst`.
