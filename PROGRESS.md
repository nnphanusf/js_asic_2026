# Progress

Deadline 2027-01-18 (RTL freeze 2027-01-08). See `PLAN.md` for the full brief. This file is the running log:
what is done, what is next, and every decision with its reason.

## Status at a glance (2026-11-17)
- Phases 0–4 are done locally: spec, toolchain, RTL, core protocols, verification depth, stretch protocols.
- Phase 5 (write-up) is drafted: architecture, ISA, programming guide, verification, results, AI usage.
- Waiting on CI for the full design: the first `gds` run of the complete RTL (commit 793167b) was still routing
  after 80 minutes. The later commits need a push and a green run of `gds`, `precheck`, `gl_test`, `test`,
  `lockstep` and `formal`.

## Phase 0: Setup and spec
- [x] Repo from `ttihp-verilog-template`; OSS CAD Suite 2026-11-16 in `~/tools/oss-cad-suite`
- [x] IHP SG13CMOS5L liberty and cell models in `~/tools/pdk/ihp-sg13cmos5l/libs.ref` (from the IHP open PDK repo)
      for local area estimates and gate-level simulation
- [x] `info.yaml` (8x4, 60 MHz, all source files), `docs/info.md` datasheet
- [x] `gds` + precheck green at 8x4 / 60 MHz on the probe design (3627c90); viewer at
      https://nnphanusf.github.io/js_asic_2026/
- [x] Spec: `sw/bitweaver/regmap.py` generates `src/bw_defs.vh` and `docs/regmap.md`; `docs/isa.md`
- [x] Area check against the IHP library: `scripts/synth_area.sh`
- [x] Demo-board host path: the TT MicroPython SDK's `ttboard.util.platform.write_ui_in_byte` /
      `read_uo_out_byte` (fast byte I/O); `sw/host/micropython/bitweaver.py` bit-bangs the host SPI port with them

## Phase 1: Executable spec and first silicon path
- [x] `sw/bitweaver`: register map, assembler/disassembler, loader and load-image builder, cycle-accurate ISS,
      protocol host helpers. 74 pytest tests.
- [x] RTL: host SPI, IMEM, 4 engines with FIFO, clock divider, BAU; top level with pin merge, synchronizers, glitch
      filters, open drain, timestamp, IRQs. Verilator `-Wall` clean.
- [x] Lock-step cocotb testbench: every test compares all outputs with the ISS on every cycle.
- [x] UART TX/RX against `cocotbext-uart`.
- [ ] Full GDS + precheck + GL test green on the full design (run 44085398579 in progress)

## Phase 2: Full cluster and core protocols
- [x] 4 engines, FIFO join and chaining, IRQ flags, open drain, WAIT timeout, timestamp, BAU
- [x] UART 9600–921600 baud, 8N1/8E1, framing errors, full duplex
- [x] SPI controller modes 0–3 (up to 7.5 MHz), SPI target mode 0 (up to ~3 MHz)
- [x] I2C controller 100 kHz / 400 kHz with repeated START, ACK/NACK, clock stretching, stretch timeout; I2C target
      (two engines) with clock stretching
- [x] Concurrency: UART TX + UART RX + SPI + I2C on four engines at once

## Phase 3: Verification depth and timing closure
- [x] Constrained-random lock-step in Verilator (`verif/lockstep`): 100M cycles over 1000 seeds, no mismatch;
      CI runs 10M cycles over 50 seeds per push
- [x] Formal: FIFO order/no overflow (proved), host SPI no deadlock (proved), engine invariants incl. PC within the
      wrap range (proved), open drain never drives high (proved), covers for every opcode
- [x] Coverage: functional 465/465 bins, Verilator line 100 %, toggle 99.1 %
- [x] GL simulation of the full suite on the Yosys/IHP netlist locally; CI `gl_test` on the routed netlist pending
- [ ] Timing: read LibreLane STA for the full design (ABC estimate 9.1 ns at the slow corner, budget 16.7 ns)

