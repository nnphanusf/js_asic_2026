# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""SPI: sw/programs/spi.bw against models from cocotbext-spi.

* The controller programs (all four modes) talk to `StreamTarget`, a multi-byte target model built
  on cocotbext-spi's SpiSlaveBase. It decodes the wire itself and knows nothing about Bitweaver.
* The target program is driven by cocotbext-spi's SpiMaster.
Lock-step against the reference model stays on throughout.
"""

import cocotb
from cocotb.triggers import Timer
from cocotbext.spi import SpiBus, SpiConfig, SpiMaster

from bitweaver.asm import load_program
from bwtb import TB
from spi_models import StreamTarget

CS, MOSI, MISO, SCK = 11, 12, 13, 14


async def controller_frame(tb, sm, data):
    """Run one spi_ctrl frame; returns the bytes read back."""
    await tb.push(sm, [len(data) - 1])
    out = []
    for i in range(0, len(data), 4):
        chunk = list(data[i:i + 4])
        await tb.push(sm, chunk)
        await tb.wait_rx(sm, len(chunk), timeout_cycles=20000, poll=100)
        out += await tb.pop(sm, len(chunk))
    return bytes(out)


@cocotb.test()
async def test_spi_controller_all_modes(dut):
    """Modes 0..3 at 7.5 MHz and 1 MHz SCK; full-duplex frames of 1..9 bytes."""
    tb = TB(dut)
    await tb.start()
    origin = 0
    for mode in range(4):
        cpol, cpha = mode >> 1, mode & 1
        for clkdiv in (2, 15):
            bus = SpiBus(entity=dut, sclk_name=f"pad{SCK}", mosi_name=f"pad{MOSI}", miso_name=f"ext{MISO}",
                         cs_name=f"pad{CS}")
            frames = [bytes([0xA5]), bytes([0x00, 0xFF, 0x3C, 0x81, 0x7E]), bytes(range(1, 10))]
            replies = [0x5A, 0x12, 0x34, 0x56, 0x78, 0x9A] + list(range(0xF0, 0xF9))
            target = StreamTarget(bus, SpiConfig(cpol=bool(cpol), cpha=bool(cpha), sclk_freq=None),
                                  replies)
            prog = load_program(f"spi_ctrl_cpha{cpha}", file="spi", CPOL=cpol)
            await tb.load(prog, 0, origin=origin, clkdiv=clkdiv)
            await tb.enable(1)
            assert tb.pad(SCK) == cpol and tb.pad(CS) == 1
            got = [await controller_frame(tb, 0, f) for f in frames]
            await Timer(2, "us")
            assert target.frames == frames, (mode, clkdiv, target.frames)
            assert got == [bytes([0x5A]), bytes([0x12, 0x34, 0x56, 0x78, 0x9A]), bytes(range(0xF0, 0xF9))]
            assert tb.pad(SCK) == cpol and tb.pad(CS) == 1
            await tb.disable(1)
            target._run_coroutine_obj.cancel()
            dut.ext13.value = 1
    tb.done()


@cocotb.test()
async def test_spi_target_mode0(dut):
    """The target program answers cocotbext-spi's SpiMaster at 1 MHz and 2.5 MHz."""
    tb = TB(dut)
    await tb.start()
    await tb.load(load_program("spi_target", file="spi"), 1)
    await tb.enable(1 << 1)
    for freq in (1e6, 2.5e6):
        bus = SpiBus(entity=dut, sclk_name=f"ext{SCK}", mosi_name=f"ext{MOSI}", miso_name=f"pad{MISO}",
                     cs_name=f"ext{CS}")
        master = SpiMaster(bus, SpiConfig(sclk_freq=freq, cpol=False, cpha=False, msb_first=True))
        for sent, replies, expect in (([0x9F, 0x00, 0xFF, 0x42], [0xC3, 0x5A, 0x01], [0xC3, 0x5A, 0x01, 0xFF]),
                                      ([0x24, 0x81, 0x7E, 0x55], [0x11, 0x22, 0x33, 0x44], [0x11, 0x22, 0x33, 0x44])):
            await tb.push(1, replies)
            await master.write(sent, burst=True)
            got = list(await master.read(len(sent)))
            assert got == expect, (freq, got)
            await tb.wait_rx(1, 4, timeout_cycles=5000)
            rx = await tb.pop(1, 4)
            assert rx == sent, (freq, rx)
        master._run_coroutine_obj.cancel()
        master._SpiClock._run_coroutine_obj.cancel()
        dut.ext14.value = 0
        dut.ext11.value = 1
        await Timer(2, "us")
    tb.done()
