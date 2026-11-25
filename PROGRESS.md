# Progress

Deadline 2027-01-18 (RTL freeze 2027-01-08). This file is the running log:
what is done, what is next, and every decision with its reason.

## Third independent review (2026-11-24, commit 3b3beae)

A third audit, read-only from the same brief, reran every gate (pytest 143, cocotb 58/58 in lock-step on RTL
and a local gate-level netlist, the Verilator random lock-step, all six formal jobs, synthesis: 14 922 cells
and 242 717 um2 exactly) and checked all 25 fixes of the first two reviews. It found no RTL defect and said the
hardware is ready to submit as it is. Its five findings were in firmware, host software and docs; each was
reproduced before it was fixed, and each fix has a test that fails without it. No RTL file changed.

**Fixed.**
- **USB receive ignored bit-stuffing errors.** `usb_rx` never looked at the BAU's sticky stuff error flag, so
  a trailing stuff bit sent as 1 (seven ones) or left out before the EOP still decoded as valid: the CRC is
  computed before stuffing. Now a set error flag, or a stuff bit still due at the EOP, marks the packet bad (bit
  count 0xFFFF). Where a stuff bit is due, D- and D+ are also sampled in the same cycle (`mov osr, pins`, then
  `in osr, bau`), so SE0 in that slot is caught even when it decodes as a valid transition. Two other ways
  were tried first: checking the end-of-packet flag one tick before or after the sample either missed slow
  senders on long packets or rejected valid ones from fast senders. An IN token and an 8-byte DATA1 whose CRCs
  end with six ones are accepted with their stuff bit and rejected with it sent as 1 or left out, at -0.4, 0
  and +0.4 % and nine phases; the earlier tolerance tests still pass. `usb_rx` is 29 instructions.
- **SPI target speed.** The CS fix of the second review cost cycles: MISO came 9 to 10 cycles after CS fell,
  and the direction was set again on every byte, so a controller with half a period of CS set-up read a wrong
  first bit from 2.8 MHz (2.2 MHz with 60 ns of pad delay). The first bit is now put on the released pin while
  CS is high, the target waits for CS in a one-cycle loop (MISO driven 4 cycles after CS falls), and the
  direction is set once per selection. A mode-0 controller model at 50 MHz with delayed MISO measures the
  limits, now in the docs: 2.5 MHz with 60 ns, 3 MHz with 40 ns, CS set-up 160 ns, at ten phases. The model
  reproduces the review's failures on the old program. 33 instructions.
- **CAN remote frames.** `can_rx_decode` dropped the RTR bit and the DLC, and the CAN model node ignored RTR,
  so remote frames were never tested on the wire. The model handles them, the CAN tests send remote frames
  requesting 0, 4 and 8 bytes both ways, and `can_rx_frame` returns the RTR flag and DLC. The programs were
  already right.
- **Hand-over of pins set up in `.init`.** `uart_tx` and `ws2812` make their pin an output with `.init set
  pindirs`, which the hand-over did not count, so a `uart_tx` moved onto PIN11 floated during a reload (about
  130 us at clk/16). Such SET-range pins now count as set up by the program.
- **Assembler:** `.pins` bases over 31, `dirs`/`high`/`od`/`filter` pins of the wrong kind, a chain source that
  is not an engine, `.timeout` and the CRC polynomial over 16 bits and a stuff run over 7 are errors.
- **Docs:** gate-level counts (58 on 3b3beae in CI), the sixth formal job in the AI use page, the SPI target
  limits and remote frames in the results.

**Not changed.** The board's real pad and multiplexer delays, and gate-level simulation with SDF delays,
remain open questions for silicon; the review agreed that functional netlist tests plus three-corner static
timing analysis are the standard sign-off for Tiny Tapeout.

## Second independent review (2026-11-24, commit 68e38d0)