## Phase 4: Stretch protocols
- [x] WS2812, JTAG, SWD, PS/2, CAN 2.0A (TX/RX, BAU stuffing + CRC15), low-speed USB packets (TX/RX, BAU NRZI +
      stuffing + CRC16), plus 1-Wire
- [x] 10BASE-T: **cut** (see decisions)

## Phase 5: Write-up
- [x] `architecture.md`, `isa.md`, `programming_guide.md`, `verification.md`, `results.md`, `ai_usage.md`, README
- [ ] Final numbers from the full-design GDS run into `results.md`

## Decisions
- **Reset:** asynchronous assert, synchronous release through a 2-FF synchronizer. IHP has no flip-flop without a
  reset pin, so async reset costs no area. The ISS models the two release cycles.
- **Area guardrail:** the full design maps to 27.4K IHP cells (about 39 % of the 8x4 core). That is over the PLAN's
  24K-cell guardrail, but the guardrail exists to leave at least 25 % headroom for CTS and routing, and 39 %
  utilisation leaves far more. No feature was cut for area. If routing or timing fails, the first cut is the
  IMEM read ports (latch-based IMEM or 32 entries), as PLAN orders.
- **Includes:** RTL files `` `include "bw_defs.vh" ``; LibreLane's linter and Yosys resolve includes relative to
  the including file, so local lint needs `--relative-includes`.
- **Instruction fetch:** IR is loaded at the end of each cycle from memory[next PC] (write-first), so there is no
  fetch bubble and no branch penalty.
- **Jumps** are 5 bits relative to ORG (position-independent programs). Programs may be up to 64 instructions
  long but jump targets must be in the first 32 (the I2C target needs 41 instructions).
- **Virtual pins 19..31** expose TX-empty, RX-full, BAU stuff/error and the IRQ flags to WAIT/JMP/IN.
- **Parity** for UART is done by the host with a 9-bit program variant.
- **UART pins** default to PIN3/PIN9, the TT demo board's UART pins.
- **Receive-only and transmit-only programs join their FIFOs** (8 deep): found when the four-protocol concurrency
  test lost a UART byte while the host served other engines.
- **I2C bit timing:** 10 ticks per bit with the low/high split set by assembler defines
  (`bitweaver.proto.i2c_timing`): 5/5 at clkdiv 60 for 100 kHz (SCL stays high ≥ 4 us even after stretching),
  6/4 at clkdiv 15 for 400 kHz (tLOW 1.5 us ≥ 1.3). A fixed 5/5 split could only reach 375 kHz in fast mode.
- **WS2812** uses an integer divider (7, 857 kHz): the fractional 7.5 made some T0H pulses 383 ns (limit 380).
- **10BASE-T cut:** the host SPI port tops out at clk/8 = 7.5 Mbit/s raw, below the 10 Mbit/s line rate, and an
  8-word FIFO cannot hold even the 64-bit preamble plus a minimal frame. PLAN says to attempt it only if the timing
  math works; it does not. The BAU's Manchester mode is still implemented and tested.
- **USB** is demonstrated at the packet level (SYNC, PID, data, CRC, EOP both ways), not as a full device stack,
  which would need a CPU-speed host behind the FIFOs.
- **CAN** uses hard synchronization on SOF only; no arbitration, retransmission or error frames. Documented in
  `can.bw`.
- **Board driver:** assembly happens on the PC; the board replays load images (lists of register writes), so the
  MicroPython side needs no assembler.

## Notes
- LibreLane's `Checker.DisconnectedPins` fails the build if any input pad drives no logic; `ena` gates the
  timestamp, every other input reaches the engines or the host port.
- `make` in `test/` returns success even when tests fail; always run `cocotb_tools.check_results`.
- Waveforms are off by default; `make WAVES=1` dumps `tb.fst`.
- To run a second cocotb job while another uses `test/`, pass `SIM_BUILD=sim_build/rtl2 COCOTB_RESULTS_FILE=...`.
