# Results

## Summary

| | |
|---|---|
| Process, size | IHP SG13CMOS5L, Tiny Tapeout 8x4 tiles |
| Clock | 50 MHz sign-off (`CLOCK_PERIOD` 20 ns), the planned fallback: at 60 MHz the routed design missed setup in the slow and typical corners (history below) |
| Cells (Yosys + ABC, IHP typical library) | 14 922, about 243 000 um2, about 32 % of the core (24 076 instances after place and route, with buffers and 2 159 hold-fix delay cells) |
| Critical path estimate (ABC delay-driven mapping, slow 1.08 V / 125 C, no wire load) | 6.47 ns (7.57 ns before the timing fixes); routed wires add far more, see the history |
| Engines | 2, sharing a 64 x 16-bit instruction memory |
| Protocols demonstrated | 12 (table below), plus two at once |

The two-engine design routes cleanly (DRC, LVS, antenna, hold); its first run missed setup at 60 MHz, and the
timing fixes and the 50 MHz constraint are in the history table at the end. The four-engine design did not route;
see the history table and [architecture.md](architecture.md#why-two-engines).

## Protocols

All rates are with a 60 MHz clock, the clock the simulations run at. "Tested" means checked in simulation against
an independent model of the other end of the wire (see [verification.md](verification.md)); "limit" is what the
program's timing allows. The chip signs off at 50 MHz. Engines count clock cycles, so the programs are unchanged:
the timing helpers take the clock (`f_clk`) and compute the dividers, and every limit scales by 5/6 (SPI 6.25 MHz at
clkdiv 2, JTAG TCK 6.25 MHz). Low-speed USB was tested at 60 MHz, where a bit is exactly 40 clocks; at 50 MHz the
divider is fractional (33.3 clocks per bit), so bit edges move by up to one 20 ns clock.

| Protocol | Program (instructions) | Engines | Tested | Limit and notes |
|---|---|---|---|---|
| UART 8N1 / 8E1 TX | `uart_tx` (4) | 1 | 9600 – 921 600 baud | 8 ticks per bit: 7.5 Mbaud at clkdiv 1 |
| UART RX | `uart_rx` (11) | 1 | 9600 – 921 600 baud | samples mid-bit, framing error flag |
| SPI controller, modes 0–3 | `spi_ctrl_cpha0/1` (11) | 1 | 1 MHz, 7.5 MHz | 4 ticks per bit; 7.5 MHz (clkdiv 2) leaves MISO time for the input synchronizer |
| SPI target, mode 0 | `spi_target` (23) | 1 | 1 MHz, 2.5 MHz | about 3 MHz SCK |
| I2C controller | `i2c_ctrl` (25) | 1 | 100 kHz, 400 kHz | all standard/fast-mode timing limits met; bit split 5/5 ticks (standard) or 6/4 (fast) |
| I2C target | `i2c_cond` (19) + `i2c_target` (41) | 2 | 100 kHz, 400 kHz | clock stretching while the host supplies read data |
| WS2812 | `ws2812` (4) | 1 | 857 kHz | T0H 350 ns, T1H 817 ns |
| JTAG controller | `jtag` (2) | 1 | TCK 7.5 MHz, 1.7 MHz | 4 ticks per TCK |
| SWD controller | `swd` (16) | 1 | SWCLK 3.75 MHz | 4 ticks per SWCLK |
| PS/2 receiver | `ps2_rx` (6) | 1 | 12.5 kHz device clock | glitch filter on CLK, watchdog drops cut frames |
| CAN 2.0A | `can_tx` (34), `can_rx` (12) | 1 each | 500 kbit/s, 1 Mbit/s | 10 ticks per bit; stuffing and CRC15 in the BAU; hard sync on SOF only |
| Low-speed USB packets | `usb_tx` (26), `usb_rx` (14) + `usb_se0` (7) | 1, 2 | 1.5 Mbit/s | NRZI, stuffing, CRC16 in the BAU; packet level, not a device stack |
| 1-Wire master | `onewire` (28) | 1 | standard speed | presence detection with a timed WAIT |
| SPI + I2C at once | 36 instructions in total | 2 | as above | one shared host port |

**Cut:** 10BASE-T. The BAU's Manchester encoder works (tested), but the host port moves at most 7.5 Mbit/s raw
(SCK ≤ clk/8), less than Ethernet's 10 Mbit/s, and an 8-word FIFO cannot buffer even the preamble and a minimal
frame.

## History

Area and timing numbers, newest first. Cell counts from Yosys generic synthesis unless noted; GDS numbers come from
the `gds` workflow's LibreLane reports.

| Date | Design state | Cells | Notes |
|---|---|---|---|
| 2026-11-20 | Timing fixes on the two-engine design | 14 922 IHP cells (Yosys + ABC, typ corner), 242 717 um2 | RTL, both exact: the divider's `cnt == 0` is a registered flag (proved in `formal/clkdiv.sby`), and the instruction-memory ports read each word's next value, so no compare or bypass mux follows the 64:1 read (Yosys equivalence proof). Delay-driven ABC estimate of the slow-corner logic path 7.57 → 6.47 ns; the longest path now starts at the instruction register instead of the divider counter. Flow: `CLOCK_PERIOD` 20 (the 50 MHz fallback), design-rule and timing repair after global routing, 800 um maximum wire length, Magic DRC off (the precheck job runs the PDK's KLayout SG13CMOS5L deck, the DRC Tiny Tapeout signs off on for IHP). GDS result: next row when it lands. |
| 2026-11-19 | Two engines, first GDS (297df42) | 24 076 instances after place and route, 2 199 flip-flops, 2 159 hold-fix delay cells | **Routed; failed setup at 60 MHz.** 4 h 46 min. Global routing: Metal2 70.2 %, Metal3 79.7 % with 14 446 overflow (Metal3 is the only horizontal signal layer), Metal4 29.7 %; 3.95 m of global wire, 2.81 m after detailed routing. Detailed routing took 3 h 23 min from 37 679 violations after the first pass to zero. Magic DRC (59 min), LVS and antenna clean; no hold violation in any corner. Setup: the placement-based timing after CTS showed none, the routed design failed in the slow and typical corners (typical: 12 endpoints) and passed in the fast corner (reg-to-reg worst slack about +4 ns); max-slew and max-cap violations in all corners. The log's timing table is cut at 80 columns, so the exact slacks are only in the run's `GDS_logs` artifact. |
| 2026-11-19 | Two engines, IMEM without host read port, `PL_TARGET_DENSITY_PCT` 40 | 15 288 IHP cells (Yosys + ABC, typ corner) | Area 230 944 um2 (57 % of the four-engine design), about 30 % of the core. ABC critical-path estimate 8.67 ns at the slow corner without wire load (target 16.67 ns). |
| 2026-11-17 | Four engines, full GDS attempts (793167b, a1e294a, ab202af) | n/a | **Did not route.** All three `gds` runs were cancelled at GitHub's 6-hour limit. Synthesis to post-CTS resizing took 6 minutes; global placement already reported 20–45 % of routing tiles overflowed at 50 % utilisation; global routing took 48 min and ended with congestion; detailed routing had 296 679 violations after its first pass (3 h) and 348 317 partway through the second. Cause: wiring demand of the mux-heavy design (five 64:1 x 16 read ports on 1024 IMEM bits, four engines) far above what Metal2–Metal4 offer. |
| 2026-11-17 | Full cluster RTL: 4 engines with BAU, 64x16 IMEM, host SPI, pin mux (commit before first full GDS) | 27412 IHP cells (Yosys + ABC, typ corner) | Area 385815 um2, about 39 % of the 8x4 core. Hierarchy before flattening (generic cells): IMEM 12134 (1024 flops + four 64:1 read ports), one engine 4061 (FIFO 1056, BAU 220, divider 197), host port 230, top level 2150. ABC critical-path estimate 9.07 ns at the slow 1.08 V / 125 C corner, without wire load (target 16.67 ns). |
| 2026-11-17 | Same, after GDS (commit 3627c90) | 300 IHP cells | 8x4 tiles at 60 MHz: utilisation 0.408 %, wire length 6576 um, 54 flip-flops, no setup or hold violations. Precheck all green (SG13CMOS5L DRC, pin, boundary, layer, cell name checks). GDS 54 min, precheck 22 min on GitHub runners. |
| 2026-11-16 | Phase 0 skeleton + SPI bring-up probe | 138 (generic) | Every input pin now drives logic. The first GDS run failed with "19 critical disconnected pins" because the all-idle skeleton had no logic. |
