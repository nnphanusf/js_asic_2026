# Plan: Fable builds a Jane Street Protocol-Emulator ASIC submission

## Context

Jane Street's competition (blog post, Sep 10 2026) asks for an open-source, **reprogrammable protocol-emulator chip**: a small, cycle-exact engine that bit-bangs UART/SPI/I2C (stretch: low-speed USB, 10 Mbit Ethernet; also JTAG, SWD, PS/2, CAN) in firmware. Hard-wired UART/SPI/I2C blocks don't count. The target is **Tiny Tapeout on IHP 130 nm CMOS5L, 8x4 tiles** (about 1.0 mm², about 1K cells per tile, about 32K cells in all), 24 digital pins (8 in, 8 out, 8 bidirectional) plus clk and rst_n, and 3.3 V I/O. 60 MHz is confirmed OK and pad round-trip is about 10 ns. **Deadline: Jan 18 2027.** Today is Nov 14 2026, so there are about 9 weeks. Judges weight **flexibility** most (can it run protocols it wasn't designed for?), then which protocols are shown, unique capabilities, design and verification rigor, and above all a **clear write-up**.

**Required submission (a public repo):** RTL, tests, a passing TT GDS and precheck run, architecture and ISA docs, an assembler or toolchain with example programs per protocol, and a verification write-up. A description of how AI was used is required because AI was used.

**Decisions made:** Verilog + cocotb (the TT template), a multi-core PIO-style architecture, and Fable (`claude-fable-5-2`) running mostly autonomously. The human only handles accounts, sign-up, and final submission.

---

## Architecture to implement (working name `tt_um_nnphanusf_bitweaver`, rename freely)

**Top:** `tt_um_nnphanusf_bitweaver` with the standard TT ports (`ui_in, uo_out, uio_in, uio_out, uio_oe, ena, clk, rst_n`). Everything is synchronous to `clk`, and synchronous reset comes from `rst_n` because flops don't power up at 0.

**Pin plan (fixed early, documented in `docs/info.md`):**
- Host port (program load and runtime data): `ui_in[7]`=CS_n, `ui_in[6]`=SCK, `ui_in[5]`=MOSI, `uo_out[7]`=MISO, `uo_out[6]`=IRQ.
- Protocol pins free for the engines: `ui_in[4:0]`, `uo_out[5:0]`, `uio[7:0]`. That is 19 pins. Engines address one 19-bit virtual pin space, and a per-pin mux maps them to pads.
- The `uio` pins follow TT Pmod conventions (SPI/UART/I2C on uio[0..3] or [4..7]), so standard Pmods work.

**Engine cluster (4 state machines, SM0 to SM3):**
- **Shared instruction memory:** 64 x 16-bit in flops, readable by 4 ports at once, writable only from the host port. Each SM has a wrap start and wrap end, so programs share the memory. An IHP SRAM macro is a Phase-0 trade study and stays out of the baseline.
- **Per SM:**
  - PC (6 bit), X/Y scratch (16 bit), OSR/ISR (16 bit) with shift direction, auto-pull/auto-push thresholds, and a bit counter.
  - TX and RX FIFOs, 4 x 16 bit, that can be joined into 8-deep.
  - A 16.8 fractional clock divider and a 5-bit delay field per instruction.
  - Side-set (0 to 3 pins, optional enable bit).
  - Pin bases: IN, OUT, SET, SIDESET, JMP.
- **ISA, 16-bit, PIO-class core:** `JMP` (cond: always, !X, X--, !Y, Y--, X!=Y, PIN, !OSRE), `WAIT` (pin/gpio/irq, polarity), `IN`, `OUT`, `PUSH`, `PULL` (block/noblock, iffull/ifempty), `MOV` (with invert/bit-reverse), `IRQ` (set/clear/wait, 8 shared flags), `SET`.
- **Original extensions.** These are the "unique capabilities" differentiators. Each gets its own ISA doc section and tests:
  1. **Open-drain / tri-state pin mode** per pin: drive-0 or Hi-Z through `uio_oe`, never drive-1. This gives native I2C, 1-Wire, and wired-AND buses with clock stretching.
  2. **WAIT with timeout:** the wait aborts after N divided cycles and jumps to an alternate target. This covers I2C stretch timeouts, UART break, and bus-idle detection.
  3. **Bit-level assist unit (BAU)** per SM, a configurable shift-path filter:
     - NRZI encode/decode
     - bit-stuff insert/remove after N equal bits (USB=6, CAN=5)
     - Manchester encode
     - CRC LFSR with programmable polynomial and width up to 16 (CRC5/CRC16 for USB, CRC15 for CAN)

     These are generic, programmable primitives. That generality is what makes USB LS, CAN, and 10BASE-T reachable in firmware without per-protocol hardware.
  4. **Edge/timestamp capture:** a free-running 16-bit cycle counter that `IN` can sample. Use it for autobaud, PS/2, and logic-analyzer mode.
  5. **2-FF input synchronizers plus an optional glitch filter** on every input.
  6. **SM-to-SM FIFO chaining:** one SM's RX feeds another's TX, for full-duplex and controller/target pairs.
- **Host register map**, reached through the SPI target with framing `[cmd][addr][data…]`: IMEM write and readback, per-SM config and enable/restart, FIFO push/pop and status, IRQ mask, pin-mux and pad-mode registers, chip ID, and version. Program reload after deselect is a single SPI burst.
- **Clock target:** constrain at 60 MHz, with 50 MHz as the documented fallback if timing doesn't close.

**Area guardrail:** one SM plus its BAU is at most about 4K cells. The 4-SM cluster plus IMEM plus host interface is at most about 24K cells, which leaves at least 25% headroom for CTS and routing. If the Phase-0 synthesis spike exceeds this, drop to 3 SMs or a 32-entry IMEM before cutting extensions.

---

## Repo layout (from `TinyTapeout/ttihp-verilog-template`, main branch, CMOS5L)

```
info.yaml                 # tiles: "8x4", clock_hz: 60000000, top_module, source_files, pinout
src/  project.v  bw_host_spi.v  bw_regs.v  bw_imem.v  bw_sm.v  bw_bau.v  bw_fifo.v
      bw_clkdiv.v  bw_pinmux.v  bw_sync.v  bw_defs.vh
test/ Makefile  tb.v  test_smoke.py  test_isa_*.py  test_lockstep_random.py
      test_proto_uart.py  test_proto_spi.py  test_proto_i2c.py  test_proto_<stretch>.py
      lib/host.py (SPI host BFM)  lib/scoreboard.py
sw/   bitweaver/ (pip package)
        asm.py       # assembler: labels, .side_set, .wrap, .define, .bau, emits JSON+hex
        iss.py       # cycle-accurate Python ISS (golden model of whole chip incl. pins)
        regmap.py    # single source of truth -> also generates bw_defs.vh + docs table
        loader.py    # builds SPI load bursts
      host/micropython/bitweaver.py   # RP2350 demo-board driver (bit-bang/PIO SPI to ui_in)
      programs/ uart_tx.bw uart_rx.bw spi_ctrl_mode{0..3}.bw spi_target.bw i2c_ctrl.bw
                i2c_target.bw  + stretch: jtag.bw swd.bw ws2812.bw ps2.bw can_tx.bw usb_ls.bw
formal/ fifo.sby  host_spi.sby  sm_props.sby  opendrain.sby
docs/ info.md (TT datasheet)  architecture.md  isa.md  programming_guide.md
      verification.md  results.md  ai_usage.md
CLAUDE.md                # conventions for Fable sessions (below)
```

`sw/bitweaver/regmap.py` is the single source of truth. A script generates `src/bw_defs.vh` and the doc tables from it, so the RTL, ISS, assembler, and docs can't drift apart.

---

## Phased schedule (Fable executes; each phase ends at a CI-green gate)

**Phase 0: Setup and spec (Nov 14–20)**
- Clone the template and fill in `info.yaml` (8x4).
- Install the toolchain: devcontainer or local, with iverilog, Verilator, cocotb, yosys, SymbiYosys, LibreLane via the TT tools.
- Write `isa.md`, the register map, and the pinout as the spec.
- **Area spike:** synthesize a skeleton SM and FIFO with yosys and the IHP lib to check the cell-count budget. Record it in `docs/results.md`.
- Push a trivial design and get the **GDS action and precheck green on day 1-3**, so the flow is proven early.

**Phase 1: Executable spec and first silicon path (Nov 21–Dec 4)**
- Write the ISS and assembler first, with Python unit tests. Run the UART TX and SPI programs on the ISS.
- RTL: host SPI, regs, IMEM, one SM (no BAU yet), clkdiv, pinmux, sync.
- Write the cocotb smoke and per-instruction tests.
- **Lockstep harness:** load the same program into the RTL and the ISS, then compare `uo_out/uio_out/uio_oe` every cycle.
- Gate: UART TX passes on the RTL against `cocotbext-uart`, and a full GDS run passes at 8x4.

**Phase 2: Full cluster and the three core protocols (Dec 5–18)**
- Add 4 SMs, FIFO joining and chaining, IRQ flags, open-drain mode, WAIT-timeout, timestamp, and BAU.
- Programs: UART TX/RX (9600 to 921600 baud, 8N1/8E1), SPI controller modes 0 to 3 and SPI target, I2C controller (100/400 kHz, ACK/NACK, repeated start, clock stretching) and I2C target.
- Check every protocol against **independent BFMs** (`cocotbext-uart`, `cocotbext-spi`, an I2C BFM written separately from the firmware), not just against the ISS.
- Demo concurrency: UART + SPI + I2C running on 3 SMs at once.

**Phase 3: Verification depth and timing closure (Dec 19–25)**
- **Constrained-random lockstep:** random legal programs and configs, millions of cycles in Verilator, compared with the ISS. Seed-logged and reproducible.
- **Formal (SBY):**
  - FIFO no overflow/underflow and order preservation
  - the host SPI protocol never deadlocks
  - open-drain pins never drive high
  - PC stays within wrap bounds
  - cover properties for each opcode
- **Coverage:** opcode x option functional coverage (cocotb-coverage) and Verilator line/toggle coverage. Close the gaps.
- **Gate-level sim:** run the full cocotb suite on the post-PnR netlist (`GATES=yes`).
- Timing: read the LibreLane STA reports, fix critical paths (likely the IMEM read mux to decode, or the BAU CRC), and confirm 60 MHz or fall back to 50.

**Phase 4: Flexibility showcase / stretch protocols (Dec 26–Jan 7)**
- These are the strongest evidence for the judging criterion: protocols the core wasn't designed around, using only firmware and generic primitives.
- Priority order (do as many as fit):
  1. WS2812
  2. JTAG
  3. SWD
  4. PS/2
  5. CAN TX/RX with bit-stuffing and CRC15 via the BAU
  6. low-speed USB, 1.5 Mbps NRZI, bit-stuffing, CRC5/16, about 40 clk/bit at 60 MHz
  7. 10BASE-T TX/NLP, Manchester via the BAU, attempt only if the timing math works
- Give each one a program, a cocotb test against a reference decoder, and a short doc section.

**Phase 5: Freeze and write-up (Jan 8–15)**
- **RTL freeze Jan 8.** Do a final hardening run, archive the GDS/precheck/STA/area reports, and generate the 3D/2D layout image.
- Write-up, which judges weigh heavily:
  - `architecture.md` with diagrams
  - `isa.md` with encodings
  - `programming_guide.md` (a walkthrough that writes a new protocol from scratch: the flexibility argument)
  - `verification.md`: the strategy, tools, coverage numbers, formal results, GL-sim results, bugs found and how
  - `results.md`: area, fmax, protocol table with max speeds
  - `ai_usage.md`: how Fable was used, which human checkpoints existed, and what was checked by tools instead of trusted
- Optional: a short demo GIF of waveforms.

**Buffer and submit (Jan 16–18).**

---

## How Fable should run autonomously

- Run in Claude Code with model `claude-fable-5-2`, in the competition repo. Treat this plan as the brief and keep a `PROGRESS.md` checklist that Fable updates every session.
- `CLAUDE.md` rules:
  - Every RTL change ships with a test.
  - `make -C test` and `pytest sw` must pass before a commit.
  - Never edit generated files (`bw_defs.vh`). Regenerate them from `regmap.py`.
  - Keep the ISS cycle-accurate, because it is the reference model.
  - Synthesize and check area after any structural change.
  - Record every timing/area number in `docs/results.md`.
- CI is the referee: the template workflows (`test`, `gds`, `docs`, `fpga`) plus added `formal` and `lockstep` workflows. Fable reads CI failures and fixes them, and never marks a phase done while anything is red.
- If GDS exceeds the 6-hour GitHub Actions limit, switch to local hardening plus the TT custom-GDS action, which must still pass precheck.

## Human-only tasks

1. Fill in the sign-up form (non-binding).
2. Create the GitHub repo (private until submission is fine) and enable Actions.
3. Choose a team handle and project name for the `tt_um_` prefix.
4. Optionally skim the gate reviews at the end of each phase.
5. Make the repo public and submit through the final form Jane Street posts near the deadline.
6. Questions go to asic-competition@janestreet.com or the TT Discord #competitions channel.

## Key risks and mitigations

- **Area overrun:** the Phase-0 spike plus a per-phase synthesis check, with ordered cuts (SM count, then IMEM depth, then FIFO depth).
- **Timing at 60 MHz:** pipeline instruction fetch by 1 stage if needed (the ISS models it), with a 50 MHz fallback.
- **Flow drift:** run GDS weekly from week 1, not only at the end.
- **Model/RTL disagreement:** lockstep random testing plus independent protocol BFMs. Never use the ISS as the only oracle for protocol correctness.
- **Demo-board host path:** confirm in Phase 0 how the RP2350 drives `ui_in` (TT MicroPython SDK) so `sw/host` matches the real board.

## Verification (definition of done)

- The `gds` workflow is green, including **precheck**, with `info.yaml` at 8x4. STA is clean at the stated clock.
- `make -C test` passes in RTL and **GL** (`GATES=yes`). The lockstep random run passes at least 10M cycles across 100+ seeds.
- All `formal/*.sby` jobs pass (prove or bounded depth, documented).
- Every program in `sw/programs/` assembles, runs on the ISS, and passes its cocotb protocol test against an independent BFM. UART, SPI (all 4 modes), and I2C at 100/400 kHz are the minimum.
- The docs set is complete, and someone new to the project could write a new protocol by following `programming_guide.md`.
