# Progress

Deadline 2027-01-18 (RTL freeze 2027-01-08). See `PLAN.md` for the full brief. This file is the running log:
what is done, what is next, and every decision with its reason.

## Status (2026-11-20)

**The two-engine design routes; its first run missed setup at 60 MHz.** The `gds` run of 297df42 finished in
4 h 46 min: detailed routing reached zero violations after 3 h 23 min; Magic DRC, LVS and antenna checks are
clean, with no hold violation in any corner. Setup failed in the slow and typical corners (typical: 12
endpoints) and passed in the fast corner (reg-to-reg worst slack about +4 ns). The placement-based timing after
clock-tree synthesis had shown no setup violation, so most of the loss came from routing: Metal3, the only
horizontal signal layer, ended global routing at 79.7 % with 14 446 overflow, and the log reports max-slew and
max-cap violations in every corner after routing. The job log truncates the slack columns of the timing table,
so the exact slacks are only in that run's `GDS_logs` artifact (downloading it needs a GitHub login).

**Fixes, following PLAN ("fix critical paths ... confirm 60 MHz or fall back to 50").**
- RTL, exact retimings (no cycle changes): the clock divider's `cnt == 0` is now a registered flag, so an
  engine's tick comes from a flip-flop (proved equal to the counter, `formal/clkdiv.sby`); the instruction-memory
  ports read each word's next value, so no compare or bypass mux follows the 64:1 read (proved equivalent to the
  previous memory with Yosys `equiv_induct`). Delay-driven ABC estimate of the slow-corner logic path 7.57 →
  6.47 ns.
- Flow (`src/config.json`): `CLOCK_PERIOD` 20 (50 MHz, PLAN's fallback and the Tiny Tapeout IHP default);
  design-rule repair and timing repair after global routing, against the routed parasitics; buffers on wires
  longer than 800 um; Magic DRC off, because it took 59 minutes of the 6-hour job and repeats the check the
  precheck job makes with the PDK's KLayout SG13CMOS5L deck (Tiny Tapeout's precheck runs Magic DRC only for
  sky130).
- Docs and `info.yaml` now say 50 MHz. Simulations still run at 60 MHz: engines count cycles, the programs are
  unchanged, and the timing helpers take the clock as `f_clk`.

**Verification of this state.** pytest 77 passed; cocotb 56/56 in lock-step (new: divider integer part 1 and 2
with fractional carries, integer part 0 over 65 536 cycles, host rewrites landing on every fetch phase of a
two-cycle loop); Verilator lock-step 1000 seeds, 100M cycles, no mismatch; functional coverage 465/465, line
100 % (285/285), toggle 98.3 % (8444/8590, the same 146 constant bits as before); formal: all five jobs pass
(clkdiv, FIFO, host port, open drain, engine with the 7-minute PDR proof); gate level 56/56 on the Yosys/IHP
netlist; synthesis 14 922 cells, 242 717 um2.

**Open.** The `gds` run of this commit: `gds`, `precheck`, `gl_test` and `viewer` must be green. If setup still
fails at 50 MHz, first read the per-corner STA reports in its `GDS_logs` artifact
(`runs/wokwi/*-openroad-stapostpnr/*/max.rpt`); then the options are PLAN's one-stage fetch pipeline (with the
simulator changed to match) or the next area cut, IMEM depth 32 (the I2C target pair and the SPI + I2C demo
would no longer fit).

## Status (2026-11-19)

**The four-engine design did not route; the chip is now two engines.** All three `gds` runs of the four-engine
RTL (793167b, a1e294a, ab202af) were cancelled at GitHub's 6-hour job limit. The raw log of ab202af shows why:
synthesis to post-CTS resizing took 6 minutes, but global placement reported 20–45 % of routing tiles
overflowed at 50 % utilisation, global routing took 48 minutes and finished congested, and detailed routing had
296 679 violations after its first 3-hour pass and 348 317 partway through the second. A Tiny Tapeout tile gives
signals only Metal2–Metal4, and the design was dominated by wide multiplexers (five 64:1 x 16 read ports over the
1024 instruction-memory bits, four engines). Following PLAN's cut order (engine count first), commit 297df42:
- two engines instead of four (every protocol program still fits; none needs more than two engines);
- no host read port on the instruction memory (words are read back through an engine: EXEC a `JMP`, read `EXEC`);
- `PL_TARGET_DENSITY_PCT` 60 → 40 to spread placement.
Result: 15 288 IHP cells, 230 944 um2 (57 % of before), about 30 % utilisation; ABC slow-corner estimate 8.67 ns.

**What was built.** Bitweaver, `tt_um_nnphanusf_bitweaver`, 8x4 tiles on IHP SG13CMOS5L at 60 MHz: two
programmable bit-banging engines sharing a 64 x 16-bit instruction memory, a PIO-class ISA extended with a timed
WAIT, a bit-level assist unit (NRZI, bit stuffing, Manchester, programmable CRC), open-drain pads, input glitch
filters, a timestamp source, FIFO joining and chaining, and a register-mapped SPI host port. Toolchain in
`sw/bitweaver` (register map as single source of truth, assembler, loader, cycle-accurate simulator), a MicroPython
driver for the demo board, 18 protocol programs and the documentation set in `docs/`.

