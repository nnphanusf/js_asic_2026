"""Timing on real hardware: program dividers follow the chip clock, and the host SPI read margin.

The cocotb suite runs at 60 MHz with ideal pads. The chip is rated for 50 MHz (regmap.F_CLK), so these
tests run the cycle-accurate simulator with 20 ns cycles and check timings in real units, and model
the delay that pads and the Tiny Tapeout multiplexer add to MISO.
"""

import pytest

from bitweaver import regmap as R
from bitweaver.asm import load_program
from bitweaver.loader import Bench, FastDriver, SpiDriver, clkdiv_fields
from bitweaver.proto import (USB_PID, can_clkdiv, i2c_timing, spi_clkdiv, uart_clkdiv, usb_crc5, usb_crc16, usb_pid_byte,
                             usb_rx_decode, usb_tx_words)

NS_50 = 20.0  # one clock cycle at the rated 50 MHz, in ns


def effective_div(div: float) -> float:
    i, f = clkdiv_fields(div)
    return i + f / 256


# ---------------------------------------------------------------- dividers
def test_rated_clock_is_the_default():
    assert R.F_CLK == 50_000_000
    assert uart_clkdiv(115200) == pytest.approx(50e6 / (8 * 115200))
    assert spi_clkdiv(1e6) == pytest.approx(12.5)
    assert can_clkdiv(500e3) == pytest.approx(10.0)
    assert can_clkdiv(1e6) == pytest.approx(5.0)
    assert i2c_timing(100e3)[0] == 50
    assert i2c_timing(400e3)[0] == 13


@pytest.mark.parametrize("name, at50, at60", [("ws2812", 6, 7), ("onewire", 50, 60),
                                              ("usb_tx", 50 / 15, 4), ("usb_rx", 50 / 15, 4)])
def test_program_dividers_follow_the_clock(name, at50, at60):
    """Programs whose timing is absolute compute their divider from F_CLK; at 60 MHz (the cocotb
    clock) they keep the dividers the simulations were verified with."""
    assert load_program(name).config["clkdiv"] == pytest.approx(at50)
    assert load_program(name, F_CLK=60_000_000).config["clkdiv"] == pytest.approx(at60)


def test_usb_bit_rate_at_50mhz():
    div = effective_div(load_program("usb_tx").config["clkdiv"])
    rate = 50e6 / (10 * div)
    assert abs(rate / 1.5e6 - 1) < 0.0025  # low-speed USB allows 1.5 %


@pytest.mark.parametrize("bitrate", [500e3, 1e6])
def test_can_bit_rate_at_50mhz(bitrate):
    div = effective_div(can_clkdiv(bitrate))
    assert 50e6 / (10 * div) == pytest.approx(bitrate)


# ---------------------------------------------------------------- low-speed USB packets at 50 MHz
GET_DESCRIPTOR = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00])


def _lsb(value, n):
    return [(value >> i) & 1 for i in range(n)]


def _usb_line(pid, data=None):
    """(D-, D+) per bit time for one packet from J: SYNC, PID, payload, CRC16, stuffing, NRZI, EOP."""
    bits = _lsb(0x80, 8) + _lsb(usb_pid_byte(USB_PID[pid]), 8)
    if data is not None:
        bits += [b for byte in data for b in _lsb(byte, 8)] + _lsb(usb_crc16(data), 16)
    stuffed, ones = [], 0
    for b in bits:
        stuffed.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            stuffed.append(0)
            ones = 0
    level, out = 1, []
    for b in stuffed:
        level ^= b ^ 1  # NRZI: a 0 is a transition
        out.append((level, level ^ 1))
    return out + [(0, 0), (0, 0), (1, 0)]