A second audit, by another AI assistant working read-only from the same brief, checked the first review's fixes
(it confirmed the CAN, I2C controller, loader, board-driver, assembler and USB receive fixes) and reproduced the
test, lint and CI results. It found no RTL failure. Its 16 findings were in the protocol programs, the host
software and the documentation. Each high-severity one was reproduced here before the fix (the I2C target's false
repeated START, the USB transmit underrun at clk/16 with the exact bit count it reported, the stale pin drivers
and `PAD_OD` 0x6000 after switching protocols, the SPI target's MISO), and each fix has a test that fails without
it. No RTL file changed.

**Fixed.**
- **I2C target: data set-up after a stretch.** The target put a data bit on SDA in the same cycle it released a
  stretched SCL. A first bit of 0 then fell while SCL rose: the bus monitor saw a repeated START with 0 ns set-up.
  The bit now goes out with SCL held low, and SCL is released 16 cycles later (320 ns at 50 MHz, over the 250 ns
  tSU;DAT of standard mode). The target test reads a stretched 0x19.
- **SPI target: MISO only while selected.** MISO was enabled at load and never released, so two targets on one
  MISO line would fight. The program releases MISO whenever CS is high, also mid-byte; a byte cut short by CS
  is dropped both ways (34 instructions, was 23).
- **USB transmit at clk/16.** Streaming a 13-word DATA packet as the FIFO drained left the engine without a word
  mid-packet (103 raw bits, a stuffing error, a bad CRC). `push_packet` (loader, board driver, testbench) waits
  for the previous packet, stops the engine, preloads the 8-word FIFO and streams the rest (about 43 µs of slack
  for 8 bytes). Packets of up to 8 words have no deadline; the bit-banged board port can send only those, which
  the program header and `results.md` say. Tested in RTL at 50 MHz with the host at clk/16.
- **Pin hand-over between programs.** Disabling an engine keeps its pins and pad modes, and the loaders only
  added bits: I2C then SPI left SCK open drain, SPI target then controller left MISO driven. The loaders now
  release the replaced program's pins and clear the pad bits only it needed (`PinOwners`), and `release(sm)`
  frees an engine. A pin the new program sets up itself is not released in between: the first version did
  that, and the full cocotb run caught it (a reloaded UART TX line, PIN9, dropped to 0 and sent a false start
  bit; SPI's SCK got an edge). Load images carry a format number and their pins; the board driver refuses
  older images before writing anything.
- **SWD.** `jtag_to_swd` put eight idle cycles between the high cycles and the select word, so a target in JTAG
  mode never switched (ADIv5 B5.2.2). The new `swd_transfer` reads the ACK before the data phase, retries on
  WAIT and sends no data after WAIT or FAULT. The SWD model now starts in JTAG mode and can answer WAIT.
- **PS/2:** the wait for CLK high had no timeout, so a frame cut off with CLK held low (the host inhibiting) left
  its bits in the ISR for the next frame. Both waits are timed now.
- **WS2812:** the documented latch time was the original part's 50 µs; the WS2812B-V5 needs over 280 µs. The
  gap is now 300 µs (`WS2812_RESET_US`) and the test decoder checks the low time itself.
- **Host helpers:** the decoders check every packet's length (a token with no field, a CAN header with no data,
  an ACK with an extra byte); CAN remote frames have no data field (`dlc` gives the requested length); the
  assembler rejects shift thresholds above 16.
- **Docs:** the reset state per pin type (PIN5..PIN10 drive 0 and have no output enable); manual bring-up sets
  the idle level before the direction and `SET_COUNT` with `SET_BASE`; the host read commits at a word's first
  data bit, CS set-up and hold, and the status byte only means "room for one"; the physical summary gives exact
  slack, the fanout and slew warnings and which stage each cell count comes from; the routed critical path starts
  at the host write address, not where synthesis put it.

**Not changed.**
- The physical numbers above (slack to four decimals, 171 fanout warnings, the routed critical path) are quoted
  from the review, which read them in the run's `GDS_logs` archive; downloading that archive needs a GitHub
  sign-in, so they were not re-read here. They agree with the summary numbers already recorded.
- Fixing the fanout and slew warnings would need a new layout and sign-off; none fails timing.
- A USB packet encoding that fits an 8-byte DATA packet in the FIFO, so the board driver could send it: a
  firmware redesign, left for later.

## Independent review (2026-11-24)

An independent review of the repository, run with a second AI assistant from a written audit brief, reproduced
every result (pytest, cocotb, lint, formal, random lock-step, synthesis, CI) and found no RTL defect and no
mismatch between the RTL and the ISS. Its findings were in the protocol programs, the host software, the
testbench and the documentation. Each was reproduced here before it was fixed, and each fix has a test that fails
without it. No RTL file changed, so the signed-off netlist stands.

**Fixed.**
- **CAN: no stuff bit after the CRC.** ISO 11898-1 stuffs the whole CRC sequence, so when the last CRC bit ends a
  run of five, a stuff bit precedes the delimiter. `can_tx` never checked after the 15th bit: identifier 0x033
  with data 01 put six recessive bits on the bus (a stuff error for every receiver), and 0x02B with data 01 made
  it sample the ACK slot one bit early and report a false missing ACK. The CRC loop now checks for a stuff bit
  before it exits (and is 5 instructions shorter, 29). The CAN model checks the trailing stuff bit and the
  delimiter, and the test sends both frames; 306 random frames were also checked against the model's encoder.
- **I2C: set-up times after clock stretching.** `i2c_ctrl` waited its four set-up ticks before the wait for SCL
  to rise, so a target that stretched SCL after the ACK left about 2 µs before a repeated START or STOP
  (limits 4.7 µs and 4.0 µs at 100 kHz). The delay now follows the wait, and both waits time out into the stuck
  handler. The target model can stretch after the ACK, and the controller test does.
- **Open-drain pins driven high while loading.** Every loader set the pins (`set pins, 1`) before it set
  `PAD_OD`, so an I2C or 1-Wire line was driven high, push-pull, for the rest of the load (about 6 000 cycles
  over SPI at clk/16). The loaders (`Driver`, the cocotb `TB`, load images) now set `PAD_OD` first, and
  `test_loader_safety.py` checks every cycle of a load.
- **Board driver.** The example called `DemoBoard()`, which raises once the firmware has created the board at
  boot, and did not select `RPMode.ASIC_RP_CONTROL`, without which the RP2 does not drive `ui_in`. It now uses
  `DemoBoard.get()` and sets the mode (checked against the tt-micropython-firmware source). Loading a second
  image disabled the running engine and overwrote the other engine's open-drain and filter masks: images now
  carry the masks, and the driver ORs them into the chip's and keeps the running engines' `CTRL.EN` bits.
- **`read_imem` moved pins.** The injected `JMP` carried side-set value 0, so on an engine with a non-optional
  side-set (SPI with CPOL 1) it pulled SCK low. All three `read_imem` versions clear side-set around the `JMP`
  and restore `ORG` and `PINCTRL1`.
- **Assembler checks:** `.entry` beyond the first 32 instructions (the loader's injected `JMP` cannot reach it),
  `.wrap_target` after `.wrap`, empty programs, `out_count` over 16, `set_count` over 5, operands on `nop`, and a
  side-set expression with spaces (`side 1 - CPOL` used to encode `side 1`).
- **USB receive tolerance.** At 50 MHz `usb_rx` decoded an 8-byte packet only with the sender between 0 and
  +0.3 % of 1.5 Mbit/s: it sampled at about 61 % of each bit, and a slightly slow sender made it read the first
  EOP bit as data. The first sample is now one tick earlier, and `usb_rx_decode` drops one extra bit after whole
  bytes (the CRC still checks the packet). The window is ±0.4 % (±2 % for a handshake) at 40, 50 and 60 MHz.
  This is still short of the 1.5 % that USB allows a low-speed device, because `usb_rx` synchronizes only on
  SYNC; that is now documented. The decoders also return "invalid" instead of raising on a short word list.
- **Testbench.** The lock-step checker skipped cycles with X or Z instead of failing, and started after the reset,
  so it never compared the reset cycles. It now fails on X or Z and starts one clock after time zero. It also
  runs on gate-level netlists: it reads only pins, so the old reason for turning it off ("no internal state")
  did not hold. A new formal job, `quiet.sby`, proves through the pins alone that nothing is driven from reset
  until the host first selects the chip. `cov_report.py --require-all` fails on any missed bin.
- **Docs:** host `EXEC` on a running engine (it stretches the interrupted delay, restarts a timed WAIT or an
  IRQ WAIT, and can drop a pending `OUT EXEC`); a round-trip note for SPI MISO, JTAG TDO and SWD reads (clkdiv 4
  or more on the board until its delays are measured); the datasheet's bring-up steps (`PAD_OD`, pin
  directions, `CTRL` bits); stale 60 MHz numbers in the programming guide example, the glitch filter, the SPI and
  JTAG rows and the `i2c_timing` docstring; the old area figure; why there are about as many hold delay cells as
  flip-flops (the library has no enable flip-flop).

**Not changed.**
- Edge resynchronization in `usb_rx`, which full USB tolerance needs: a new receiver design, not a fix.
- Exact slow-corner slack and which nets have the six max-slew warnings: only in the `gds` run's log archive.
- Gate-level simulation with SDF delays: static timing analysis signs off timing; the netlist tests are
  functional.
- Covers for `clkdiv.sby` and `opendrain.sby`: their properties live in RTL files, which stay as signed off.

## Review for real hardware (2026-11-23)

A review of the whole project for edge cases found no defect in the RTL; every finding was in the software,
the documentation or the assumptions around the chip. The RTL is unchanged apart from a comment, so the
signed-off netlist stands.

**Fixed.**
- **Host SPI read margin.** The docs allowed SCK up to clk/8. MISO changes two to three cycles after the
  synchronized SCK falls, so at clk/8 reads only work with ideal pads: in the ISS one cycle of extra MISO delay
  (pads, the Tiny Tapeout multiplexer, or SCK's phase against the clock) shifts every read by one bit. The docs,
  datasheet, MicroPython driver note and the RTL comment now say SCK at most clk/16 (about 3 MHz at 50 MHz),
  which tolerates four cycles. `SpiDriver` gained `miso_delay` to model it, and a test pins the margins.
- **Programs and helpers were tuned for 60 MHz** while the chip is rated for 50 MHz. With the old defaults at
  50 MHz, low-speed USB ran at 1.25 Mbit/s, CAN at 417 kbit/s, the UART helper 17 % slow, 1-Wire sampled reads at
  18 µs (the limit is 15 µs) and WS2812 T0H was 420 ns (limit 380 ns). Now `regmap.F_CLK` (50 MHz) is the
  default clock: the `bitweaver.proto` helpers default to it, `.clkdiv` accepts an expression of `F_CLK`
  (`ws2812`, `onewire`, `usb_tx`, `usb_rx` use one), the loader takes `--f-clk`, and `can_clkdiv` is new. The
  cocotb tests pass the 60 MHz simulation clock explicitly (`bwtb.F_CLK`), so they load exactly the dividers
  they did before. `sw/tests/test_clock_and_host_timing.py` checks the timings in real units at 50 MHz.
- **Docs:** FIFO chaining to several engines (an engine whose TX FIFO is full misses that word), `CLKDIV_INT` 0
  only with `FRAC` 0, `HOST_IRQ` can glitch (use a level-sensitive interrupt), long FIFO bursts are not flow
  controlled, and the external parts each protocol needs (pull-ups, a CAN transceiver, the USB pull-up, level
  shifting for 5 V devices).

**Decided not to change.**
- Shifting MISO on the SCK rising edge would give reads almost a full SCK period and allow faster host clocks,
  but it changes signed-off RTL and the cycle model; clk/16 already carries every protocol.
- The tile count (8x4 at about 25 % utilisation) is a fixed fact; routing, not logic, set it.

## Final report (2026-11-20)

**Status: ready to submit.** On 144966f every workflow is green: `gds`, `precheck`, `gl_test` and `viewer` (the
Tiny Tapeout hardening), plus `test`, `lockstep`, `formal` and `docs`. The commits after it change only
documentation, so their `gds` runs harden the same RTL and configuration again.

**What was built.** Bitweaver, `tt_um_nnphanusf_bitweaver`, 8x4 tiles on IHP SG13CMOS5L at 50 MHz:
- two programmable bit-banging engines sharing a 64 x 16-bit instruction memory;
- a PIO-class ISA, extended with a timed WAIT, a bit-level assist unit (NRZI, bit stuffing, Manchester,
  programmable CRC), open-drain pads, input glitch filters, a timestamp source, and FIFO joining and chaining;
- a register-mapped SPI host port with an interrupt line.

The software and docs around it:
- the toolchain in `sw/bitweaver`: the register map as the single source of truth, an assembler, a loader, and a
  cycle-accurate simulator, which is the reference model;
- a MicroPython driver for the demo board;
- 18 protocol programs;
- the documentation in `docs/`.

**Hardening (144966f, LibreLane via `tt-gds-action`).**
- 21 201 standard cells after place and route: 2 201 flip-flops, 4 846 buffers and 2 261 hold delay cells.
  Placement utilisation is 24.8 %.
- Routing: Metal3 at 46 % with no overflow; 1.54 m of wire; zero routing violations.
- Timing at 50 MHz: met in all three corners. The worst register-to-register setup slack is under 1 ns in the
  slow corner and about 8 ns typical. There are no hold or max-cap violations and 6 max-slew warnings in the
  slow corner.
- Checks: LVS, antenna and the illegal-overlap check are clean. Precheck passes, including the KLayout
  SG13CMOS5L DRC.
- Power: about 8.1 mW typical.
- Run times: build 30 min, precheck 62 min.

Full numbers are in `docs/results.md`.

**Verification.**
- pytest: 165 tests (77 at the time of this report; 16 added on 2026-11-23, then 26, 24 and 22 with the three
  reviews on 2026-11-24).
- cocotb: 58 tests (two added with the second review), all in cycle-by-cycle lock-step with the simulator. All
  58 passed on the routed netlist in CI in lock-step (3b3beae), and on the local Yosys/IHP netlist.
- Constrained-random lock-step (Verilator): 100M cycles over 1000 seeds with no mismatch; CI runs 10M cycles on
  every push.
- Coverage: functional 465/465 bins, line 100 % (285/285), toggle 98.3 % (8444/8590). The 146 untoggled points
  are all constant bits, listed in `docs/verification.md`.
- Formal: six SymbiYosys jobs pass in CI: the FIFO, the host port (no deadlock), engine invariants, the clock
  divider, open-drain safety, and (since 2026-11-24) a quiet chip from reset until the host selects it. All six
  are unbounded proofs (PDR, or k-induction for open drain).
- Equivalence: a Yosys proof for the instruction-memory timing rewrite.

**Protocols working**, each checked against an independent model of the other end:
- UART at 9600–921600 baud, 8N1 and 8E1;
- SPI controller, modes 0–3; SPI target;
- I2C controller at 100 and 400 kHz, with every timing limit checked; I2C target on two engines;
- WS2812, JTAG, SWD and PS/2;
- CAN 2.0A at 500 kbit/s and 1 Mbit/s;
- low-speed USB packets;
- 1-Wire;
- SPI and I2C on both engines at once.

The simulations run at 60 MHz. At the 50 MHz sign-off clock the dividers are computed from the clock (since
2026-11-23 they default to 50 MHz) and every clock-bound rate limit scales by 5/6. Low-speed USB has exact bit
timing only at 60 MHz.

**Cut**, with reasons in the decisions below:
- two of the four engines, and the instruction memory's host read port: the four-engine design did not route;
- 10BASE-T: the host port's bandwidth is below the line rate;
- 60 MHz, replaced by the planned 50 MHz fallback.

**Known limits.**
- Low-speed USB bit edges move by up to one clock at 50 MHz.
- CAN has no arbitration or error frames.
- USB works at the packet level only, not as a device stack.
- Jump targets must be in the first 32 instructions of a program.
- The slow-corner setup margin is under 1 ns.
- The host port needs SCK at most clk/16 on real hardware.
- No silicon has been tested.

**Steps left for you.**
1. Merge `phase0-setup` into `main` (done 2026-11-23, by fast-forward).
2. Keep the repository public (it is public now).
3. Submit through the Jane Street form when it is posted, using the green `gds` run. The deadline is 2027-01-18
   and the RTL freeze is 2027-01-08.
4. Optional: every push re-runs `gds`, now about 1.5 h with precheck; add `paths-ignore` for doc-only pushes if you
   want.
5. Optional: the exact slack values are only in the `GDS_logs` artifact (it needs a GitHub login to download).

## Timing closure (2026-11-20)

**The two-engine design routes; its first run missed setup at 60 MHz.** The `gds` run of 297df42 finished in
4 h 46 min: detailed routing reached zero violations after 3 h 23 min; Magic DRC, LVS and antenna checks are
clean, with no hold violation in any corner. Setup failed in the slow and typical corners (typical: 12
endpoints) and passed in the fast corner (reg-to-reg worst slack about +4 ns). The placement-based timing after
clock-tree synthesis had shown no setup violation, so most of the loss came from routing: Metal3, the only
horizontal signal layer, ended global routing at 79.7 % with 14 446 overflow, and the log reports max-slew and
max-cap violations in every corner after routing. The job log truncates the slack columns of the timing table,
so the exact slacks are only in that run's `GDS_logs` artifact (downloading it needs a GitHub login).

**Fixes, following the brief ("fix critical paths ... confirm 60 MHz or fall back to 50").**
- RTL, exact retimings (no cycle changes): the clock divider's `cnt == 0` is now a registered flag, so an
  engine's tick comes from a flip-flop (proved equal to the counter, `formal/clkdiv.sby`); the instruction-memory
  ports read each word's next value, so no compare or bypass mux follows the 64:1 read (proved equivalent to the
  previous memory with Yosys `equiv_induct`). Delay-driven ABC estimate of the slow-corner logic path 7.57 →
  6.47 ns.
- Flow (`src/config.json`): `CLOCK_PERIOD` 20 (50 MHz, the brief's fallback and the Tiny Tapeout IHP default);
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

**Result.** The `gds` run of 144966f is green: `gds` 30 min, `precheck` 62 min, `gl_test` 56/56 and `viewer`
all pass. Global routing left Metal3 at 46 % with no overflow, and detailed routing reached zero violations in
18 min. The routed design meets 50 MHz in all corners (numbers in the final report above and in
`docs/results.md`).

**Latch warning left in place (decision).** The GDS summary shows Yosys "latch inferred" warnings for `bi`, the
integer loop variable of the `MOV` bit reverse in `bw_sm`. Nothing reads it, so it is removed, and the netlist has
no latch cells. A loop-free rewrite was proved equivalent (Yosys `equiv_induct`, 1960 of 1960). The cocotb suite,
all five formal jobs and the 100M-cycle lock-step all passed on it, but it was not adopted. It changes the mapped
netlist (14 975 cells instead of 14 922; ABC's slow-corner estimate moves by −0.45 ns with one script and
+0.09 ns with the other). With under 1 ns of routed slow-corner slack, a cosmetic warning does not justify
re-rolling place and route on a signed-off design.

## Routing (2026-11-19)

**The four-engine design did not route; the chip is now two engines.** All three `gds` runs of the four-engine
RTL (793167b, a1e294a, ab202af) were cancelled at GitHub's 6-hour job limit. The raw log of ab202af shows why:
synthesis to post-CTS resizing took 6 minutes, but global placement reported 20–45 % of routing tiles
overflowed at 50 % utilisation, global routing took 48 minutes and finished congested, and detailed routing had
296 679 violations after its first 3-hour pass and 348 317 partway through the second. A Tiny Tapeout tile gives
signals only Metal2–Metal4, and the design was dominated by wide multiplexers (five 64:1 x 16 read ports over the
1024 instruction-memory bits, four engines). Following the brief's cut order (engine count first), commit 297df42:
- two engines instead of four (every protocol program still fits; none needs more than two engines);
- no host read port on the instruction memory (words are read back through an engine: EXEC a `JMP`, read `EXEC`);
- `PL_TARGET_DENSITY_PCT` 60 → 40 to spread placement.
Result: 15 288 IHP cells, 230 944 um2 (57 % of before), about 30 % utilisation; ABC slow-corner estimate 8.67 ns.

## Phase 0: Setup and spec
- [x] Repo from `ttihp-verilog-template`; OSS CAD Suite 2026-11-16 in `~/tools/oss-cad-suite`
- [x] IHP SG13CMOS5L liberty and cell models in `~/tools/pdk/ihp-sg13cmos5l/libs.ref` (from the IHP open PDK repo)
      for local area estimates and gate-level simulation
- [x] `info.yaml` (8x4, all source files; 60 MHz, 50 MHz since 2026-11-20), `docs/info.md` datasheet
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
- [x] Full GDS + precheck + GL test green: four engines did not route (3 runs cancelled at 6 h); two engines
      green on 144966f at 50 MHz

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
- [x] Coverage: functional 465/465 bins, Verilator line 100 % (285/285), toggle 98.3 % (8444/8590) (two engines)
- [x] GL simulation of the full suite on the Yosys/IHP netlist locally and on the routed netlist in CI (56/56)
- [x] Timing, first pass: the two-engine GDS routed but missed 60 MHz (slow and typical corners); two exact RTL
      retimings, repair after global routing, 50 MHz fallback (2026-11-20)
- [x] Timing: `gds` green at 50 MHz, all corners (144966f)

## Phase 4: Stretch protocols
- [x] WS2812, JTAG, SWD, PS/2, CAN 2.0A (TX/RX, BAU stuffing + CRC15), low-speed USB packets (TX/RX, BAU NRZI +
      stuffing + CRC16), plus 1-Wire
- [x] 10BASE-T: **cut** (see decisions)

## Phase 5: Write-up
- [x] `architecture.md`, `isa.md`, `programming_guide.md`, `verification.md`, `results.md`, `ai_usage.md`, README
- [x] Final numbers from the full-design GDS run into `results.md`

## Decisions
- **Reset:** asynchronous assert, synchronous release through a 2-FF synchronizer. IHP has no flip-flop without a
  reset pin, so async reset costs no area. The ISS models the two release cycles.
- **Area guardrail (revised 2026-11-19):** the four-engine design (27.4K cells, 39 % by cell area, 50 % placement
  utilisation) passed the cell-count reasoning but did not route: routing, not area, was the binding constraint,
  because the design is mostly wide multiplexers and a TT tile only has Metal2–Metal4 for signals. Cut to two
  engines and removed the IMEM host read port (15.3K cells). The earlier reasoning that 39 % utilisation "leaves
  far more than 25 % headroom" was wrong for this kind of logic.
- **Clock (2026-11-20): 50 MHz sign-off.** The routed two-engine design missed 60 MHz in the slow and typical
  corners. The brief names 50 MHz as the fallback and it is the Tiny Tapeout IHP template default. RTL and programs are
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
  8-word FIFO cannot hold even the 64-bit preamble plus a minimal frame. The brief says to attempt it only if the timing
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
