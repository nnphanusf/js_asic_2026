"""Timing on real hardware: program dividers follow the chip clock, and the host SPI read margin.

The cocotb suite runs at 60 MHz with ideal pads. The chip is rated for 50 MHz (regmap.F_CLK), so these
tests run the cycle-accurate simulator with 20 ns cycles and check timings in real units, and model
the delay that pads and the Tiny Tapeout multiplexer add to MISO.
"""

import pytest

from bitweaver import regmap as R
from bitweaver.asm import load_program
from bitweaver.loader import Bench, FastDriver, SpiDriver, clkdiv_fields
from bitweaver.proto import can_clkdiv, i2c_timing, spi_clkdiv, uart_clkdiv

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
