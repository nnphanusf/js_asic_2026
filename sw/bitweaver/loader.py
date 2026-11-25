"""Host-side helpers: build the register writes that load and configure a program.

Everything here is a pure function of its arguments, so the same code drives the simulator, the
cocotb testbench and the MicroPython driver. A transport only has to implement

    write(addr, words, inc=False)      one SPI write frame
    read(addr, count=1, inc=False)     one SPI read frame, returns a list of words
"""

from __future__ import annotations

from . import regmap as R
from .asm import Program, enc_jmp, enc_set

_CFG_KEYS = {
    "clkdiv", "clkdiv_int", "clkdiv_frac", "in_base", "out_base", "set_base", "side_base", "jmp_pin",
    "out_count", "set_count", "in_shiftdir", "out_shiftdir", "autopush", "autopull", "push_thresh",
    "pull_thresh", "join_tx", "join_rx", "chain_src", "tmo", "bau_poly", "stuff_n", "stuff_any",
    "od_pins", "filter_pins", "dirs_pins", "high_pins",
}


def clkdiv_fields(div: float) -> tuple[int, int]:
    """Split a divider such as 65.104 into the CLKDIV_INT and CLKDIV_FRAC register values."""
    fixed = round(div * 256)
    if not 256 <= fixed <= 65536 * 256:
        raise ValueError(f"clock divider {div} is out of range (1 .. 65536)")
    return (fixed >> 8) & 0xFFFF, fixed & 0xFF


def frame_bytes(addr: int, words=(), count: int = 0, write: bool = True, inc: bool = False) -> bytes:
    """Bytes the host sends in one SPI frame (CS_N low for the whole frame)."""
    out = bytearray([(R.CMD_WRITE if write else 0) | (R.CMD_INC if inc else 0), addr & 0xFF])
    if write:
        for w in words:
            out += bytes([(w >> 8) & 0xFF, w & 0xFF])
    else:
        out += bytes(2 * count)
    return bytes(out)


def sm_config(prog: Program, origin: int, **overrides) -> list[int]:
    """The eleven configuration words CLKDIV_INT .. BAU_CFG for a program placed at `origin`."""
    c = dict(prog.config)
    for k, v in overrides.items():
        if k not in _CFG_KEYS:
            raise KeyError(f"unknown configuration key '{k}'")
        c[k] = v
    if "clkdiv" in c:
        c["clkdiv_int"], c["clkdiv_frac"] = clkdiv_fields(c["clkdiv"])
    g = c.get
    if len(prog.code) + origin > R.IMEM_DEPTH:
        raise ValueError(f"program '{prog.name}' does not fit at origin {origin}")
    return [
        g("clkdiv_int", 1) & 0xFFFF,
        g("clkdiv_frac", 0),
        R.pack("WRAP", bottom=origin + prog.wrap_target, top=origin + prog.wrap),
        R.pack("ORG", org=origin, jmp_pin=g("jmp_pin", 0)),
        R.pack("PINCTRL0", in_base=g("in_base", 0), out_base=g("out_base", 0), set_base=g("set_base", 0)),
        R.pack("PINCTRL1", side_base=g("side_base", 0), out_count=g("out_count", 0),
               set_count=g("set_count", 0), side_count=prog.side_count, side_en=int(prog.side_opt)),
        R.pack("SHIFTCTRL", push_thresh=g("push_thresh", 16) & 15, pull_thresh=g("pull_thresh", 16) & 15,
               autopush=g("autopush", 0), autopull=g("autopull", 0), in_shiftdir=g("in_shiftdir", 1),
               out_shiftdir=g("out_shiftdir", 1), side_pindir=int(prog.side_pindirs)),
        R.pack("FIFOCTRL", join_tx=g("join_tx", 0), join_rx=g("join_rx", 0),
               chain_en=int("chain_src" in c), chain_src=g("chain_src", 0)),
        g("tmo", 0),
        g("bau_poly", 0),
        R.pack("BAU_CFG", stuff_n=g("stuff_n", 0), stuff_any=g("stuff_any", 0)),
    ]


def pin_mask(pins) -> int:
    m = 0
    for p in pins or ():
        m |= 1 << p
    return m


