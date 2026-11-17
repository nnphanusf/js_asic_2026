"""Host-side helpers for the example protocol programs in sw/programs.

They only build and parse FIFO words; any transport (simulator, cocotb, MicroPython) can send them.
"""

from __future__ import annotations


# ---------------------------------------------------------------- UART (uart.bw)
def uart_clkdiv(baud: float, f_clk: float = 60e6) -> float:
    """Clock divider for uart_tx / uart_rx: 8 ticks per bit."""
    return f_clk / (8 * baud)


def uart_parity_word(byte: int, odd: bool = False) -> int:
    """9-bit word for the DATA_BITS = 9 variants: data plus an even (or odd) parity bit."""
    p = (bin(byte & 0xFF).count("1") & 1) ^ int(odd)
    return (byte & 0xFF) | (p << 8)


# ---------------------------------------------------------------- SPI (spi.bw)
def spi_frame(data) -> list[int]:
    """TX FIFO words for one spi_ctrl_cpha0/1 frame: the length minus one, then the bytes."""
    data = list(data)
    if not data:
        raise ValueError("an SPI frame needs at least one byte")
    return [len(data) - 1] + [b & 0xFF for b in data]


def spi_clkdiv(f_sck: float, f_clk: float = 60e6) -> float:
    """Clock divider for the SPI controller: 4 ticks per bit."""
    return f_clk / (4 * f_sck)


# ---------------------------------------------------------------- I2C (i2c.bw)
def i2c_cmd(byte: int, ack: int = 1, start: bool = False, stop: bool = False) -> int:
    """One i2c_ctrl command word."""
    return (int(start) << 15) | ((byte & 0xFF) << 7) | ((ack & 1) << 6) | (int(stop) << 5)


def i2c_write_cmds(address: int, data=(), stop: bool = True) -> list[int]:
    """START, address with W, data bytes, optional STOP."""
    data = list(data)
    words = [i2c_cmd(address << 1, start=True, stop=stop and not data)]
    words += [i2c_cmd(b, stop=stop and i == len(data) - 1) for i, b in enumerate(data)]
    return words


def i2c_read_cmds(address: int, count: int, stop: bool = True) -> list[int]:
    """(Repeated) START, address with R, `count` reads (ACK all but the last, NACK the last)."""
    words = [i2c_cmd((address << 1) | 1, start=True)]
    words += [i2c_cmd(0xFF, ack=int(i == count - 1), stop=stop and i == count - 1) for i in range(count)]
    return words


def i2c_result(word: int) -> tuple[int, bool]:
    """(byte seen on SDA, acknowledged) for one i2c_ctrl RX word."""
    return (word >> 1) & 0xFF, (word & 1) == 0


def i2c_clkdiv(f_scl: float, f_clk: float = 60e6) -> int:
    """Clock divider for i2c_ctrl (10 ticks per bit, SCL low for 5) at or below `f_scl`.

    Rounds up so the bus never runs faster than asked, and keeps tLOW (5 ticks) at or above the
    I2C minimum: 4.7 us up to 100 kHz, 1.3 us above. 100 kHz -> 60, 400 kHz -> 16 (375 kHz).
    """
    import math
    t_low = 4.7e-6 if f_scl <= 100e3 else 1.3e-6
    return max(math.ceil(f_clk / (10 * f_scl) - 1e-9), math.ceil(t_low * f_clk / 5 - 1e-9))


# ---------------------------------------------------------------- WS2812 (ws2812.bw)
def ws2812_words(pixels) -> list[int]:
    """Pack (r, g, b) pixels into 16-bit words, GRB order, MSB first; the tail is padded with 0 bits."""
    bits = []
    for r, g, b in pixels:
        for byte in (g, r, b):
            bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * (-len(bits) % 16)
    return [int("".join(map(str, bits[i:i + 16])), 2) for i in range(0, len(bits), 16)]


