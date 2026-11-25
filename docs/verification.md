# Verification

Bitweaver is verified in five layers. Each layer catches a different class of mistake, and no single source of
truth is trusted on its own.

| Layer | Tool | What it shows | Where |
|---|---|---|---|
| 1. Executable specification | Python ISS + pytest | the ISA, assembler and loader behave as documented | `sw/bitweaver`, `sw/tests` |
| 2. Lock-step | cocotb + Icarus, Verilator | the RTL equals the ISS on every pin, every cycle | `test/lib/bwtb.py`, `verif/lockstep` |
| 3. Independent protocol models | cocotb, cocotbext-uart/spi, own models | the programs speak the real protocols | `test/test_proto_*.py`, `test/lib` |
| 4. Formal | SymbiYosys | safety properties hold for all inputs | `formal/*.sby` |
| 5. Physical | LibreLane, gate-level simulation | the netlist after synthesis and place and route still works | `gds` workflow, `GATES=yes` |

Everything runs in CI: `test` (pytest, generated-file check, the cocotb suite), `lockstep` (constrained random,
coverage), `formal`, `gds` (hardening, precheck, gate-level tests on the routed netlist), `docs`.

## 1. The ISS as executable specification

`sw/bitweaver/iss.py` is a cycle-accurate model of the whole chip, including the host SPI port, the input
synchronizers and glitch filters, the pad logic and every engine. It was written first, together with
[isa.md](isa.md), and the RTL was then written to match it in the same structure: one `step()` computes every
next-state value from the current state and the inputs sampled at the clock edge. Because the RTL follows the model,
lock-step (layer 2) shows that the RTL implements the model exactly, and the independent protocol models (layer 3)
are what show that the model itself does the right thing.

The pytest suite (`make -C sw test`, 165 tests) checks the assembler encodings and error messages, a
disassemble/re-assemble round trip over the whole 16-bit encoding space, the generated files
(`src/bw_defs.vh`, `docs/regmap.md` must match `regmap.py`), each documented instruction rule on the ISS, that
every program in `sw/programs` assembles, and the MicroPython board driver against the ISS.
`sw/tests/test_loader_safety.py` checks on every clock cycle that loading a program never drives an open-drain pin
high (the loaders set `PAD_OD` before any pin), that reading instruction memory through an engine moves no
pin, and that loading another program onto an engine hands its pins over: I2C, then SPI controller, then SPI
target on one engine leaves no stale driver and no stale open-drain bit, and freeing one engine keeps the other's
pins. The board-driver tests also check that an image from an older loader is refused before anything is written.
`sw/tests/test_proto_helpers.py` covers the host-side protocol helpers: the decoders reject short and over-long
packets instead of raising, CAN remote frames carry no data field, the JTAG-to-SWD sequence has no gap before
the select word, and an ACK-aware SWD write puts no data on the wire after WAIT or FAULT.

`sw/tests/test_clock_and_host_timing.py` covers what the 60 MHz cocotb runs with ideal pads cannot: it runs the
ISS with 20 ns cycles (the rated 50 MHz) and checks the WS2812 pulse widths at 40, 50 and 60 MHz, 1-Wire reads
against a device that releases a 0 at 16 µs (and that the old 60 MHz divider would misread it), the CAN bit
rates, USB packets in both directions (`usb_tx` exact, edges within one clock of the divider's grid; `usb_rx`
with the sender 0.4 % slow or fast, at four phases, and rejecting a trailing stuff bit sent as 1 or left out
at nine phases; `usb_tx` packets back to back through the SPI host port at SCK = clk/16 with `push_packet`),
the SPI target's limits against a mode-0 controller model with delayed MISO (2.5 MHz with 60 ns, 3 MHz with
40 ns, CS set-up 160 ns, at ten phases), and the host port's read margin with `SpiDriver(miso_delay=...)`: at
SCK clk/8 a single cycle of MISO delay breaks reads, at clk/16 four cycles still work.

## 2. Lock-step: RTL against the ISS

**Every cocotb test runs in lock-step.** `test/lib/bwtb.py` starts a checker coroutine that, after every rising
clock edge, feeds the inputs the RTL actually sampled (registered copies in `test/tb.v`, so stimulus written at the
same instant as a clock edge cannot race) to the ISS and requires `uo_out`, `uio_out` and `uio_oe` to be identical.
A test fails on the first differing cycle, and on any X or Z on an input or output (no cycle is skipped). The
checker starts one clock after time zero, so it also compares the reset cycles and the release of the reset
synchronizer. Because MISO is one of the compared outputs, every register read also checks internal state.