def load_ops(prog: Program, sm: int, origin: int, en_shadow: int = 0, write_code: bool = True,
             **overrides) -> list[tuple[int, list[int], bool]]:
    """Register writes that load `prog` into engine `sm`, leaving it disabled and ready to enable.

    Returns a list of (address, words, auto_increment) frames. `en_shadow` is the current CTRL.EN
    value; the engine being loaded is disabled first and the others are left alone.
    """
    en = en_shadow & ~(1 << sm) & 0xF
    ops: list[tuple[int, list[int], bool]] = [(R.addr("CTRL"), [en], False)]
    if write_code:
        ops.append((R.IMEM_BASE + origin, list(prog.code), True))
    ops += pin_setup_ops(prog, sm, **{k: v for k, v in overrides.items() if k in ("dirs_pins", "high_pins")})
    ops.append((R.addr("CLKDIV_INT", sm), sm_config(prog, origin, **overrides), True))
    ops.append((R.addr("CTRL"), [en | (0x10 << sm)], False))
    for word in prog.init:
        ops.append((R.addr("EXEC", sm), [word], False))
    if prog.entry:
        ops.append((R.addr("EXEC", sm), [enc_jmp(R.COND_ALWAYS, prog.entry)], False))
    return ops


def pin_setup_ops(prog: Program, sm: int, **overrides) -> list[tuple[int, list[int], bool]]:
    """Set the initial level and direction of the pins named in `.pins dirs=... high=...`.

    Each pin is set through the engine itself (SET_BASE pointed at the pin, then EXEC of `set pins` and
    `set pindirs`), so pins owned by other engines or by the host are never touched.
    """
    c = dict(prog.config)
    c.update(overrides)
    dirs = c.get("dirs_pins") or []
    high = set(c.get("high_pins") or [])
    ops: list[tuple[int, list[int], bool]] = []
    for pin in dirs:
        ops.append((R.addr("PINCTRL0", sm), [R.pack("PINCTRL0", set_base=pin), R.pack("PINCTRL1", set_count=1)], True))
        ops.append((R.addr("EXEC", sm), [enc_set(R.SET_PINS, 1 if pin in high else 0)], False))
        ops.append((R.addr("EXEC", sm), [enc_set(R.SET_PINDIRS, 1)], False))
    return ops


def pad_masks(prog: Program, **overrides) -> tuple[int, int]:
    """(open-drain mask, filter mask) requested by the program's `.pins od=... filter=...`."""
    c = dict(prog.config)
    c.update(overrides)
    return pin_mask(c.get("od_pins")), pin_mask(c.get("filter_pins"))


def image_pins(prog: Program, **overrides) -> tuple[int, int, int, int]:
    """(owned pins, pins it sets up, open-drain mask, filter mask) of a program, as PinOwners records them.

    A program sets a pin up itself when it is in `.pins dirs=`, or in its SET range and one of its
    `.init` instructions is `set pindirs` (uart_tx and ws2812 do that), wherever the SET range is moved.
    """
    c = dict(prog.config)
    c.update(overrides)
    od, filt = pad_masks(prog, **{k: v for k, v in overrides.items() if k in ("od_pins", "filter_pins")})
    dirs = pin_mask(c.get("dirs_pins"))
    if any((w >> 13) == R.OP_SET and ((w >> 5) & 7) == R.SET_PINDIRS for w in prog.init):
        for i in range(c.get("set_count", 0)):
            dirs |= 1 << ((c.get("set_base", 0) + i) % 32)
    return owned_pins(prog, **overrides), dirs & OUTPUT_PINS, od, filt


OUTPUT_PINS = ((1 << 19) - 1) & ~0x1F  # PIN5..PIN18: the pins an engine can drive
UIO_PINS = 0xFF << 11  # PIN11..PIN18: the pins that can be released (PIN5..PIN10 always drive)


def owned_pins(prog: Program, **overrides) -> int:
    """Pins a program may drive: its `.pins dirs=` list and its OUT, SET and side-set ranges."""
    c = dict(prog.config)
    c.update(overrides)
    m = pin_mask(c.get("dirs_pins"))
    for base, count in ((c.get("out_base", 0), c.get("out_count", 0)),
                        (c.get("set_base", 0), c.get("set_count", 0)),
                        (c.get("side_base", 0), prog.side_count)):
        for i in range(count):
            m |= 1 << ((base + i) % 32)
    return m & OUTPUT_PINS


