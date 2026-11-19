# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""Two protocols at once: an SPI controller on SM0 and an I2C controller (400 kHz) on SM1.

Each engine runs its own program at its own clock divider, each bus is checked by its own
independent model, and all host traffic shares the single SPI host port. Lock-step stays on.
"""

import cocotb
from cocotb.triggers import Timer
from cocotbext.spi import SpiBus, SpiConfig

from bitweaver.asm import load_program
from bitweaver.proto import i2c_read_cmds, i2c_result, i2c_timing, i2c_write_cmds, spi_frame
from bwtb import TB
from i2c_bfm import FAST, I2cMonitor, I2cTarget, Line
from spi_models import StreamTarget


@cocotb.test()
async def test_two_protocols_concurrently(dut):
    tb = TB(dut)
    await tb.start()
    # Pins: SPI on PIN11..14 (CS, MOSI, MISO, SCK), I2C SCL PIN15 / SDA PIN16.
    await tb.load(load_program("spi_ctrl_cpha0", file="spi"), 0, clkdiv=3)
    i2c_div, i2c_defines = i2c_timing(400e3)
    await tb.load(load_program("i2c_ctrl", **i2c_defines), 1, clkdiv=i2c_div, in_base=16, out_base=16, set_base=16,
                  side_base=15, dirs_pins=[15, 16], high_pins=[15, 16], od_pins=[15, 16])
    assert tb.origin == 11 + 25

    spi_target = StreamTarget(SpiBus(entity=dut, sclk_name="pad14", mosi_name="pad12", miso_name="ext13",
                                     cs_name="pad11"), SpiConfig(cpol=False, cpha=False, sclk_freq=None),
                              replies=[0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90])
    scl, sda = Line(dut.pad15, dut.ext15), Line(dut.pad16, dut.ext16)
    i2c_mon = I2cMonitor(scl, sda, FAST)
    i2c_target = I2cTarget(scl, sda, 0x3C, replies=[0xC0, 0xFE])
    await tb.enable(0b11)

    async def spi():
        got = []
        for frame in (b"\x9f\x01\x02\x03\x04\x05", b"\x0b\xaa\x55"):
            await tb.push_all(0, spi_frame(frame))
            got.append(bytes(await tb.collect(0, len(frame))))
        return got

    async def i2c():
        words = i2c_write_cmds(0x3C, [0x00, 0xAF], stop=False) + i2c_read_cmds(0x3C, 2)
        pusher = cocotb.start_soon(tb.push_all(1, words))
        res = [i2c_result(w) for w in await tb.collect(1, len(words))]
        await pusher
        return res

    spi_task, i2c_task = cocotb.start_soon(spi()), cocotb.start_soon(i2c())
    spi_got, i2c_res = await spi_task, await i2c_task
    await Timer(5, "us")

    assert spi_got == [bytes([0x10, 0x20, 0x30, 0x40, 0x50, 0x60]), bytes([0x70, 0x80, 0x90])]
    assert spi_target.frames == [b"\x9f\x01\x02\x03\x04\x05", b"\x0b\xaa\x55"]
    assert [b for b, _ in i2c_res[-2:]] == [0xC0, 0xFE]
    assert [a for _, a in i2c_res] == [True, True, True, True, True, False]
    assert i2c_target.written == [[0x00, 0xAF]]
    assert not i2c_mon.errors, i2c_mon.errors
    tb.done()
