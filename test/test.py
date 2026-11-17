# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

CLK_PERIOD_PS = 16666  # ~60 MHz, matches info.yaml clock_hz (even ps so the clock splits evenly)

CS_N = 1 << 7
SCK = 1 << 6
MOSI = 1 << 5
PIN_MASK = 0x1F  # ui_in[4:0] = PIN0..PIN4
SPI_HALF = 8  # clk cycles per SCK half-period (SCK = clk/16, within the clk/8 limit)

PROBE_ID = 0xB7
CMD_ID, CMD_UI, CMD_UIO, CMD_CYCLES = 0, 1, 2, 3


async def reset(dut):
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    dut.ena.value = 1
    dut.ui_in.value = CS_N
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


class ProbeHost:
    """Bit-banged SPI mode 0 controller on the host pins; keeps PIN0..PIN4 as given."""

    def __init__(self, dut, pins=0):
        self.dut = dut
        self.pins = pins & PIN_MASK

    def _drive(self, cs_n, sck, mosi):
        self.dut.ui_in.value = (CS_N if cs_n else 0) | (SCK if sck else 0) | (MOSI if mosi else 0) | self.pins

    def miso(self):
        return (int(self.dut.uo_out.value) >> 7) & 1

    async def transfer(self, tx_bytes):
        """Assert CS_N, exchange the bytes MSB first, release CS_N. Returns the bytes read."""
        rx_bytes = []
        self._drive(1, 0, 0)
        await ClockCycles(self.dut.clk, SPI_HALF)
        self._drive(0, 0, 0)
        await ClockCycles(self.dut.clk, SPI_HALF)
        for byte in tx_bytes:
            value = 0
            for bit in range(7, -1, -1):
                mosi = (byte >> bit) & 1
                self._drive(0, 0, mosi)
                await ClockCycles(self.dut.clk, SPI_HALF)
                value = (value << 1) | self.miso()  # sample just before the rising edge
                self._drive(0, 1, mosi)
                await ClockCycles(self.dut.clk, SPI_HALF)
            rx_bytes.append(value)
        self._drive(0, 0, 0)
        await ClockCycles(self.dut.clk, SPI_HALF)
        self._drive(1, 0, 0)
        await ClockCycles(self.dut.clk, SPI_HALF)
        return rx_bytes


@cocotb.test()
async def test_idle_after_reset(dut):
    """Every output is low and every bidirectional pin is Hi-Z after reset."""
    await reset(dut)
    assert dut.uo_out.value == 0
    assert dut.uio_out.value == 0
    assert dut.uio_oe.value == 0


@cocotb.test()
async def test_idle_ignores_protocol_pins(dut):
    """With the host deselected, toggling the protocol and host pins drives nothing."""
    await reset(dut)
    for pattern in (0xFF, 0xA5, 0x5A, 0x00, 0x7F):
        dut.ui_in.value = pattern | CS_N
        dut.uio_in.value = pattern ^ 0xFF
        await ClockCycles(dut.clk, 3)
        assert dut.uo_out.value == 0
        assert dut.uio_out.value == 0
        assert dut.uio_oe.value == 0


@cocotb.test()
async def test_probe_id(dut):
    """The first byte after CS_N falls is the ID, and cmd 0 returns it again."""
    await reset(dut)
    host = ProbeHost(dut)
    assert await host.transfer([CMD_ID, CMD_ID, CMD_ID]) == [PROBE_ID, PROBE_ID, PROBE_ID]
    assert dut.uo_out.value == 0  # MISO released after CS_N rises


@cocotb.test()
async def test_probe_reads_ui_pins(dut):
    """cmd 1 returns PIN0..PIN4 (ui_in[4:0]) through the synchronizers."""
    await reset(dut)
    for pins in (0x00, 0x1F, 0x15, 0x0A, 0x01, 0x10):
        host = ProbeHost(dut, pins)
        assert await host.transfer([CMD_UI, CMD_ID]) == [PROBE_ID, pins]


@cocotb.test()
async def test_probe_reads_uio_pins(dut):
    """cmd 2 returns PIN11..PIN18 (uio_in) while the uio pins stay inputs."""
    await reset(dut)
    host = ProbeHost(dut)
    rng = random.Random(0xB17)
    for value in [0x00, 0xFF, 0xA5, 0x5A] + [rng.randrange(256) for _ in range(8)]:
        dut.uio_in.value = value
        assert await host.transfer([CMD_UIO, CMD_ID]) == [PROBE_ID, value]
        assert dut.uio_oe.value == 0


@cocotb.test()
async def test_probe_cycle_counter(dut):
    """cmd 3 returns a counter that advances by one per clock while ena is high and holds while ena is low."""
    await reset(dut)
    host = ProbeHost(dut)
    # Two back-to-back reads: the reply is loaded on the 8th SCK rise of each byte, so the
    # two samples are exactly 8 SCK periods (8 * 2 * SPI_HALF clocks) apart.
    _, first, second = await host.transfer([CMD_CYCLES, CMD_CYCLES, CMD_ID])
    assert (second - first) % 256 == (8 * 2 * SPI_HALF) % 256

    dut.ena.value = 0
    _, first, second = await host.transfer([CMD_CYCLES, CMD_CYCLES, CMD_ID])
    assert first == second
    dut.ena.value = 1


@cocotb.test()
async def test_probe_reset_mid_transfer(dut):
    """Releasing CS_N in the middle of a byte resets the probe; the next transfer starts with the ID."""
    await reset(dut)
    host = ProbeHost(dut)
    host._drive(0, 0, 0)
    await ClockCycles(dut.clk, SPI_HALF)
    for _ in range(3):
        host._drive(0, 1, 1)
        await ClockCycles(dut.clk, SPI_HALF)
        host._drive(0, 0, 1)
        await ClockCycles(dut.clk, SPI_HALF)
    host._drive(1, 0, 0)
    await ClockCycles(dut.clk, SPI_HALF)
    assert await host.transfer([CMD_ID]) == [PROBE_ID]