def release_ops(sm: int, pins: int) -> list[tuple[int, list[int], bool]]:
    """Release every pin in the mask `pins` (output enable off, level kept) through disabled engine `sm`.

    Like pin_setup_ops this goes through the engine (SET_BASE, then EXEC of `set pindirs, 0`), so no
    other pin is touched. It overwrites the engine's PINCTRL0/1; loading a program rewrites them.
    """
    ops: list[tuple[int, list[int], bool]] = []
    for pin in range(19):
        if (pins >> pin) & 1:
            ops.append((R.addr("PINCTRL0", sm), [R.pack("PINCTRL0", set_base=pin), R.pack("PINCTRL1", set_count=1)], True))
            ops.append((R.addr("EXEC", sm), [enc_set(R.SET_PINDIRS, 0)], False))
    return ops


class PinOwners:
    """The pins and pad modes of the program loaded on each engine, so that a load can hand them over.

    Disabling or restarting an engine keeps its pins driven and its pad modes set. Loading another
    program onto the engine, or freeing it, has to release the pins its previous program drove and
    clear the open-drain and filter bits that only that program needed; this class works out those
    writes. Pins and pad bits used by another engine's program, or set by the host outside any
    program, are left alone. A pin the new program sets up itself (`.pins dirs=`) goes straight from
    its old level to its new one, without a release in between, unless its open-drain bit is about to
    be cleared. PIN5..PIN10 have no output enable (released, they would drive 0), so they keep their
    level until the new program sets it.
    """

    def __init__(self):
        self.images: dict[int, tuple[int, int, int, int]] = {}  # sm -> (owned, dirs, open drain, filter)

    def others(self, sm: int) -> tuple[int, int, int, int]:
        acc = [0, 0, 0, 0]
        for k, image in self.images.items():
            if k != sm:
                acc = [a | b for a, b in zip(acc, image)]
        return tuple(acc)

    def handoff(self, sm: int, new: tuple[int, int, int, int] | None, en: int, od: int, filt: int):
        """Writes that disable engine `sm`, release its old program's pins and set the pad modes for
        `new` = (owned, dirs, od, filter) of the program about to be loaded (None: free the engine).

        `en`, `od` and `filt` are the current CTRL.EN, PAD_OD and PAD_FILT values. Returns
        (frames, new PAD_OD, new PAD_FILT) and records `new` for engine `sm`.
        """
        o_owned, _, o_od, o_filt = self.others(sm)
        old_owned, _, old_od, old_filt = self.images.get(sm, (0, 0, 0, 0))
        n_owned, n_dirs, n_od, n_filt = new or (0, 0, 0, 0)
        od_clear = old_od & ~o_od & ~n_od
        ops: list[tuple[int, list[int], bool]] = [(R.addr("CTRL"), [en & ~(1 << sm) & 0xF], False)]
        # Release first: an open-drain bit is only cleared once its pin is no longer driven.
        ops += release_ops(sm, old_owned & ~o_owned & UIO_PINS & (~n_dirs | od_clear))
        od2 = (od & ~(old_od & ~o_od)) | n_od
        filt2 = (filt & ~(old_filt & ~o_filt)) | n_filt
        if od2 != od:
            ops += pinvec_ops("PAD_OD", od2)
        if filt2 != filt:
            ops += pinvec_ops("PAD_FILT", filt2)
        if new is None:
            self.images.pop(sm, None)
        else:
            self.images[sm] = new
        return ops, od2, filt2


def pinvec_ops(name: str, value: int) -> list[tuple[int, list[int], bool]]:
    """Write a 19-bit pin vector to a `<name>_LO` / `<name>_HI` register pair."""
    return [(R.addr(name + "_LO"), [value & 0xFFFF, (value >> 16) & 7], True)]


SIDE_MASK = R.fld("PINCTRL1", "SIDE_COUNT").mask | R.fld("PINCTRL1", "SIDE_EN").mask


def read_imem_ops(addr: int, sm: int, org: int, pinctrl1: int) -> list:
    """Frames that read instruction-memory word `addr` through disabled engine `sm`, given its current
    ORG and PINCTRL1. `None` marks where the host reads the EXEC register (the instruction register).

    The injected JMP would drive a side-set pin (a non-optional side-set has no "off" value), so
    side-set is cleared from PINCTRL1 around it; both registers are written back afterwards.
    """
    return [(R.addr("PINCTRL1", sm), [pinctrl1 & ~SIDE_MASK], False),
            (R.addr("ORG", sm), [R.pack("ORG", org=addr & 0x20)], False),
            (R.addr("EXEC", sm), [enc_jmp(R.COND_ALWAYS, addr & 31)], False),
            None,
            (R.addr("ORG", sm), [org], False),
            (R.addr("PINCTRL1", sm), [pinctrl1], False)]


