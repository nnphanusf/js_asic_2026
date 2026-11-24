<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

Bitweaver is a programmable protocol emulator. It has no fixed UART, SPI or I2C blocks. Instead, two small
engines that keep exact cycle timing drive and sample pins under firmware control. A protocol is a short program, so
the chip can be reprogrammed after fabrication to speak protocols it was never designed for.

- **Two engines (SM0 and SM1)** share a 64 x 16-bit instruction memory. Each has a program counter, scratch
  registers X and Y, input and output shift registers with automatic push and pull, 4-deep TX and RX FIFOs (one
  8-deep FIFO when joined), a 16.8 fractional clock divider, up to three side-set pins and a 5-bit delay field on
  every instruction.
- **Instruction set:** `JMP` (always, X or Y zero, decrement, X!=Y, pin, OSR not empty), `WAIT` (pin or IRQ flag,
  optionally with a timeout), `IN`, `OUT`, `PUSH`, `PULL`, `MOV` (invert, bit-reverse, byte-swap), `IRQ` (set, wait,
  clear; eight shared flags) and `SET`. Instructions can also be injected from the host or from a FIFO.
- **Bit-level assist unit** in every engine: NRZI coding, bit stuffing and destuffing, Manchester encoding and a
  CRC with a programmable polynomial of up to 16 bits, all on the one-bit shift path.
- **Open-drain mode** on the bidirectional pins for I2C and other wired-AND buses, a free-running 16-bit timestamp
  that programs can sample, 2-FF input synchronizers with optional glitch filters, and FIFO chaining between
  engines.
- **Host SPI port** to load programs, configure engines and move data at run time.

The 19 protocol pins form one pin space, PIN0 to PIN18, that every engine can use:

| Pins | Pads | Direction |
|---|---|---|
| PIN0..PIN4 | `ui_in[0]`..`ui_in[4]` | input |
| PIN5..PIN10 | `uo_out[0]`..`uo_out[5]` | output |
| PIN11..PIN18 | `uio[0]`..`uio[7]` | bidirectional, optional open drain |

Programs in the repository (each tested against an independent model of the other end of the wire): UART
(9600–921600 baud), SPI controller (modes 0–3) and target, I2C controller (100/400 kHz) and target, WS2812, JTAG,
SWD, PS/2, CAN 2.0A, low-speed USB packets and 1-Wire.

Full documentation: `docs/architecture.md`, `docs/isa.md`, `docs/regmap.md` and `docs/programming_guide.md` in the
project repository.

## How to test

The design is driven over the host SPI port (mode 0, MSB first, SCK at most clk/16, about 3 MHz at the rated
50 MHz), normally from the RP2040 or RP2350 on the Tiny Tapeout demo board:

| Signal | Pin |
|---|---|
| `HOST_CS_N` | `ui_in[7]` |
| `HOST_SCK` | `ui_in[6]` |
| `HOST_MOSI` | `ui_in[5]` |
| `HOST_MISO` | `uo_out[7]` |
| `HOST_IRQ` | `uo_out[6]` |

Each SPI frame is `[command] [address] [16-bit data words...]`. Command bit 7 selects write (1) or read (0), bit 6
auto-increments the address after every word. While the command and address bytes are shifted in, MISO returns the
ID byte `0xB7` and a FIFO status byte. Register `0x00` reads `0xB717`.

1. Hold `rst_n` low, then release it. Until the host first pulls `HOST_CS_N` low (proved formally,
   `formal/quiet.sby`):
   * PIN0..PIN4 (`ui_in[4:0]`) are inputs, as always.
   * PIN5..PIN10 (`uo_out[5:0]`) are dedicated outputs and drive 0. They have no output enable, so never
     connect them to another driver.
   * PIN11..PIN18 (`uio[7:0]`) are released (`uio_oe` = 0); no pad is open drain or filtered.
   * `HOST_MISO` and `HOST_IRQ` drive 0.
2. Write a program into instruction memory (addresses `0x80`..`0xBF`).
3. For pins that are open drain (I2C, 1-Wire, PS/2), set `PAD_OD` (`0x16`/`0x17`) first. Then give each
   output pin its idle level and only then make it an output: with `PIN_OUT` then `PIN_OE` (`0x12`/`0x13`,
   `0x14`/`0x15`), or through the engine: point `SET_BASE` at the pin and set `SET_COUNT` to 1 (`PINCTRL0`,
   `PINCTRL1`), then write `set pins, <level>` and `set pindirs, 1` to its `EXEC` register (offset `0xC`).
   An engine only drives pins whose direction is output.
4. Configure the engine (registers at `0x20 + 0x10 * n`: clock divider, wrap range, pin bases), restart it with
   `CTRL.RESTART` (`0x02` bits 5:4, one per engine) and enable it with `CTRL.EN` (bits 1:0).
5. Push data to its TX FIFO and read results from its RX FIFO (offset `0xB` in the engine's register block).

Disabling or restarting an engine does not release its pins or clear its pad modes. Before giving the pins to
another program, release them (`set pindirs, 0` through the engine, or `PIN_OE`) and clear the open-drain and
filter bits nothing needs any more, in that order.

`bitweaver.loader` and the demo-board driver do steps 2 to 4 for you. They set `PAD_OD` before any pin, and
loading a program onto an engine first releases the pins of the program it replaces that the new one does not
set up itself, so a reloaded UART or SPI line does not glitch (`release(sm)` frees an engine).

For example, `sw/programs/uart.bw` turns SM0 into a UART transmitter on `uo_out[4]` and SM1 into a receiver on
`ui_in[3]`, the pins wired to the demo board's UART. The `sw/bitweaver` Python package assembles programs and
builds the SPI frames; `sw/host/micropython/bitweaver.py` is a driver for the demo board.

## External hardware

Standard Tiny Tapeout Pmods on the bidirectional header (`uio`), using the usual SPI, UART and I2C pin
conventions. The demo board's I/O is 3.3 V logic, and the chip has no pull-up resistors or bus
transceivers of its own:

* I2C, 1-Wire and PS/2 need external pull-up resistors (to 3.3 V) on their open-drain lines.
* CAN needs a CAN transceiver between the pins and the bus.
* Low-speed USB needs the 1.5 kOhm pull-up on D-.
* 5 V devices (PS/2 keyboards, WS2812 strips powered from 5 V) need level shifting.

Signals that an external device returns in answer to the chip's clock (SPI MISO, JTAG TDO, SWD data) pass
the pads and the Tiny Tapeout multiplexer twice; start those programs at clkdiv 4 or more (about 3 MHz) and
speed up only after checking the board. The host port itself needs SCK at most clk/16.

The example programs compute their dividers for the rated 50 MHz clock. Run the chip at another clock only with
dividers computed for it (`--f-clk` for `python3 -m bitweaver.loader`, `f_clk=` for the helpers in
`bitweaver.proto`).