# ---------------------------------------------------------------- JTAG (jtag.bw)
class JtagSeq:
    """Build TMS/TDI sequences for jtag.bw from TAP-level operations and decode the TDO results.

    The TAP is assumed to be in Run-Test/Idle between operations (after `reset()`).
    """

    def __init__(self):
        self.tms: list[int] = []
        self.tdi: list[int] = []
        self.reads: list[tuple[int, int]] = []  # (first cycle, bit count) of each shift to decode

    def _clk(self, tms: int, tdi: int = 0):
        self.tms.append(tms)
        self.tdi.append(tdi)

    def reset(self):
        """Test-Logic-Reset (five cycles with TMS high), then Run-Test/Idle."""
        for _ in range(5):
            self._clk(1)
        self._clk(0)

    def _shift(self, value: int, n: int, ir: bool):
        self._clk(1)  # Select-DR-Scan
        if ir:
            self._clk(1)  # Select-IR-Scan
        self._clk(0)  # Capture
        self._clk(0)  # Shift
        self.reads.append((len(self.tms), n))
        for i in range(n):
            self._clk(1 if i == n - 1 else 0, (value >> i) & 1)  # the last bit moves to Exit1
        self._clk(1)  # Update
        self._clk(0)  # Run-Test/Idle

    def shift_ir(self, value: int, n: int):
        self._shift(value, n, ir=True)

    def shift_dr(self, value: int, n: int):
        self._shift(value, n, ir=False)

    def words(self) -> list[int]:
        """TX words, padded with Run-Test/Idle cycles to a multiple of eight."""
        while len(self.tms) % 8:
            self._clk(0)
        out = []
        for i in range(0, len(self.tms), 8):
            w = 0
            for j in range(8):
                w |= ((self.tms[i + j] << 1) | self.tdi[i + j]) << (2 * j)
            out.append(w)
        return out

    def decode(self, rx_words) -> list[int]:
        """Values shifted out of TDO by each shift_ir / shift_dr, LSB first."""
        tdo = []
        for w in rx_words:
            tdo += [(w >> (8 + j)) & 1 for j in range(8)]
        out = []
        for first, n in self.reads:
            out.append(sum(tdo[first + i] << i for i in range(n)))
        return out


# ---------------------------------------------------------------- SWD (swd.bw)
def _swd_cmd(read: bool, n: int) -> int:
    assert 1 <= n <= 16
    return (int(read) << 15) | (n - 1)


class SwdSeq:
    """Build swd.bw command words for whole SWD transactions and decode the replies (Arm ADIv5)."""

    OK, WAIT, FAULT = 0b001, 0b010, 0b100

    def __init__(self):
        self.words: list[int] = []
        self.reads: list[int] = []  # bit count of every read command, in order
        self.ops: list[tuple] = []  # ("read" | "write", index of the first read command)

    def _w(self, value: int, n: int):
        self.words += [_swd_cmd(False, n), value & ((1 << n) - 1)]

    def _r(self, n: int):
        self.words.append(_swd_cmd(True, n))
        self.reads.append(n)

    def line_reset(self):
        """At least 50 cycles with SWDIO high, then idle cycles."""
        for _ in range(4):
            self._w(0xFFFF, 16)
        self._w(0, 8)

    def jtag_to_swd(self):
        """Line reset, the JTAG-to-SWD select sequence 0xE79E, line reset, idle."""
        self.line_reset()
        self._w(0xE79E, 16)
        self.line_reset()

    @staticmethod
    def request(apndp: int, rnw: int, addr: int) -> int:
        a2, a3 = (addr >> 2) & 1, (addr >> 3) & 1
        parity = (apndp + rnw + a2 + a3) & 1
        return 1 | (apndp << 1) | (rnw << 2) | (a2 << 3) | (a3 << 4) | (parity << 5) | (0 << 6) | (1 << 7)

    def read(self, apndp: int, addr: int, request: int | None = None):
        self.ops.append(("read", len(self.reads)))
        self._w(self.request(apndp, 1, addr) if request is None else request, 8)
        self._r(1)  # turnaround
        self._r(3)  # ACK
        self._r(16)
        self._r(16)
        self._r(1)  # parity
        self._r(1)  # turnaround back to the host
        self._w(0, 8)  # idle

    def write(self, apndp: int, addr: int, value: int):
        self.ops.append(("write", len(self.reads)))
        self._w(self.request(apndp, 0, addr), 8)
        self._r(1)
        self._r(3)
        self._r(1)  # turnaround back to the host
        self._w(value & 0xFFFF, 16)
        self._w(value >> 16, 16)
        self._w(bin(value & 0xFFFFFFFF).count("1") & 1, 1)
        self._w(0, 8)

    def n_results(self) -> int:
        return len(self.reads)

    def decode(self, rx_words) -> list[tuple]:
        """One tuple per transaction: ("read", ack, value, parity_ok) or ("write", ack)."""
        vals = [w >> (16 - n) for w, n in zip(rx_words, self.reads)]
        out = []
        for kind, i in self.ops:
            ack = vals[i + 1]
            if kind == "read":
                value = vals[i + 2] | (vals[i + 3] << 16)
                out.append(("read", ack, value, (bin(value).count("1") & 1) == vals[i + 4]))
            else:
                out.append(("write", ack))
        return out


