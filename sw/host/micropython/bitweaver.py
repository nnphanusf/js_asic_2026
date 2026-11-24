# SPDX-License-Identifier: Apache-2.0
"""Bitweaver driver for the Tiny Tapeout demo board (MicroPython on the RP2040 / RP2350).

The host port is bit-banged on ui_in[7:5] (CS_N, SCK, MOSI) and uo_out[7] (MISO) with the Tiny
Tapeout SDK's fast byte I/O. The design samples SCK with its own clock; on real hardware keep SCK at
or below clk/16 (about 3 MHz at 50 MHz) so MISO settles despite pad and multiplexer delays.
MicroPython is far slower than that, which is fine.

Programs are assembled on a PC into load images. Dividers must match the clock set below
(--f-clk, default 50 MHz); for UART the divider is f_clk / (8 * baud), 54.25 for 115200 baud:

    python3 -m bitweaver.loader sw/programs/uart.bw uart_tx --sm 0 --clkdiv 54.25 -o uart_tx.json

Copy the JSON to the board (mpremote cp uart_tx.json :), then:

    from ttboard.demoboard import DemoBoard
    from ttboard.mode import RPMode
    import bitweaver
    tt = DemoBoard.get()                  # the firmware creates the board at boot; DemoBoard() raises
    tt.shuttle.tt_um_nnphanusf_bitweaver.enable()
    tt.mode = RPMode.ASIC_RP_CONTROL      # the RP2 drives ui_in, which carries the host port
    tt.clock_project_PWM(50_000_000)
    tt.reset_project(True)
    tt.reset_project(False)
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
PIN_OE_LO = 0x14
PAD_OD_LO = 0x16
PAD_FILT_LO = 0x18
SM_BASE = 0x20
ORG = 0x3
PINCTRL0 = 0x4
PINCTRL1 = 0x5
FIFO = 0xB
EXEC = 0xC
STAT = 0xD
SIDE_MASK = 0xE000  # PINCTRL1.SIDE_COUNT and SIDE_EN
SET_BASE_SHIFT = 10  # PINCTRL0.SET_BASE
SET_COUNT_1 = 0x0400  # PINCTRL1 with SET_COUNT = 1
SET_PINDIRS_0 = 0xE080  # set pindirs, 0
IMAGE_FORMAT = 2
UIO_PINS = 0xFF << 11  # PIN11..PIN18; PIN5..PIN10 have no output enable


class Bitweaver:
    def __init__(self, pins=0x1F):
        self.pins = pins & 0x1F  # levels this driver keeps on PIN0..PIN4 (ui_in[4:0])
        self.en = 0
        self.first_bytes = (0, 0)
        self.images = {}  # sm -> (owned pins, open drain, filter) of the image loaded there
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

    def _pinvec(self, addr, clear, set_):
        """Clear, then set, bits of a 19-bit pin mask in a _LO / _HI register pair (read, modify, write)."""
        lo, hi = self.read(addr, 2, inc=True)
        old = lo | (hi << 16)
        new = (old & ~clear) | set_
        if new != old:
            self.write(addr, [new & 0xFFFF, (new >> 16) & 7], inc=True)

    # ---------------------------------------------------------------- engines
    def load_image(self, path_or_dict):
        """Replay a load image built by `python3 -m bitweaver.loader`.

        Before any frame, the engine is handed over: the pins the previous image on this engine drove
        are released (unless another engine's image uses them), the open-drain and filter bits only
        that image needed are cleared, and this image's are set. So an open-drain pin is never driven
        high, and switching an engine from one protocol to another leaves no stale driver or pad mode.
        Engines that are running stay running: their CTRL.EN bits are kept in every CTRL write.
        Images from older loaders are rejected before anything is written; rebuild them.
        """
        image = path_or_dict
        if isinstance(path_or_dict, str):
            with open(path_or_dict) as fh:
                image = json.load(fh)
        if image.get("format") != IMAGE_FORMAT:
            raise ValueError("load image format %r, expected %d: rebuild it with python3 -m bitweaver.loader"
                             % (image.get("format"), IMAGE_FORMAT))
        sm = image["sm"]
        self._handoff(sm, (image["owned"], image["dirs"], image["od"], image["filter"]))
        for addr, words, inc in image["frames"]:
            if addr == CTRL:
                words = [w | self.en for w in words]
            self.write(addr, words, inc)
        return image

    def release(self, sm):
        """Free engine `sm`: disable it, release its image's pins and clear the pad modes only it used."""
        self._handoff(sm, None)

    def _handoff(self, sm, new):
        """bitweaver.loader.PinOwners.handoff: release the old image's pins (PIN11..PIN18 only, and not
        those the new image sets up itself unless their open-drain bit goes), then the pad modes."""
        owned = od = filt = 0
        for k, (o, _, d, f) in self.images.items():
            if k != sm:
                owned, od, filt = owned | o, od | d, filt | f
        old_owned, _, old_od, old_filt = self.images.get(sm, (0, 0, 0, 0))
        n_owned, n_dirs, n_od, n_filt = new or (0, 0, 0, 0)
        self.en = self.read(CTRL)[0] & ~(1 << sm) & 0xF
        self.write(CTRL, [self.en])
        base = SM_BASE + 0x10 * sm
        release = old_owned & ~owned & UIO_PINS & (~n_dirs | (old_od & ~od & ~n_od))
        for pin in range(19):
            if (release >> pin) & 1:  # through the engine, so no other pin is touched
                self.write(base + PINCTRL0, [pin << SET_BASE_SHIFT, SET_COUNT_1], inc=True)
                self.exec(sm, SET_PINDIRS_0)
        self._pinvec(PAD_OD_LO, old_od & ~od, n_od)
        self._pinvec(PAD_FILT_LO, old_filt & ~filt, n_filt)
        if new is None:
            self.images.pop(sm, None)
        else:
            self.images[sm] = new

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

    def push_packet(self, sm, words, depth=8, idle_pins=0):
        """Queue one packet so the engine never runs out of words in the middle of it (usb_tx).

        Waits until the previous packet is out (TX FIFO empty and every pin in `idle_pins` released,
        0x1800 for USB on PIN11/PIN12), stops the engine, preloads `depth` words in one frame, starts
        the engine and pushes the rest. A packet of at most `depth` words is always safe: for USB the
        handshakes (5 words), tokens (8) and empty DATA packets (7). The rest of a longer packet has to
        arrive within the preloaded words' time on the wire (about 43 us for an 8-byte DATA packet),
        which this bit-banged port cannot do; send those from a faster SPI host.
        """
        words = list(words)
        base = SM_BASE + 0x10 * sm
        while (self.read(base + STAT)[0] >> 8) & 0xF or (idle_pins and self._pins(PIN_OE_LO) & idle_pins):
            pass
        self.disable(1 << sm)
        self.write(base + FIFO, words[:depth])
        self.enable(1 << sm)
        self.push(sm, words[depth:])

    def _pins(self, addr):
        lo, hi = self.read(addr, 2, inc=True)
        return lo | (hi << 16)

    def rx_level(self, sm):
        return (self.read(SM_BASE + 0x10 * sm + STAT)[0] >> 12) & 0xF

    def pop(self, sm, count=None):
        """Pop `count` words (default: all that are there) from an engine's RX FIFO."""
        if count is None:
            count = self.rx_level(sm)
        return self.read(SM_BASE + 0x10 * sm + FIFO, count) if count else []

    def exec(self, sm, word):
        self.write(SM_BASE + 0x10 * sm + EXEC, [word])

    def read_imem(self, addr, sm=0):
        """Read an instruction-memory word through a disabled engine (no host read port exists).

        No pin moves (side-set is off around the injected JMP) and ORG and PINCTRL1 are restored;
        the engine's PC is left at `addr`, so restart it before enabling it again.
        """
        base = SM_BASE + 0x10 * sm
        org, pinctrl1 = self.read(base + ORG)[0], self.read(base + PINCTRL1)[0]
        self.write(base + PINCTRL1, [pinctrl1 & ~SIDE_MASK])
        self.write(base + ORG, [addr & 0x20])               # ORG (JMP_PIN cleared)
        self.exec(sm, addr & 31)                            # JMP addr (always)
        word = self.read(base + EXEC)[0]                    # EXEC reads the instruction register
        self.write(base + ORG, [org])
        self.write(base + PINCTRL1, [pinctrl1])
        return word

    def irq(self):
        return self.read(IRQ)[0]

    def clear_irq(self, mask):
        self.write(IRQ, [mask])
