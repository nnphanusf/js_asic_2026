<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

Bitweaver is a programmable protocol emulator. Instead of fixed UART, SPI or I2C blocks, it has four small
cycle-exact engines that wiggle and sample pins under firmware control. A protocol is a short program, so the chip
can be reprogrammed after fabrication to speak protocols it was never designed for.

Planned architecture (status: Phase 0 skeleton; all pins currently idle):

- **Four engines (SM0 to SM3)** share a 64 x 16-bit instruction memory. Each has a program counter, two scratch
  registers, input and output shift registers, 4-deep TX and RX FIFOs, a fractional clock divider, side-set pins
  and a per-instruction delay field.
- **Instruction set:** jump (with counter and pin conditions), wait (on a pin or flag, with an optional timeout),
  shift in, shift out, push, pull, move, set, and shared interrupt flags.
- **Bit-level assist unit** in each engine: NRZI, bit-stuffing, Manchester encoding and a programmable CRC, so
  low-speed USB and CAN can run in firmware.
- **Open-drain pin mode** for I2C and other wired-AND buses, a free-running timestamp counter, input synchronizers
  with glitch filters, and FIFO chaining between engines.
- **Host SPI port** to load programs, configure engines, and move data at runtime.

## How to test

The design is driven over the host SPI port (`HOST_CS_N`, `HOST_SCK`, `HOST_MOSI`, `HOST_MISO`), normally from the
RP2350 on the Tiny Tapeout demo board:

1. Hold `rst_n` low, then release it. All protocol pins start as inputs.
2. Write a program into instruction memory over the host port.
3. Configure an engine (pin mapping, clock divider, wrap range) and enable it.
4. Push data to its TX FIFO and read results from its RX FIFO.

The program is lost whenever the design is deselected, so reload it after each selection. Example programs and a
MicroPython driver will live in `sw/`.

## External hardware

Standard Tiny Tapeout Pmods on the bidirectional header (`uio`), using the usual SPI, UART and I2C pin conventions.
I2C needs external pull-up resistors on SCL and SDA.
