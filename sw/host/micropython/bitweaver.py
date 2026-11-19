# SPDX-License-Identifier: Apache-2.0
"""Bitweaver driver for the Tiny Tapeout demo board (MicroPython on the RP2040 / RP2350).

The host port is bit-banged on ui_in[7:5] (CS_N, SCK, MOSI) and uo_out[7] (MISO) with the Tiny
Tapeout SDK's fast byte I/O. The design samples SCK with its own clock, so any SCK rate up to
clk/8 works; MicroPython is far slower than that, which is fine.

Programs are assembled on a PC into load images:

    python3 -m bitweaver.loader sw/programs/uart.bw uart_tx --sm 0 --clkdiv 65.1 -o uart_tx.json

Copy the JSON to the board (mpremote cp uart_tx.json :), then:

    from ttboard.demoboard import DemoBoard
    import bitweaver
    tt = DemoBoard()
    tt.shuttle.tt_um_nnphanusf_bitweaver.enable()
    tt.clock_project_PWM(50_000_000)
    bw = bitweaver.Bitweaver()
    print(hex(bw.read(0x00)[0]))          # 0xb717
    bw.load_image("uart_tx.json")
    bw.enable(0b0001)
    bw.push(0, b"Hello\\r\\n")              # out on uo_out[4], the demo board's UART RX
"""

import json

import ttboard.util.platform as platform

CS_N = 0x80
SCK = 0x40
MOSI = 0x20

ID = 0x00
CTRL = 0x02
IRQ = 0x05
FSTAT = 0x07
SM_BASE = 0x20
FIFO = 0xB
STAT = 0xD


class Bitweaver:
    def __init__(self, pins=0x1F):
        self.pins = pins & 0x1F  # levels this driver keeps on PIN0..PIN4 (ui_in[4:0])
        self.en = 0
        self.first_bytes = (0, 0)
        self._out(CS_N)

    def set_input_pins(self, value):
        """Drive PIN0..PIN4 (ui_in[4:0]), for protocols that read them."""
        self.pins = value & 0x1F
        self._out(CS_N)

    def _out(self, bits):
        platform.write_ui_in_byte(self.pins | bits)

    def frame(self, data):
        """One SPI frame: CS_N low, exchange the bytes MSB first, CS_N high. Returns the bytes read."""
        out = bytearray()
        self._out(0)
        for byte in data:
            v = 0
            for i in range(7, -1, -1):
                m = MOSI if (byte >> i) & 1 else 0
                self._out(m)
                v = (v << 1) | (platform.read_uo_out_byte() >> 7)
                self._out(m | SCK)
            out.append(v)
        self._out(0)
        self._out(CS_N)
        self.first_bytes = (out[0], out[1]) if len(out) > 1 else (out[0], 0)
        return bytes(out)

    def write(self, addr, words, inc=False):
        data = bytearray([0x80 | (0x40 if inc else 0), addr & 0xFF])
        for w in words:
            data += bytes([(w >> 8) & 0xFF, w & 0xFF])
        self.frame(data)

    def read(self, addr, count=1, inc=False):
        raw = self.frame(bytes([0x40 if inc else 0, addr & 0xFF]) + bytes(2 * count))
        return [(raw[2 + 2 * i] << 8) | raw[3 + 2 * i] for i in range(count)]

    # ---------------------------------------------------------------- engines
    def load_image(self, path_or_dict):
        """Replay a load image built by `python3 -m bitweaver.loader`."""
        image = path_or_dict
        if isinstance(path_or_dict, str):
            with open(path_or_dict) as fh:
                image = json.load(fh)
        for addr, words, inc in image["frames"]:
            self.write(addr, words, inc)
        self.en &= ~(1 << image["sm"])
        return image

    def enable(self, mask):
        self.en |= mask & 0xF
        self.write(CTRL, [self.en])

    def disable(self, mask):
        self.en &= ~mask & 0xF
        self.write(CTRL, [self.en])

    def restart(self, mask):
        self.write(CTRL, [self.en | ((mask & 0xF) << 4)])

    def push(self, sm, words):
        """Push words (or a bytes object, one byte per word) to an engine's TX FIFO, waiting for space."""
        words = list(words)
        while words:
            # The status byte shifted out during the address byte says which TX FIFOs are not full.
            self.read(ID)
            if self.first_bytes[1] & (1 << sm):
                self.write(SM_BASE + 0x10 * sm + FIFO, [words.pop(0)])

    def rx_level(self, sm):
        return (self.read(SM_BASE + 0x10 * sm + STAT)[0] >> 12) & 0xF

    def pop(self, sm, count=None):
        """Pop `count` words (default: all that are there) from an engine's RX FIFO."""
        if count is None:
            count = self.rx_level(sm)
        return self.read(SM_BASE + 0x10 * sm + FIFO, count) if count else []

    def exec(self, sm, word):
        self.write(SM_BASE + 0x10 * sm + 0xC, [word])

    def read_imem(self, addr, sm=0):
        """Read an instruction-memory word through a disabled engine (no host read port exists)."""
        self.write(SM_BASE + 0x10 * sm + 0x3, [addr & 0x20])  # ORG (JMP_PIN cleared)
        self.exec(sm, addr & 31)                               # JMP addr (always)
        return self.read(SM_BASE + 0x10 * sm + 0xC)[0]         # EXEC reads the instruction register

    def irq(self):
        return self.read(IRQ)[0]

    def clear_irq(self, mask):
        self.write(IRQ, [mask])