# ---------------------------------------------------------------- PS/2 (ps2.bw)
def ps2_decode(word: int) -> tuple[int, bool]:
    """(data byte, frame valid) for one ps2_rx word: start 0, odd parity, stop 1."""
    f = word >> 5
    data = (f >> 1) & 0xFF
    ok = (f & 1) == 0 and (f >> 10) & 1 == 1 and (bin(data).count("1") + ((f >> 9) & 1)) & 1 == 1
    return data, ok


# ---------------------------------------------------------------- CAN (can.bw)
def can_crc15(bits) -> int:
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def can_frame_bits(ident: int, data=b"", rtr: bool = False) -> list[int]:
    """Unstuffed bits of a classical base frame from SOF to the end of the data field."""
    data = bytes(data)
    assert 0 <= ident < 2048 and len(data) <= 8
    bits = [0] + [(ident >> i) & 1 for i in range(10, -1, -1)] + [int(rtr), 0, 0]
    bits += [(len(data) >> i) & 1 for i in range(3, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    return bits


def can_tx_words(ident: int, data=b"", rtr: bool = False) -> list[int]:
    """TX FIFO words for can_tx: bit count - 1, then the bits MSB first."""
    bits = can_frame_bits(ident, data, rtr)
    padded = bits + [0] * (-len(bits) % 16)
    return [len(bits) - 1] + [int("".join(map(str, padded[i:i + 16])), 2) for i in range(0, len(padded), 16)]


def can_rx_decode(words) -> tuple[int, bytes, bool]:
    """(identifier, data, CRC correct) from the words can_rx pushed for one frame."""
    count = words[-1]
    full, rest = divmod(count, 16)
    bits = []
    for w in words[:full]:
        bits += [(w >> i) & 1 for i in range(15, -1, -1)]
    bits += [(words[full] >> i) & 1 for i in range(rest - 1, -1, -1)]

    def field(start, n):
        return sum(bits[start + i] << (n - 1 - i) for i in range(n))

    ident = field(1, 11)
    dlc = min(field(15, 4), 8)
    data = bytes(field(19 + 8 * i, 8) for i in range(dlc))
    end = 19 + 8 * dlc
    crc = field(end, 15)
    return ident, data, crc == can_crc15(bits[:end])


# ---------------------------------------------------------------- low-speed USB (usb.bw)
USB_PID = {"OUT": 0x1, "IN": 0x9, "SOF": 0x5, "SETUP": 0xD, "DATA0": 0x3, "DATA1": 0xB, "ACK": 0x2,
           "NAK": 0xA, "STALL": 0xE}


def usb_pid_byte(pid: int) -> int:
    return (pid & 0xF) | ((~pid & 0xF) << 4)


def usb_crc16(data) -> int:
    """CRC16 of a DATA payload as transmitted: send the result LSB first (little-endian bytes)."""
    crc = 0xFFFF
    for byte in bytes(data):
        for i in range(8):
            fb = (crc ^ (byte >> i)) & 1
            crc = (crc >> 1) ^ (0xA001 if fb else 0)
    return crc ^ 0xFFFF


def usb_crc5(value11: int) -> int:
    """CRC5 of a token's 11-bit address/endpoint field, to be sent LSB first."""
    crc = 0x1F
    for i in range(11):
        fb = (crc ^ (value11 >> i)) & 1
        crc = (crc >> 1) ^ (0x14 if fb else 0)
    return crc ^ 0x1F


def _bits_lsb(value: int, n: int) -> list[int]:
    return [(value >> i) & 1 for i in range(n)]


def _pack_lsb(bits) -> list[int]:
    bits = list(bits) + [0] * (-len(bits) % 16)
    return [sum(b << j for j, b in enumerate(bits[i:i + 16])) for i in range(0, len(bits), 16)]


def usb_tx_words(prog, pid: str, data=None, addr: int | None = None, endp: int = 0) -> list[int]:
    """TX FIFO words for usb_tx: a handshake (pid only), a token (addr/endp) or a DATA packet."""
    from . import regmap as R
    from .asm import NOP, enc_jmp, enc_mov, enc_set

    words: list[int] = []

    def seg(bits, instr, has_data=True):
        n = len(bits) if has_data else 16
        words.extend([n - 1, instr])
        if has_data:
            words.extend(_pack_lsb(bits))

    seg(_bits_lsb(0x80, 8) + _bits_lsb(usb_pid_byte(USB_PID[pid]), 8), NOP)
    if addr is not None:
        field = (addr & 0x7F) | ((endp & 0xF) << 7)
        seg(_bits_lsb(field, 11) + _bits_lsb(usb_crc5(field), 5), NOP)
    if data is not None:
        data = bytes(data)
        if data:
            payload = [b for byte in data for b in _bits_lsb(byte, 8)]
            seg(payload, enc_set(R.SET_BAU, R.BAU_NRZI | R.BAU_STUFF | R.BAU_CRC))
        seg([], enc_mov(R.MOV_OSR, R.SRC_CRC, R.MOVOP_INVERT), has_data=False)
    words.extend([0, enc_jmp(R.COND_ALWAYS, prog.labels["eop"])])
    return words


def usb_rx_decode(words) -> tuple[str | None, bytes, bool]:
    """(PID name, bytes after the PID, packet valid) from the words usb_rx pushed for one packet."""
    count = words[-1]
    full, rest = divmod(count, 16)
    bits = []
    for w in words[:full]:
        bits += _bits_lsb(w, 16)
    if rest:
        bits += _bits_lsb(words[full] >> (16 - rest), rest)
    if count % 8 or count < 16:
        return None, b"", False
    raw = bytes(sum(bits[8 * i + j] << j for j in range(8)) for i in range(count // 8))
    sync, pid_b, body = raw[0], raw[1], raw[2:]
    names = {v: k for k, v in USB_PID.items()}
    pid = pid_b & 0xF
    ok = sync == 0x80 and (pid_b >> 4) == (~pid & 0xF) and pid in names
    name = names.get(pid)
    if name in ("DATA0", "DATA1"):
        ok = ok and len(body) >= 2 and usb_crc16(body[:-2]) == body[-2] | (body[-1] << 8)
        body = body[:-2]
    elif name in ("OUT", "IN", "SOF", "SETUP"):
        ok = ok and len(body) == 2
        field = body[0] | (body[1] << 8)
        ok = ok and usb_crc5(field & 0x7FF) == field >> 11
    return name, body, ok