**Constrained-random lock-step** (`verif/lockstep`) runs the Verilated RTL and the ISS on the same random stimulus
and compares all outputs on every cycle. The stimulus for a seed contains:

* random instruction memory (30 % fully random 16-bit words, the rest random opcodes with gentler operands), random
  engine configurations (dividers, wrap ranges, pin bases, shift and FIFO modes, joins and chains, BAU
  parameters), random pad configuration;
* random host traffic over the real SPI pins at random SCK rates: FIFO pushes and pops, register reads and writes,
  instruction injection, restarts, instruction-memory rewrites while engines run, IRQ forcing and clearing;
* random activity on all 13 protocol input pins at a random rate, `ena` toggling, and occasional resets.

The stimulus is a pure function of the seed, and every run is logged (`lockstep_log.jsonl`), so a failure is
reproduced with `make run FIRST=<seed> SEEDS=1`.

| Campaign | Seeds | Cycles | Result |
|---|---|---|---|
| Local, 2026-11-20 (two engines, timing fixes, 144966f) | 1000 × 100 000 | 100 000 000 | all identical |
| Local, 2026-11-19 (two engines) | 1000 × 100 000 | 100 000 000 | all identical |
| Local, 2026-11-17 (four engines) | 1000 × 100 000 | 100 000 000 | all identical |
| CI (`lockstep` workflow, every push) | 50 × 200 000 | 10 000 000 | all identical |

## 3. Protocols against independent models

The ISS cannot be the only oracle for protocol correctness: a misunderstanding of the protocol would be shared by
the program, the ISS and the RTL. Every protocol program is therefore checked against a model of the *other end
of the wire* that knows nothing about Bitweaver:

