# Results

## Summary

| | |
|---|---|
| Process, size | IHP SG13CMOS5L, Tiny Tapeout 8x4 tiles |
| Clock | 50 MHz (`CLOCK_PERIOD` 20 ns), met in all three corners after routing. This is the planned fallback: the first routed run missed the original 60 MHz target in the slow and typical corners (history below) |
| Timing after routing (LibreLane STA, 50 MHz) | no setup or hold violation in any corner. Worst setup / hold slack: slow (1.08 V, 125 °C) +0.93 / +0.60 ns, typical (1.2 V, 25 °C) +8.02 / +0.29 ns, fast +12.07 / +0.11 ns, against a 20 ns propagated clock with 0.25 ns uncertainty and 4 ns input and output delays (post-route STA of the 2026-11-24 run, from its `GDS_logs` as read by the second independent review) |
| Design-rule checks in the STA (warnings in this flow) | no max-cap violations; 6 max-slew violations in the slow corner, all on one NOR4 output net (2.602 ns against a 2.507 ns limit, 0.094 ns over); 171 max-fanout violations per corner against the flow's limit of 8 (one clock-tree leaf drives 17 loads). None fails timing, and none can change without a new layout |
| Cells after place and route | 21 201 standard cells in the place-and-route cell table (fill and tap cells excluded): 2 201 flip-flops, 4 846 buffers, 2 261 hold delay cells; placement utilisation 24.8 %. The flow's final metrics count 21 318 instances and 27.9 % utilisation: a later stage, with more cell classes counted |
| Cells (Yosys + ABC, IHP typical library) | 14 922, about 243 000 um2 |
| Routing | 1.54 m of wire after detailed routing, zero violations; global routing Metal2 39 %, Metal3 46 %, Metal4 3 %, no overflow |
| Power (LibreLane IR report, typical corner, default switching activity) | 8.1 mW; worst IR drop 0.2 mV (0.02 %) |
| Critical path estimate (ABC delay-driven mapping, slow 1.08 V / 125 C, no wire load) | 6.47 ns (7.57 ns before the timing fixes); routed wires add far more, see the history. The routed worst path starts at the host write address instead ([architecture.md](architecture.md#timing)) |
| Engines | 2, sharing a 64 x 16-bit instruction memory |
| Protocols demonstrated | 12 (table below), plus two at once |

The chip hardens cleanly at 50 MHz: `gds`, `precheck` (the PDK's KLayout SG13CMOS5L DRC and every Tiny Tapeout
check), `gl_test` (the cocotb suite on the routed netlist) and `viewer` are green, with LVS and antenna checks
clean. The first two-engine run missed 60 MHz, and the history table at the end describes the timing fixes. The
four-engine design did not route; see the history table and [architecture.md](architecture.md#why-two-engines).

About one hold delay cell per flip-flop is expected with this library: it has no flip-flop with a clock enable
(only `dfrbp`/`dfrbpq`, scan and latch cells), so every register that keeps its value (the 1024 instruction-memory
bits, the FIFOs, the configuration) feeds Q back to D through a multiplexer. Those paths are only a gate or two
long, shorter than the hold margin plus clock uncertainty in the fast corner, so the flow pads them. The design
has no gated or derived clocks.

The GDS summary lists two kinds of synthesis warning, neither a defect:
- "Replacing memory `\mem` with list of registers" means the FIFO storage is built from flip-flops, as intended.
- "Latch inferred for signal `\bw_sm.\bi`" refers to `bi`, the integer loop variable of the `MOV` bit reverse
  (`src/bw_sm.v`). It holds its value outside the reverse case, but nothing reads it, so it is removed. The netlist
  has no latch cells: the cell table lists only `dfrbpq` flip-flops.

A loop-free rewrite of the reverse was proved equivalent (Yosys `equiv_induct`, 1960 of 1960 equivalence cells).
It was not adopted: it changes the mapped netlist (14 975 cells instead of 14 922), and with under 1 ns of
slow-corner slack the signed-off netlist is kept as it is.

## Protocols

The "Tested" rates are with a 60 MHz clock, the clock the cocotb simulations run at. "Tested" means checked in
simulation against an independent model of the other end of the wire (see [verification.md](verification.md));
"limit" is what the program's timing allows. The chip signs off at 50 MHz. Engines count clock cycles, so the
dividers are computed from the clock: the helpers in `bitweaver.proto` and the programs with absolute timing
(`ws2812`, `onewire`, `usb`) default to the rated 50 MHz (`regmap.F_CLK`), and the tests pass 60 MHz. Limits that
come from the clock scale by 5/6 at 50 MHz (SPI controller 6.25 MHz at clkdiv 2, JTAG TCK 6.25 MHz, UART 6.25 Mbaud).
At 50 MHz, `sw/tests/test_clock_and_host_timing.py` checks the WS2812 pulses (T0H 360 ns, T1H 840 ns), 1-Wire
reads against a device that releases a 0 at 16 µs, the CAN bit rates, and low-speed USB packets in both
directions. At 50 MHz the USB divider is fractional (33.3 clocks per bit, 0.04 % fast), so `usb_tx` bit edges move
by up to one 20 ns clock. `usb_rx` synchronizes once, on the first K of SYNC, and does not follow later edges: it
receives an 8-byte DATA packet (the longest low-speed packet) from a sender within ±0.4 % of 1.5 Mbit/s, and a
handshake within ±2 %. The USB specification lets a low-speed device be 1.5 % off, so `usb_rx` suits crystal or
PLL-clocked peers, not ones with a ceramic resonator. `usb_tx` starts a packet at its first word and stalls on the
wire if a later word is late, so the host queues each packet with `push_packet`: it stops the engine, preloads the
8-word FIFO and streams the rest, which then has about 43 µs for an 8-byte DATA packet. Through the host port at
SCK = clk/16 that works at 50 MHz (tested in RTL and in `test_clock_and_host_timing.py`); streaming the words as
the FIFO drains does not. Handshakes, tokens and empty DATA packets fit the FIFO whole, so the bit-banged
demo-board driver can send those, but not longer DATA packets.

The 60 MHz simulations have ideal pads. On the board, a signal that the external device returns in answer to an
engine's clock edge (SPI MISO, JTAG TDO, SWD reads) has two ticks minus the two-cycle input synchronizer to arrive:
at clkdiv 2 that is 40 ns at 50 MHz for both pad and multiplexer paths plus the device's output delay. Until the
board's delays are measured, run these programs at clkdiv 4 or more (SCK, TCK or SWCLK about 3 MHz).

| Protocol | Program (instructions) | Engines | Tested | Limit and notes |
|---|---|---|---|---|
| UART 8N1 / 8E1 TX | `uart_tx` (4) | 1 | 9600 – 921 600 baud | 8 ticks per bit: 6.25 Mbaud at clkdiv 1 (50 MHz) |
| UART RX | `uart_rx` (11) | 1 | 9600 – 921 600 baud | samples mid-bit, framing error flag |
| SPI controller, modes 0–3 | `spi_ctrl_cpha0/1` (11) | 1 | 1 MHz, 7.5 MHz (60 MHz clock) | 4 ticks per bit: 6.25 MHz at clkdiv 2 (50 MHz) with ideal pads; clkdiv 4 or more on the board (see above) |
| SPI target, mode 0 | `spi_target` (34) | 1 | 1 MHz, 2.5 MHz | about 3 MHz SCK; MISO driven only while CS is low, so targets can share it; a byte cut short by CS is dropped |
| I2C controller | `i2c_ctrl` (27) | 1 | 100 kHz, 400 kHz | all standard/fast-mode timing limits met, also when a target stretches SCL before or after the ACK; bit split 5/5 ticks (standard) or 6/4 (fast) |
| I2C target | `i2c_cond` (19) + `i2c_target` (42) | 2 | 100 kHz, 400 kHz | clock stretching while the host supplies read data; each data bit is on SDA 16 cycles (320 ns at 50 MHz) before SCL is released |
| WS2812 | `ws2812` (4) | 1 | 857 kHz | 833 kHz at 50 MHz: T0H 360 ns, T1H 840 ns (857 kHz, 350 / 817 ns at 60 MHz); 300 µs low between frames latches every revision, including the WS2812B-V5 (> 280 µs) |
| JTAG controller | `jtag` (2) | 1 | TCK 7.5 MHz, 1.7 MHz (60 MHz clock) | 4 ticks per TCK: 6.25 MHz at clkdiv 2 (50 MHz) with ideal pads |
| SWD controller | `swd` (16) | 1 | SWCLK 3.75 MHz | 4 ticks per SWCLK; JTAG-to-SWD switch tested against a target that starts in JTAG; `swd_transfer` reads the ACK first and retries on WAIT |
| PS/2 receiver | `ps2_rx` (8) | 1 | 12.5 kHz device clock | glitch filter on CLK; the watchdog drops a frame cut short with CLK left high or held low |
| CAN 2.0A | `can_tx` (29), `can_rx` (12) | 1 each | 500 kbit/s, 1 Mbit/s | 10 ticks per bit; stuffing (including a stuff bit after the CRC) and CRC15 in the BAU; hard sync on SOF only |
| Low-speed USB packets | `usb_tx` (26), `usb_rx` (14) + `usb_se0` (7) | 1, 2 | 1.5 Mbit/s | NRZI, stuffing, CRC16 in the BAU; packet level, not a device stack; receive tolerance ±0.4 %; transmit needs each packet preloaded (see above) |
| 1-Wire master | `onewire` (28) | 1 | standard speed | presence detection with a timed WAIT |
| SPI + I2C at once | 38 instructions in total | 2 | as above | one shared host port |

**Cut:** 10BASE-T. The BAU's Manchester encoder works (tested), but the host port moves about 3 Mbit/s raw at the
recommended SCK of clk/16 (6.25 Mbit/s at clk/8 even with ideal pads), far less than Ethernet's 10 Mbit/s, and an
8-word FIFO cannot buffer even the preamble and a minimal frame.

**Host port speed.** Reads need the host's SCK low time to exceed three clock cycles plus the delay the pads, the
Tiny Tapeout multiplexer and SCK's phase add to MISO, so run SCK at clk/16 or slower on real hardware
([architecture.md](architecture.md#host-port)). The demo board's MicroPython driver bit-bangs SCK far slower than
that.

## History

Area and timing numbers, newest first. Cell counts from Yosys generic synthesis unless noted; GDS numbers come from
the `gds` workflow's LibreLane reports.

| Date | Design state | Cells | Notes |
|---|---|---|---|
| 2026-11-24 | First review's fixes, same RTL and netlist (68e38d0) | unchanged | **All green: `gds`, `precheck`, `gl_test`, `viewer`** on `main` and `phase0-setup`. `gl_test` now compares every output against the reference model on every cycle from reset (lock-step) and matched on all 56 tests on the routed netlist. Post-route STA from this run's `GDS_logs`, read by the second independent review: setup slack +0.9305 ns slow, +8.0179 ns typical, +12.0699 ns fast; hold +0.6038, +0.2852, +0.1070 ns; 171 max-fanout and 6 max-slew (slow) design-rule warnings. The second review's findings were all in firmware, host software and docs. |
| 2026-11-20 | Timing fixes and 50 MHz, GDS (144966f) | 21 201 cells after place and route: 2 201 flip-flops, 4 846 buffers, 2 261 hold delay cells | **All green: `gds`, `precheck`, `gl_test`, `viewer`.** Build 30 min (the first two-engine run took 4 h 46 min); detailed routing 18 min, from 10 927 violations after the first pass to zero after the sixth; precheck 62 min; gate-level tests on the routed netlist 18 min. Global routing: Metal2 39 %, Metal3 46 %, Metal4 3 %, no overflow (first two-engine run: Metal3 79.7 % with 14 446 overflow); 1.95 m of global wire, 1.54 m after detailed routing. Placement-based timing after CTS: no setup violation; 2 028 hold endpoints fixed with 2 261 delay cells; the timing repair after global routing found no setup or hold violation left. Routed STA: setup and hold met in all corners, worst reg-to-reg setup slack under 1 ns slow, about 8 ns typical; 0 max-cap, 6 max-slew violations (slow corner). LVS, antenna (after 47 diodes) and illegal-overlap checks clean. IR drop 0.02 %, 8.1 mW typical. The log cuts the slack columns at one digit; exact values are in the run's `GDS_logs` artifact. |
| 2026-11-20 | Timing fixes on the two-engine design | 14 922 IHP cells (Yosys + ABC, typ corner), 242 717 um2 | RTL, both exact: the divider's `cnt == 0` is a registered flag (proved in `formal/clkdiv.sby`), and the instruction-memory ports read each word's next value, so no compare or bypass mux follows the 64:1 read (Yosys equivalence proof). Delay-driven ABC estimate of the slow-corner logic path 7.57 → 6.47 ns; the longest path now starts at the instruction register instead of the divider counter. Flow: `CLOCK_PERIOD` 20 (the 50 MHz fallback), design-rule and timing repair after global routing, 800 um maximum wire length, Magic DRC off (the precheck job runs the PDK's KLayout SG13CMOS5L deck, the DRC Tiny Tapeout signs off on for IHP). GDS result in the row above. |
| 2026-11-19 | Two engines, first GDS (297df42) | 24 076 instances after place and route, 2 199 flip-flops, 2 159 hold-fix delay cells | **Routed; failed setup at 60 MHz.** 4 h 46 min. Global routing: Metal2 70.2 %, Metal3 79.7 % with 14 446 overflow (Metal3 is the only horizontal signal layer), Metal4 29.7 %; 3.95 m of global wire, 2.81 m after detailed routing. Detailed routing took 3 h 23 min from 37 679 violations after the first pass to zero. Magic DRC (59 min), LVS and antenna clean; no hold violation in any corner. Setup: the placement-based timing after CTS showed none, the routed design failed in the slow and typical corners (typical: 12 endpoints) and passed in the fast corner (reg-to-reg worst slack about +4 ns); max-slew and max-cap violations in all corners. The log's timing table is cut at 80 columns, so the exact slacks are only in the run's `GDS_logs` artifact. |
| 2026-11-19 | Two engines, IMEM without host read port, `PL_TARGET_DENSITY_PCT` 40 | 15 288 IHP cells (Yosys + ABC, typ corner) | Area 230 944 um2 (57 % of the four-engine design), about 30 % of the core. ABC critical-path estimate 8.67 ns at the slow corner without wire load (target 16.67 ns). |
| 2026-11-17 | Four engines, full GDS attempts (793167b, a1e294a, ab202af) | n/a | **Did not route.** All three `gds` runs were cancelled at GitHub's 6-hour limit. Synthesis to post-CTS resizing took 6 minutes; global placement already reported 20–45 % of routing tiles overflowed at 50 % utilisation; global routing took 48 min and ended with congestion; detailed routing had 296 679 violations after its first pass (3 h) and 348 317 partway through the second. Cause: wiring demand of the mux-heavy design (five 64:1 x 16 read ports on 1024 IMEM bits, four engines) far above what Metal2–Metal4 offer. |
| 2026-11-17 | Full cluster RTL: 4 engines with BAU, 64x16 IMEM, host SPI, pin mux (commit before first full GDS) | 27412 IHP cells (Yosys + ABC, typ corner) | Area 385815 um2, about 39 % of the 8x4 core. Hierarchy before flattening (generic cells): IMEM 12134 (1024 flops + four 64:1 read ports), one engine 4061 (FIFO 1056, BAU 220, divider 197), host port 230, top level 2150. ABC critical-path estimate 9.07 ns at the slow 1.08 V / 125 C corner, without wire load (target 16.67 ns). |
| 2026-11-17 | Same, after GDS (commit 3627c90) | 300 IHP cells | 8x4 tiles at 60 MHz: utilisation 0.408 %, wire length 6576 um, 54 flip-flops, no setup or hold violations. Precheck all green (SG13CMOS5L DRC, pin, boundary, layer, cell name checks). GDS 54 min, precheck 22 min on GitHub runners. |
| 2026-11-16 | Phase 0 skeleton + SPI bring-up probe | 138 (generic) | Every input pin now drives logic. The first GDS run failed with "19 critical disconnected pins" because the all-idle skeleton had no logic. |
