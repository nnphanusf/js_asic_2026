# Progress

Deadline 2027-01-18 (RTL freeze 2027-01-08). See `PLAN.md` for the full brief. This file is the running log:
what is done, what is next, and every decision with its reason.

## Final report (2026-11-17)

**What was built.** Bitweaver, `tt_um_nnphanusf_bitweaver`, 8x4 tiles on IHP SG13CMOS5L at 60 MHz: four
programmable bit-banging engines sharing a 64 x 16-bit instruction memory, a PIO-class ISA extended with a timed
WAIT, a bit-level assist unit (NRZI, bit stuffing, Manchester, programmable CRC), open-drain pads, input glitch
filters, a timestamp source, FIFO joining and chaining, and a register-mapped SPI host port. Toolchain in
`sw/bitweaver` (register map as single source of truth, assembler, loader, cycle-accurate simulator), a MicroPython
driver for the demo board, 18 protocol programs and the documentation set in `docs/`.

**Protocols working** (each against an independent model of the other end): UART 9600–921600 baud 8N1/8E1, SPI
controller modes 0–3 up to 7.5 MHz, SPI target, I2C controller at 100 and 400 kHz with every timing limit checked,
I2C target (two engines), WS2812, JTAG, SWD, PS/2, CAN 2.0A at 500 kbit/s and 1 Mbit/s, low-speed USB packets,
1-Wire, and UART + UART + SPI + I2C on four engines at once.

**Verification.**
- pytest: 74 tests (assembler, simulator rules, generated files, every program, board driver).
- cocotb: 53 tests, all in cycle-by-cycle lock-step with the simulator; all pass at RTL and on the Yosys/IHP
  gate-level netlist locally.
- Constrained-random lock-step (Verilator): 100M cycles over 1000 seeds, no mismatch; CI runs 10M per push.
- Formal (SymbiYosys): FIFO ordering, host-port no-deadlock, engine invariants (PC within wrap range etc.) and
  open drain all proved unbounded; covers reached for every opcode.
- Coverage: functional 465/465 bins; Verilator line 100 %, toggle 99.1 % (the 69 missing points are constant bits).

**Area and timing.** 27.4K IHP cells, about 386 000 um2, about 39 % of the 8x4 core (Yosys + ABC). ABC critical
path estimate 9.07 ns at the slow corner without wire load, against 16.67 ns. LibreLane numbers: see CI status.

**Cut.** 10BASE-T (host port bandwidth below the line rate; see decisions). Nothing else from PLAN was cut;
1-Wire was added for the programming guide.

**CI status at the end of this session.** On ab202af (the final commit): `test`, `lockstep`, `formal` (all four
jobs) and `docs` green. (`formal` had failed once on a1e294a only because the OSS CAD Suite download hit GitHub's
anonymous API rate limit; fixed by passing `GITHUB_TOKEN`.) `gds` for the full design: three hardening runs
(793167b, a1e294a, ab202af; identical RTL apart from `ifdef FORMAL` blocks) were in progress, the oldest after
3 h 40 min; results go into `docs/results.md`.

**Steps left for the human.**
1. Confirm the full-design `gds`, `precheck`, `gl_test` and `viewer` jobs pass, then merge `phase0-setup` into
   `main`. (The repository was briefly private during this session and is public again.)
2. Each push runs `gds`, about three hours of runner time; consider whether doc-only pushes should skip it.
3. Optionally skim `docs/` (architecture, ISA, programming guide, verification, results, AI usage).
4. Submit through the Jane Street form when it is posted (deadline 2027-01-18; RTL freeze 2027-01-08).

## Status at a glance (2026-11-17)
- Phases 0–5 are done locally; see the final report above for what remains in CI.

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