| Protocol | Program | Reference model | What is checked |
|---|---|---|---|
| UART 8N1/8E1 | `uart.bw` | cocotbext-uart (third party) | 9600–921600 baud TX and RX, parity, framing error, full duplex |
| SPI | `spi.bw` | cocotbext-spi SpiMaster; `StreamTarget` on cocotbext-spi's SpiSlaveBase | controller modes 0–3 at 1 and 7.5 MHz, multi-byte frames; target mode 0 at 1 and 2.5 MHz, MISO released while deselected and after a byte cut short by CS |
| I2C | `i2c.bw` | `test/lib/i2c_bfm.py` (from NXP UM10204) | 100 kHz / 400 kHz, every standard- and fast-mode timing limit, ACK/NACK, repeated START, clock stretching both ways (by the target before and after the ACK, and by Bitweaver's target before a byte starting with 0 or 1), stretch timeout |
| WS2812 | `ws2812.bw` | pulse-width decoder from the WS2812B datasheet | T0H/T1H/bit period limits; a frame ends only after 280 µs low (WS2812B-V5), and any other gap must be a bit |
| JTAG | `jtag.bw` | IEEE 1149.1 TAP model (16-state controller, IR, IDCODE, BYPASS, user register) | IDCODE read, IR capture, register write/read-back, BYPASS delay |
| SWD | `swd.bw` | Arm ADIv5 SW-DP model, starting in JTAG mode | the exact JTAG-to-SWD switch (the old sequence is shown to fail), line reset, DPIDR, power-up handshake, posted AP reads, parity, malformed request, WAIT with no data phase |
| PS/2 | `ps2.bw` | keyboard model | frames, odd parity, glitches on CLK (filter), aborted frames with CLK left high or held low for 360 µs (WAIT timeouts) |
| CAN 2.0A | `can.bw` | CAN node with its own encoder, stuffing and CRC15, wired-AND bus | TX and RX at 500 kbit/s and 1 Mbit/s, worst-case stuffing, a stuff bit after the last CRC bit (both levels), recessive CRC delimiter, ACK and missing ACK, remote frames requesting 0, 4 and 8 bytes |
| USB low speed | `usb.bw` | USB 2.0 line model with spec-literal CRC5/CRC16 | DATA0/1 (incl. all-0xFF stuffing, zero length), ACK, IN, SETUP; NRZI, stuffing, EOP, bus release; transmit at 50 MHz with the host port at SCK = clk/16 |
| 1-Wire | `onewire.bw` | Maxim 1-Wire device (ROM, scratchpad) | reset/presence, ROM with CRC8, write/read, absent device timeout |
| Concurrency | SPI controller + I2C controller (400 kHz) | the models above, simultaneously | two protocols on both engines at once, one shared host port |

## 4. Formal verification

All jobs run in the `formal` workflow. "Proved" means an unbounded proof (PDR or k-induction).

| Job | Scope | Properties | Result |
|---|---|---|---|
| `fifo.sby` | `bw_fifo` | counts never exceed the configured depth and agree with the pointers; two arbitrary words pushed back to back come out in order, in normal, joined-TX and joined-RX modes | proved (abc pdr); 5 covers reached |
| `host_spi.sby` | `bw_host_spi` | legal phase and bit counter; write and pop strobes are single-cycle and only follow a real SCK edge; MISO low while deselected; **no deadlock**: one cycle of CS_N high always returns to idle | proved (abc pdr); 3 covers reached |
| `sm_props.sby` | one `bw_sm` with an unconstrained instruction stream, pins, flags and host writes | shift counters ≤ 16; injected-instruction flags consistent; wait-progress flags only with WAIT/IRQ in IR; restart gives PC = ORG and clean state; IR equals memory[PC] when not injected; **PC stays inside the wrap range** without a jump; a disabled engine does not move | proved (abc pdr, 7 min); BMC to depth 24; 12 covers reached, one per opcode completing plus stall, exec and stuff cases |
| `opendrain.sby` | whole chip | an open-drain pad never drives high; no output register bits exist for input-only pins | proved (k-induction). The first property holds by construction of the pad logic, and only for pins already in open-drain mode: software must set `PAD_OD` before it sets such a pin, which `test_loader_safety.py` checks |
| `quiet.sby` | whole chip, through its pins only (`formal/quiet_top.v`) | from reset until the host first pulls CS_N low, no pad is driven and every output is low, for any other inputs | proved (abc pdr); 2 covers reached (quiet past the reset synchronizer, and the end of the quiet period) |
| `clkdiv.sby` | `bw_clkdiv` | the registered zero flag that drives the engine tick always equals `cnt == 0`, so the registered tick is the combinational zero detect it replaced | proved (abc pdr) |

One proof was run once rather than kept as a job: when the instruction-memory ports were changed to read each
word's next value (a timing fix, see [architecture.md](architecture.md#timing)), Yosys `equiv_make` /
`equiv_induct` proved the new memory equivalent to the previous one (1068 of 1068 equivalence cells).

The engine proof assumes two facts about its environment, stated in `src/bw_sm_formal.vh`: the host performs at
most one register write per cycle, and the instruction memory is not rewritten under the address being executed.
The second is a real hardware corner: rewriting the instruction under a stalled WAIT makes the replacement inherit
the wait's progress flags. The ISS models it identically and [isa.md](isa.md) documents it.

## 5. Coverage

**Functional coverage** is collected by the ISS during the random lock-step campaign (the RTL is identical on every
cycle, so the bins hold for both). The bins are every reachable (instruction, option, outcome) combination, 465 in
all: for example `JMP x-- not taken`, `WAIT irq 1 timed timeout`, `OUT pindirs bau stuff`, `PULL ifempty noblock
empty`, all 256 `MOV` destination × operation × source combinations. `verif/lockstep/cov_report.py` lists the
holes; with `--require-all` it fails on any. The CI run (50 seeds × 200 000 cycles) reaches about 454 of the 465
bins and only reports the rest; the 1000-seed campaign below reaches all of them.

**Code coverage** is Verilator line and toggle coverage of the same runs.

| Metric | Result |
|---|---|
| Functional bins (465 reachable) | 465 / 465 (100 %) |
| Verilator line coverage | 100 % (285 / 285 lines) |
| Verilator toggle coverage | 98.3 % (8444 / 8590 points); all 146 missing points are bits that cannot change |

The 146 untoggled points were each checked: the status bits that the register layout reserves for engines 2 and 3,
which the two-engine chip does not have (`INT_STAT`, `FSTAT`, `FDEBUG`, the SPI status byte: 76), `PIN_OUT`/`PIN_OE`
bits of the input-only pins (20), `PAD_OD` bits of pins without open drain (22), `PAD_FILT` bits of output-only
pins (12), FIFO depth bits that are 0 for every depth 0/4/8 (8), the always-zero virtual pin 23 (2), the engines'
constant numbers (3), and bits of the shift-count arithmetic that are constant because counts are 1..16 (3).
Synthesis removes all of them. (A first two-engine campaign also left bit 6 of the write address untoggled, a real
stimulus gap: the generator never wrote to empty address windows. It now does.)

With 1000 seeds the random campaign reaches every bin. At 200 seeds it missed one, `OUT X, BAU` while a stuff bit
is due, which now also has a directed test (`test_isa.test_bau_stuff_bit_into_register`).

## 6. Gate-level simulation

The whole cocotb suite also runs on gate-level netlists (`make GATES=yes`). Lock-step stays on: the checker uses
only the chip's pins, so the routed netlist must match the ISS on every pin on every cycle, as the RTL does (until
2026-11-24 it was off for netlists, and only the protocol models and expected values checked them). These
netlists are simulated without timing (no SDF back-annotation), so they check the logic of the netlist, not its
delays; timing is signed off by static timing analysis.

