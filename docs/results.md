# Results

## Summary

| | |
|---|---|
| Process, size | IHP SG13CMOS5L, Tiny Tapeout 8x4 tiles |
| Clock | 60 MHz target (`CLOCK_PERIOD` 16.667 ns) |
| Cells (Yosys + ABC, IHP typical library) | 27 412, about 386 000 um2, about 39 % of the core |
| Critical path estimate (ABC, slow 1.08 V / 125 C, no wire load) | 9.07 ns |
| Engines | 4, sharing a 64 x 16-bit instruction memory |
| Protocols demonstrated | 12 (table below), plus four at once |

Numbers from the LibreLane run of the full design (utilisation, wire length, STA at every corner) are added when the
`gds` workflow for the full design finishes; see the history table at the end.

## Protocols

All rates are with a 60 MHz clock. "Tested" means checked in simulation against an independent model of the
other end of the wire (see [verification.md](verification.md)); "limit" is what the program's timing allows.

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
| UART + UART + SPI + I2C at once | 51 instructions in total | 4 | as above | one shared host port |

**Cut:** 10BASE-T. The BAU's Manchester encoder works (tested), but the host port moves at most 7.5 Mbit/s raw
(SCK ≤ clk/8), less than Ethernet's 10 Mbit/s, and an 8-word FIFO cannot buffer even the preamble and a minimal
frame.

## History

Area and timing numbers, newest first. Cell counts from Yosys generic synthesis unless noted; GDS numbers come from
the `gds` workflow's LibreLane reports.

| Date | Design state | Cells | Notes |
|---|---|---|---|
| 2026-11-17 | Full cluster RTL: 4 engines with BAU, 64x16 IMEM, host SPI, pin mux (commit before first full GDS) | 27412 IHP cells (Yosys + ABC, typ corner) | Area 385815 um2, about 39 % of the 8x4 core. Hierarchy before flattening (generic cells): IMEM 12134 (1024 flops + four 64:1 read ports), one engine 4061 (FIFO 1056, BAU 220, divider 197), host port 230, top level 2150. ABC critical-path estimate 9.07 ns at the slow 1.08 V / 125 C corner, without wire load (target 16.67 ns). |
| 2026-11-17 | Same, after GDS (commit 3627c90) | 300 IHP cells | 8x4 tiles at 60 MHz: utilisation 0.408 %, wire length 6576 um, 54 flip-flops, no setup or hold violations. Precheck all green (SG13CMOS5L DRC, pin, boundary, layer, cell name checks). GDS 54 min, precheck 22 min on GitHub runners. |
| 2026-11-16 | Phase 0 skeleton + SPI bring-up probe | 138 (generic) | Every input pin now drives logic. The first GDS run failed with "19 critical disconnected pins" because the all-idle skeleton had no logic. |