**Protocols working** (each against an independent model of the other end): UART 9600–921600 baud 8N1/8E1, SPI
controller modes 0–3 up to 7.5 MHz, SPI target, I2C controller at 100 and 400 kHz with every timing limit checked,
I2C target (two engines), WS2812, JTAG, SWD, PS/2, CAN 2.0A at 500 kbit/s and 1 Mbit/s, low-speed USB packets,
1-Wire, and SPI + I2C on both engines at once.

**Verification of the two-engine RTL.**
- pytest: 77 tests. cocotb: 53 tests, all in cycle-by-cycle lock-step with the simulator, all passing.
- Constrained-random lock-step (Verilator): 100M cycles over 1000 seeds, no mismatch; functional coverage
  465/465 bins, line coverage 100 %.
- Toggle coverage 97.8 % (all 146 missing points are constant bits, listed in `docs/verification.md`).
- Formal: all four jobs pass in CI on 297df42 (FIFO ordering, host-port no-deadlock and engine invariants proved
  unbounded; open drain proved); covers reached for every opcode.
- Gate level: 53/53 cocotb tests pass on the Yosys/IHP netlist of the two-engine design.
- CI on 297df42: `test`, `lockstep`, `formal`, `docs` green.

**Cut.** Two of the four engines and the instruction-memory host read port (routing, above); 10BASE-T (host port
bandwidth below the line rate). 1-Wire was added for the programming guide.

**Open.** The `gds` run of 297df42 (two engines) decides whether the chip is buildable: `gds`, `precheck`,
`gl_test` and `viewer` must be green. If routing still fails, the next cuts in PLAN's order are IMEM depth (32
words) and FIFO depth.

**Steps left for the human.**
1. Once `gds`, `precheck` and `gl_test` are green, merge `phase0-setup` into `main`.
2. Each push runs `gds`; consider whether doc-only pushes should skip it.
3. Submit through the Jane Street form when it is posted (deadline 2027-01-18; RTL freeze 2027-01-08).

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
- [x] RTL: host SPI, IMEM, engines with FIFO, clock divider, BAU (4 engines, cut to 2 on 2026-11-19); top level with pin merge, synchronizers, glitch
      filters, open drain, timestamp, IRQs. Verilator `-Wall` clean.
- [x] Lock-step cocotb testbench: every test compares all outputs with the ISS on every cycle.
- [x] UART TX/RX against `cocotbext-uart`.
- [ ] Full GDS + precheck + GL test green: four engines did not route (3 runs cancelled at 6 h); two-engine run
      of 297df42 pending

## Phase 2: Full cluster and core protocols
- [x] Engines, FIFO join and chaining, IRQ flags, open drain, WAIT timeout, timestamp, BAU
- [x] UART 9600–921600 baud, 8N1/8E1, framing errors, full duplex
- [x] SPI controller modes 0–3 (up to 7.5 MHz), SPI target mode 0 (up to ~3 MHz)
- [x] I2C controller 100 kHz / 400 kHz with repeated START, ACK/NACK, clock stretching, stretch timeout; I2C target
      (two engines) with clock stretching
- [x] Concurrency: UART TX + UART RX + SPI + I2C on four engines at once (now SPI + I2C on two)

## Phase 3: Verification depth and timing closure
- [x] Constrained-random lock-step in Verilator (`verif/lockstep`): 100M cycles over 1000 seeds, no mismatch;
      CI runs 10M cycles over 50 seeds per push
- [x] Formal: FIFO order/no overflow (proved), host SPI no deadlock (proved), engine invariants incl. PC within the
      wrap range (proved), open drain never drives high (proved), covers for every opcode
- [x] Coverage: functional 465/465 bins, Verilator line 100 %, toggle 99.1 %
- [x] GL simulation of the full suite on the Yosys/IHP netlist locally; CI `gl_test` on the routed netlist pending
- [x] Timing, first pass: the two-engine GDS routed but missed 60 MHz (slow and typical corners); two exact RTL
      retimings, repair after global routing, 50 MHz fallback (2026-11-20)
- [ ] Timing: `gds` green at 50 MHz

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
- **Area guardrail (revised 2026-11-19):** the four-engine design (27.4K cells, 39 % by cell area, 50 % placement
  utilisation) passed the cell-count reasoning but did not route: routing, not area, was the binding constraint,
  because the design is mostly wide multiplexers and a TT tile only has Metal2–Metal4 for signals. Cut to two
  engines and removed the IMEM host read port (15.3K cells). The earlier reasoning that 39 % utilisation "leaves
  far more than 25 % headroom" was wrong for this kind of logic.
- **Clock (2026-11-20): 50 MHz sign-off.** The routed two-engine design missed 60 MHz in the slow and typical
  corners. PLAN names 50 MHz as the fallback and it is the Tiny Tapeout IHP template default. RTL and programs are
  unchanged (engines count cycles); simulations stay at 60 MHz, and the timing helpers take `f_clk`. Low-speed
  USB is exact only at 60 MHz (40 clocks per bit); at 50 MHz the fractional divider moves bit edges by one clock.
- **Flow settings beyond the template (2026-11-20):** repair after global routing (`RUN_POST_GRT_DESIGN_REPAIR`,
  `RUN_POST_GRT_RESIZER_TIMING`, LibreLane marks both experimental), 800 um maximum wire length, and
  `RUN_MAGIC_DRC` off to keep the run inside GitHub's 6-hour limit; DRC sign-off stays with the precheck job's
  KLayout SG13CMOS5L deck.
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