def spi_wave(data: bytes, half: int = 4, lead: int = 4, gap: int = 6) -> tuple[list[tuple[int, int, int]], list[int]]:
    """Cycle-by-cycle (cs_n, sck, mosi) levels for one SPI mode-0 frame.

    Returns the waveform and, for each bit, the cycle index at which the host samples MISO (the
    last cycle before SCK rises). `half` is the SCK half period in clock cycles.
    """
    wave: list[tuple[int, int, int]] = []
    samples: list[int] = []
    wave += [(0, 0, 0)] * lead
    for byte in data:
        for bit in range(7, -1, -1):
            m = (byte >> bit) & 1
            wave += [(0, 0, m)] * half
            samples.append(len(wave) - 1)
            wave += [(0, 1, m)] * half
    wave += [(0, 0, 0)] * lead
    wave += [(1, 0, 0)] * gap
    return wave, samples


class Bench:
    """The simulator plus a simple model of the pads around it, for unit tests and examples.

    `ui_pins` holds the levels driven into PIN0..PIN4 and `uio_ext` the levels driven by the outside
    world onto the bidirectional pads (1 = released / pulled up). A pad the chip drives takes the
    chip's level. Callables in `hooks` run before every clock cycle and may change both.
    """

    def __init__(self, iss=None, trace: bool = False):
        from .iss import Bitweaver
        self.iss = iss or Bitweaver()
        self.ui_pins = 0x1F
        self.uio_ext = 0xFF
        self.hooks: list = []
        self.trace: list[int] | None = [] if trace else None
        self.out = (0, 0, 0)
        self.cycle = 0
        for _ in range(3):
            self.out = self.iss.step(0x80 | self.ui_pins, self.uio_ext, rst_n=0)
        self.run(4)

    def pads(self) -> int:
        """Levels on all 19 pins as an outside observer sees them."""
        uo, uio_out, uio_oe = self.out
        uio = (uio_out & uio_oe) | (self.uio_ext & ~uio_oe & 0xFF)
        return (self.ui_pins & 0x1F) | ((uo & 0x3F) << 5) | (uio << 11)

    def pin(self, n: int) -> int:
        return (self.pads() >> n) & 1

    def step(self, cs_n: int = 1, sck: int = 0, mosi: int = 0) -> None:
        for h in self.hooks:
            h(self)
        ui = (self.ui_pins & 0x1F) | (mosi << 5) | (sck << 6) | (cs_n << 7)
        self.out = self.iss.step(ui, (self.pads() >> 11) & 0xFF)
        self.cycle += 1
        if self.trace is not None:
            self.trace.append(self.pads())

    def run(self, cycles: int) -> None:
        for _ in range(cycles):
            self.step()

    @property
    def miso(self) -> int:
        return (self.out[0] >> 7) & 1

    @property
    def host_irq(self) -> int:
        return (self.out[0] >> 6) & 1


