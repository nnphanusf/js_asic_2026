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

The design is driven over the host SPI port (mode 0, MSB first, SCK at most clk/8), normally from the RP2040 or
RP2350 on the Tiny Tapeout demo board:

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

1. Hold `rst_n` low, then release it. All protocol pins start as inputs.
2. Write a program into instruction memory (addresses `0x80`..`0xBF`).
3. Configure an engine (registers at `0x20 + 0x10 * n`: clock divider, wrap range, pin bases) and enable it in
   `CTRL` (`0x02`).
4. Push data to its TX FIFO and read results from its RX FIFO (offset `0xB` in the engine's register block).

For example, `sw/programs/uart.bw` turns SM0 into a UART transmitter on `uo_out[4]` and SM1 into a receiver on
`ui_in[3]`, the pins wired to the demo board's UART. The `sw/bitweaver` Python package assembles programs and
builds the SPI frames; `sw/host/micropython/bitweaver.py` is a driver for the demo board.

## External hardware

Standard Tiny Tapeout Pmods on the bidirectional header (`uio`), using the usual SPI, UART and I2C pin
conventions. I2C needs external pull-up resistors on SCL and SDA.