def _usb_receive(pid, data, offset, phase):
    """usb_se0 + usb_rx at 50 MHz against a sender whose bit rate is off by `offset`."""
    bench = Bench()
    d = FastDriver(bench)
    d.load(load_program("usb_se0"), 0, clkdiv=1)
    d.load(load_program("usb_rx"), 1)
    d.enable(0b11)
    bench.run(50)
    line = _usb_line(pid, data)
    period = 50e6 / (1.5e6 * (1 + offset))
    t0 = bench.cycle + 10 + phase

    def sender(b):
        k = int((b.cycle - t0) // period) if b.cycle >= t0 else -1
        dm, dp = line[k] if 0 <= k < len(line) else (1, 0)
        b.uio_ext = (b.uio_ext & ~3) | dm | (dp << 1)

    bench.hooks.append(sender)
    bench.run(int(period * (len(line) + 4)))
    return usb_rx_decode(d.pop(1, d.rx_level(1)))


@pytest.mark.parametrize("offset", [-0.004, 0.0, 0.004])
def test_usb_rx_at_50mhz_with_sender_offset(offset):
    """usb_rx synchronizes once, on the first K of SYNC. At 50 MHz it receives the longest low-speed
    packet (8 data bytes) from a sender up to 0.4 % off the nominal 1.5 Mbit/s, at any phase."""
    for phase in (0.0, 0.25, 0.5, 0.75):
        assert _usb_receive("DATA0", GET_DESCRIPTOR, offset, phase * 33) == ("DATA0", GET_DESCRIPTOR, True)
        assert _usb_receive("ACK", None, 5 * offset, phase * 33) == ("ACK", b"", True)


def _usb_bits_line(bits, trailing):
    """(D-, D+) per bit for raw packet bits, stuffed and NRZI encoded, then EOP. `trailing` says what
    happens to a stuff bit due after the last bit: "ok" sends it, "one" sends a 1 (seven ones in a
    row), "omit" leaves it out (SE0 straight after six ones)."""
    stuffed, ones = [], 0
    for i, b in enumerate(bits):
        stuffed.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            if i < len(bits) - 1 or trailing == "ok":
                stuffed.append(0)
            elif trailing == "one":
                stuffed.append(1)
            ones = 0
    level, out = 1, []
    for b in stuffed:
        level ^= b ^ 1
        out.append((level, level ^ 1))
    return out + [(0, 0), (0, 0), (1, 0)]


def _usb_receive_line(line, offset, phase):
    bench = Bench()
    d = FastDriver(bench)
    d.load(load_program("usb_se0"), 0, clkdiv=1)
    d.load(load_program("usb_rx"), 1)
    d.enable(0b11)
    bench.run(50)
    period = 50e6 / (1.5e6 * (1 + offset))
    t0 = bench.cycle + 10 + phase

    def sender(b):
        k = int((b.cycle - t0) // period) if b.cycle >= t0 else -1
        dm, dp = line[k] if 0 <= k < len(line) else (1, 0)
        b.uio_ext = (b.uio_ext & ~3) | dm | (dp << 1)

    bench.hooks.append(sender)
    bench.run(int(period * (len(line) + 4)))
    return usb_rx_decode(d.pop(1, d.rx_level(1)))


_FIELD = 0x0C | (0x8 << 7)  # IN to address 12, endpoint 8: its CRC5 ends the packet with six ones
_LONG = b"\xff" * 6 + b"\x00\x53"  # 8 bytes, many stuff bits, and a CRC16 that ends with six ones
_STUFF_PACKETS = {
    "IN": _lsb(0x80, 8) + _lsb(usb_pid_byte(USB_PID["IN"]), 8) + _lsb(_FIELD, 11) + _lsb(usb_crc5(_FIELD), 5),
    "DATA1": _lsb(0x80, 8) + _lsb(usb_pid_byte(USB_PID["DATA1"]), 8)
    + [b for x in _LONG for b in _lsb(x, 8)] + _lsb(usb_crc16(_LONG), 16),
}


@pytest.mark.parametrize("pid", _STUFF_PACKETS)
@pytest.mark.parametrize("trailing", ["ok", "one", "omit"])
def test_usb_rx_checks_the_trailing_stuff_bit(pid, trailing):
    """A packet whose CRC ends with six ones needs a stuff bit before the EOP (USB 2.0 7.1.9). usb_rx
    accepts it with the stuff bit and rejects it with the stuff bit sent as 1 (seven ones: the BAU's
    stuff error) or left out (SE0 where the stuff bit belongs), from a sender up to 0.4 % off, at any
    phase."""
    bits = _STUFF_PACKETS[pid]
    assert bits[-6:] == [1] * 6
    line = _usb_bits_line(bits, trailing)
    for offset in (-0.004, 0.0, 0.004):
        for phase in range(0, 33, 4):
            name, _, ok = _usb_receive_line(line, offset, phase)
            assert ok == (trailing == "ok"), (offset, phase)
            if ok:
                assert name == pid


def test_usb_rx_rejects_seven_ones_inside_a_packet():
    bits = _STUFF_PACKETS["DATA1"]
    line = _usb_bits_line(bits, "ok")
    k = 16 + 6  # the stuff bit after the first six ones of the payload (SYNC and PID have no run of six)
    line = line[:k] + [line[k - 1]] + line[k + 1:]  # sent without its transition: a seventh one
    for phase in (0, 11, 22):
        assert _usb_receive_line(line, 0.0, phase)[2] is False


def test_usb_tx_at_50mhz():
    """usb_tx with the fractional divider: decoded at the nominal 1.5 Mbit/s from the first K, the
    packet is exact, and no bit edge is more than one clock from its place on the divider's grid."""
    bench = Bench(trace=True)
    d = FastDriver(bench)
    prog = load_program("usb_tx")
    d.load(prog, 0, origin=0)
    d.enable(1)
    start = len(bench.trace)
    words = usb_tx_words(prog, "DATA0", GET_DESCRIPTOR)
    while words:  # 13 words through the 8-word joined FIFO, as fast as it drains
        n = 8 - d.tx_level(0)
        d.push(0, words[:n])
        words = words[n:]
        bench.run(34)
    bench.run(200 * 34)
    lines = [((p >> 11) & 1, (p >> 12) & 1) for p in bench.trace[start:]]
    first_k = lines.index((0, 1))
    period = 50e6 / 1.5e6
    want = _usb_line("DATA0", GET_DESCRIPTOR)
    got = [lines[first_k + int((k + 0.5) * period)] for k in range(len(want))]
    assert got == want
    # Edges against the divider's own average bit time (0.04 % short of 1/1.5 MHz at 50 MHz).
    bit = 10 * effective_div(prog.config["clkdiv"])
    edges = [i - first_k for i in range(first_k + 1, len(lines)) if lines[i] != lines[i - 1]]
    assert max(abs(e - round(e / bit) * bit) for e in edges) <= 1.0


def test_usb_tx_at_clk16_host():
    """Two packets back to back through the SPI host port at SCK = clk/16, the rate recommended for
    the board: push_packet preloads the joined FIFO, so the 13-word DATA packet never runs dry
    (streamed as the FIFO drains, the same packet stalls on the wire and fails its stuffing and CRC),
    and waits for the first packet to finish before stopping the engine for the second."""
    bench = Bench(trace=True)
    d = SpiDriver(bench, half=8)
    prog = load_program("usb_tx")
    d.load(prog, 0, origin=0)
    d.enable(1)
    start = len(bench.trace)
    packets = [("DATA0", b"abcdefgh"), ("ACK", None)]
    for pid, data in packets:
        d.push_packet(0, usb_tx_words(prog, pid, data), idle_pins=(1 << 11) | (1 << 12))
    bench.run(200 * 34)
    lines = [((p >> 11) & 1, (p >> 12) & 1) for p in bench.trace[start:]]
    period = 50e6 / 1.5e6
    at = 0
    for pid, data in packets:
        first_k = lines.index((0, 1), at)
        want = _usb_line(pid, data)
        got = [lines[first_k + int((k + 0.5) * period)] for k in range(len(want))]
        assert got == want, pid
        at = first_k + int(len(want) * period)


# ---------------------------------------------------------------- WS2812 on the simulator, 20 ns cycles
def _runs(levels):
    """(level, length) runs of a 0/1 sequence."""
    out = []
    for v in levels:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([v, 1])
    return out


@pytest.mark.parametrize("f_clk", [40e6, 50e6, 60e6])
def test_ws2812_pulses_in_spec(f_clk):
    ns = 1e9 / f_clk
    bench = Bench(trace=True)
    d = FastDriver(bench)
    d.load(load_program("ws2812", F_CLK=int(f_clk)), 0, origin=0)
    d.push(0, [0xF0F0, 0x0F0F])  # 32 bits, both kinds
    d.enable(1)
    start = len(bench.trace)
    bench.run(32 * 10 * 8 + 200)
    line = [(p >> 5) & 1 for p in bench.trace[start:]]
    runs = _runs(line)
    highs = [n * ns for v, n in runs if v == 1]
    assert len(highs) == 32
    zeros = [t for t in highs if t < 500]
    ones = [t for t in highs if t >= 500]
    assert len(zeros) == 16 and len(ones) == 16
    assert all(220 <= t <= 380 for t in zeros), zeros
    assert all(580 <= t <= 1000 for t in ones), ones
    rises = [i for i in range(1, len(line)) if line[i] and not line[i - 1]]
    periods = [(b - a) * ns for a, b in zip(rises, rises[1:])]
    assert all(650 <= p <= 1850 for p in periods), periods


# ---------------------------------------------------------------- 1-Wire read slots, 20 ns cycles
def _onewire_read_byte(prog, hold_us: float) -> int:
    """Run one byte exchange (a read: all write-1 slots) against a device that answers 0 by holding
    the line low for `hold_us` after each slot starts. Every cycle is 20 ns (50 MHz)."""
    bench = Bench()
    d = FastDriver(bench)
    d.load(prog, 0, origin=0)
    hold = round(hold_us * 1000 / NS_50)
    state = {"prev": 1, "until": -1}
    bit = 1 << (17 - 11)  # PIN17 = uio[6]

    def device(b):
        line = b.pin(17)
        if state["prev"] and not line and b.cycle > state["until"]:
            state["until"] = b.cycle + hold  # a slot started: hold the line low
        b.uio_ext = (b.uio_ext & ~bit) if b.cycle < state["until"] else (b.uio_ext | bit)
        state["prev"] = line

    bench.hooks.append(device)
    d.push(0, [0x01FF])  # exchange one byte, sending 0xFF (eight read slots)
    d.enable(1)
    for _ in range(200):
        bench.run(500)
        if d.rx_level(0):
            break
    return d.pop(0)[0] >> 8


def test_onewire_reads_a_device_that_releases_at_16us():
    """The master must sample within 15 us of the slot start; a device may release a 0 soon after.
    With the divider from the real clock the read is right; with the 60 MHz divider at 50 MHz the
    sample moves to 18 us and every 0 reads as 1."""
    assert _onewire_read_byte(load_program("onewire"), hold_us=16) == 0x00
    assert _onewire_read_byte(load_program("onewire", F_CLK=60_000_000), hold_us=16) == 0xFF


# ---------------------------------------------------------------- host SPI read margin
@pytest.mark.parametrize("half, max_delay", [(4, 0), (6, 2), (8, 4)])
def test_host_spi_read_margin(half, max_delay):
    """MISO changes 2-3 cycles after the chip sees SCK fall, so a read only works if the host's SCK
    low time is more than 3 cycles plus every cycle of pad, multiplexer and phase delay. At clk/8
    (half = 4) there is no margin left, which is why the docs ask for clk/16 on real hardware."""
    for delay in range(max_delay + 1):
        assert SpiDriver(Bench(), half=half, miso_delay=delay).read(R.addr("ID")) == [R.CHIP_ID]
    assert SpiDriver(Bench(), half=half, miso_delay=max_delay + 1).read(R.addr("ID")) != [R.CHIP_ID]


def test_host_spi_writes_and_reads_at_clk16_with_delay():
    b = Bench()
    d = SpiDriver(b, half=8, miso_delay=3)
    d.write(R.addr("INT_EN"), [0x1234])
    assert d.read(R.addr("INT_EN")) == [0x1234]