class Driver:
    """High-level host operations on top of a transport's `write` and `read`."""

    def __init__(self):
        self.en = 0
        self.od = 0
        self.filt = 0
        self.origin = 0  # next free instruction-memory address
        self.owners = PinOwners()

    # -- transport, implemented by subclasses
    def write(self, addr: int, words, inc: bool = False) -> None:
        raise NotImplementedError

    def read(self, addr: int, count: int = 1, inc: bool = False) -> list[int]:
        raise NotImplementedError

    # -- operations
    def load(self, prog: Program, sm: int, origin: int | None = None, **overrides) -> int:
        """Load a program into engine `sm` (disabled). Returns the origin it was placed at.

        The pins of the program this one replaces are released first (see PinOwners), then the
        open-drain and filter masks are set, so a pin the program sets high is never driven high,
        not even while it is being loaded.
        """
        if origin is None:
            origin = self.origin
            self.origin += len(prog.code)
        self._handoff(sm, image_pins(prog, **overrides))
        for addr, words, inc in load_ops(prog, sm, origin, self.en, **overrides):
            self.write(addr, words, inc)
        return origin

    def release(self, sm: int) -> None:
        """Free engine `sm`: disable it, release its program's pins and clear the pad modes only it used."""
        self._handoff(sm, None)

    def _handoff(self, sm: int, new) -> None:
        ops, self.od, self.filt = self.owners.handoff(sm, new, self.en, self.od, self.filt)
        for op in ops:
            self.write(*op)
        self.en &= ~(1 << sm)

    def enable(self, mask: int) -> None:
        self.en |= mask & 0xF
        self.write(R.addr("CTRL"), [self.en])

    def disable(self, mask: int) -> None:
        self.en &= ~mask
        self.write(R.addr("CTRL"), [self.en])

    def set_open_drain(self, mask: int) -> None:
        self.od = mask
        for op in pinvec_ops("PAD_OD", mask):
            self.write(*op)

    def set_filter(self, mask: int) -> None:
        self.filt = mask
        for op in pinvec_ops("PAD_FILT", mask):
            self.write(*op)

    def set_pins(self, levels: int, dirs: int) -> None:
        for op in pinvec_ops("PIN_OUT", levels) + pinvec_ops("PIN_OE", dirs):
            self.write(*op)

    def push(self, sm: int, words) -> None:
        self.write(R.addr("FIFO", sm), list(words))

    def pop(self, sm: int, count: int = 1) -> list[int]:
        return self.read(R.addr("FIFO", sm), count)

    def exec(self, sm: int, word: int) -> None:
        self.write(R.addr("EXEC", sm), [word])

    def read_imem(self, addr: int, sm: int = 0) -> int:
        """Read an instruction-memory word through engine `sm` (there is no host read port).

        Points the disabled engine there with an injected JMP and reads its instruction register.
        Side-set is switched off around the JMP so no pin moves, and ORG and PINCTRL1 are restored;
        the engine's PC is left at `addr`, so restart it before enabling it again.
        """
        org, pinctrl1 = self.read(R.addr("ORG", sm))[0], self.read(R.addr("PINCTRL1", sm))[0]
        for op in read_imem_ops(addr, sm, org, pinctrl1):
            if op is None:
                word = self.read(R.addr("EXEC", sm))[0]
            else:
                self.write(*op)
        return word

    def push_packet(self, sm: int, words, depth: int = 8, idle_pins: int = 0) -> None:
        """Queue one packet so that the engine never runs out of words in the middle of it.

        A program such as usb_tx starts sending at its first word and stalls on the wire if a later
        word is late. Streaming the words as the FIFO drains is too slow at the recommended host SCK
        (clk/16): status polls and frames cost more than the line takes to send a word. So this waits
        until the previous packet is out (TX FIFO empty and every pin in `idle_pins` released), stops
        the engine, preloads `depth` words (8 for a joined FIFO), starts it again and streams the
        rest, which then has a deadline of the preloaded words' time on the wire. Packets of at most
        `depth` words have no deadline at all.
        """
        words = list(words)
        while self.tx_level(sm) or (idle_pins and self.pin_oe() & idle_pins):
            self.idle(32)
        self.disable(1 << sm)
        self.push(sm, words[:depth])
        self.enable(1 << sm)
        words = words[depth:]
        while words:
            n = depth - self.tx_level(sm)
            if n > 0:
                self.push(sm, words[:n])
                words = words[n:]
            else:
                self.idle(32)

    def pin_oe(self) -> int:
        lo, hi = self.read(R.addr("PIN_OE_LO"), 2, inc=True)
        return lo | (hi << 16)

    def idle(self, cycles: int) -> None:
        """Let time pass between polls (a real transport's reads take time on their own)."""

    def rx_level(self, sm: int) -> int:
        return (self.read(R.addr("STAT", sm))[0] >> 12) & 15

    def tx_level(self, sm: int) -> int:
        return (self.read(R.addr("STAT", sm))[0] >> 8) & 15


class FastDriver(Driver):
    """Drives the simulator through its register strobes, skipping the SPI bit clock."""

    def __init__(self, bench: Bench):
        super().__init__()
        self.b = bench

    def idle(self, cycles):
        self.b.run(cycles)

    def write(self, addr, words, inc=False):
        iss = self.b.iss
        for w in words:
            iss.wr_en, iss.wr_addr, iss.wr_data = 1, addr & 0xFF, w & 0xFFFF
            self.b.step()
            self.b.step()
            addr += int(inc)

    def read(self, addr, count=1, inc=False):
        iss = self.b.iss
        out = []
        for _ in range(count):
            out.append(iss.peek(addr))
            k = iss._fifo_index(addr & 0xFF)
            if k is not None:
                iss.rd_armed = 1 if iss.sm[k].rx else 0
                iss.rd_pop, iss.addr = 1, addr & 0xFF
                self.b.step()
                self.b.step()
            addr += int(inc)
        return out