* locally, on the Yosys netlist mapped to the IHP SG13CMOS5L cells (`scripts/synth_area.sh`): every test passes,
  for the four-engine design (53 tests) and for the current two-engine design (58 tests, all in lock-step);
* in CI, on the routed netlist from LibreLane (`gds` workflow, `gl_test` job): 56 / 56 on 144966f, the first
  two-engine design to pass hardening, 56 / 56 with lock-step on 68e38d0 and 58 / 58 with lock-step on 3b3beae
  (2026-11-24): the routed netlist matched the reference model on every pin on every cycle of every test.

## Bugs found, and by what

| Found by | Bug | Fix |
|---|---|---|
| lock-step | ISS applied a `TS_PRESCALE` write to the timestamp in the same cycle; the RTL applies it from the next | ISS |
| independent model (SPI) | SPI target program stalled when its RX FIFO was full, then sampled the last bit late | program uses `push noblock` |
| independent model (SPI) | SPI target sent a stale filler byte at the start of a frame whose replies arrived between frames | program re-fetches at CS fall if the prefetch was filler |
| independent model (I2C) | I2C target sampled one bogus bit right after START (SCL still high) | wait for SCL low after START |
| I2C timing monitor | controller's SCL high time could fall just under 4.0 µs after a target stretched the clock | configurable low/high split: 5/5 ticks at 100 kHz, 6/4 at 400 kHz |
| WS2812 decoder | fractional divider 7.5 made T0H 383 ns, above the 380 ns limit | integer divider 7 |
| USB line model | SE0 detector tested D- and D+ on consecutive cycles and saw K→J transitions as SE0 | `in pins, 2` samples both at once |
| USB line model | USB receiver restarted during the EOP (D- is low in SE0) | wait for J before the next SYNC |
| USB line model | USB transmitter kept driving the bus between packets | release until a packet arrives |
| formal (FIFO) | none in the RTL; the first ordering property was itself wrong | rewritten |
| formal (engine) | none in the RTL; found the documented IMEM-rewrite corner case and two missing environment assumptions | assumptions stated |
| independent review 1 | CAN transmitter left out a stuff bit due after the last CRC bit; I2C controller timed START/STOP set-up from its own SCL release, so a stretching target cut it short; loaders set pins before `PAD_OD`; assembler range checks; USB receive window | programs, loaders, assembler; tests that fail without the fix |
| independent review 3 | `usb_rx` ignored the BAU's stuff error and a missing stuff bit before the EOP; the SPI target needed 9 cycles from CS to MISO and missed 3 MHz; the CAN model and decoder did not handle remote frames; a remapped `uart_tx` floated during a reload; directive ranges | programs, model, loader, assembler; tests that fail without the fix |
| independent review 2 | I2C target released SCL in the same cycle it put a 0 on SDA after a stretch (false repeated START); SPI target drove MISO while deselected; USB transmit ran dry at SCK clk/16; loaders left a replaced program's pins and pad modes; JTAG-to-SWD switch had a gap; SWD write data after WAIT; PS/2 watchdog missed a held-low CLK; WS2812 reset gap too short for the V5; decoder crashes; CAN remote frames; shift thresholds | programs, loaders, helpers; tests that fail without the fix |

Several "failures" were bugs in the test models themselves (an I2C controller model that read back a line in the
same simulator step it drove it, a target model that drove ACK and released SCL at the same instant). These were
found because the design and the model disagreed, and were fixed in the model after checking the specification.

## Reproducing

```bash
source ~/tools/oss-cad-suite/environment      # or any install with iverilog, verilator, yosys, sby, cocotb
make -C sw test                               # pytest
cd test && make && python3 -m cocotb_tools.check_results results.xml
cd verif/lockstep && make run SEEDS=100 CYCLES=100000
cd verif/lockstep && make cov SEEDS=100 && python3 cov_report.py      # --require-all with 1000 seeds
cd formal && for j in fifo host_spi sm_props opendrain clkdiv quiet; do sby -f $j.sby; done
scripts/synth_area.sh                         # area against the IHP cell library
```