class SpiDriver(Driver):
    """Drives the simulator through its SPI pins, one clock cycle at a time.

    `half` is the SCK half period in clock cycles. `miso_delay` models the pads, the Tiny Tapeout
    multiplexer and SCK's phase against the chip clock: the host sees MISO that many cycles late.
    Reads need half > miso_delay + 3; real hardware should use half >= 8 (SCK at most clk/16).
    """

    def __init__(self, bench: Bench, half: int = 4, miso_delay: int = 0):
        super().__init__()
        self.b = bench
        self.half = half
        self.miso_delay = miso_delay
        self.first_bytes = (0, 0)  # the ID and status bytes seen in the last frame

    def idle(self, cycles):
        self.b.run(cycles)

    def _frame(self, data: bytes) -> bytes:
        wave, samples = spi_wave(data, self.half)
        bits = []
        want = {s - self.miso_delay for s in samples}
        for i, (cs_n, sck, mosi) in enumerate(wave):
            if i in want:
                bits.append(self.b.miso)
            self.b.step(cs_n, sck, mosi)
        out = bytearray()
        for i in range(0, len(bits), 8):
            v = 0
            for bit in bits[i:i + 8]:
                v = (v << 1) | bit
            out.append(v)
        self.first_bytes = (out[0], out[1])
        return bytes(out)

    def write(self, addr, words, inc=False):
        self._frame(frame_bytes(addr, words, write=True, inc=inc))

    def read(self, addr, count=1, inc=False):
        raw = self._frame(frame_bytes(addr, count=count, write=False, inc=inc))
        return [(raw[2 + 2 * i] << 8) | raw[3 + 2 * i] for i in range(count)]


IMAGE_FORMAT = 2  # images without "format" come from older loaders and must be rebuilt


def build_image(source: str, program: str, sm: int, origin: int = 0, defines: dict | None = None,
                **overrides) -> dict:
    """A load image: every SPI register write needed to load `program` into engine `sm`.

    The MicroPython driver (sw/host/micropython/bitweaver.py) replays images, so nothing has to be
    assembled on the board.
    """
    from .asm import assemble_file
    prog = assemble_file(source, defines)[program]
    frames = [[a, w, inc] for a, w, inc in load_ops(prog, sm, origin, **overrides)]
    owned, dirs, od, filt = image_pins(prog, **overrides)
    return {"format": IMAGE_FORMAT, "program": program, "sm": sm, "origin": origin, "length": len(prog.code),
            "frames": frames, "owned": owned, "dirs": dirs, "od": od, "filter": filt, "labels": prog.labels,
            "note": "Before the frames, hand the engine over (PinOwners.handoff: release the pins of the "
                    "program it replaces, then set od and filter in PAD_OD and PAD_FILT); OR the other "
                    "engines' CTRL.EN bits into every CTRL frame"}


def main() -> None:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Build a Bitweaver load image (JSON) for the board driver")
    ap.add_argument("source", help=".bw file")
    ap.add_argument("program", help="program name inside the file")
    ap.add_argument("--sm", type=int, default=0)
    ap.add_argument("--origin", type=int, default=0)
    ap.add_argument("--clkdiv", type=float)
    ap.add_argument("--f-clk", type=float, default=R.F_CLK,
                    help="chip clock in Hz for programs whose divider depends on it (default %(default)g)")
    ap.add_argument("-D", "--define", action="append", default=[], help="NAME=VALUE for the assembler")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    defines = {"F_CLK": int(args.f_clk)}
    defines.update({k: int(v, 0) for k, v in (d.split("=", 1) for d in args.define)})
    overrides = {"clkdiv": args.clkdiv} if args.clkdiv else {}
    image = build_image(args.source, args.program, args.sm, args.origin, defines, **overrides)
    with open(args.output, "w") as fh:
        json.dump(image, fh)
    print(f"{args.program}: {image['length']} instructions at {args.origin} on SM{args.sm}, "
          f"{len(image['frames'])} frames -> {args.output}")


if __name__ == "__main__":
    main()
