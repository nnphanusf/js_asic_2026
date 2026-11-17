# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""Four protocols at once: UART TX, UART RX, SPI controller and I2C controller on SM0..SM3.

Every engine runs its own program at its own clock divider, each bus is checked by its own
independent model, and all host traffic shares the single SPI host port. Lock-step stays on.
"""

import cocotb
from cocotb.triggers import Timer
from cocotbext.spi import SpiBus, SpiConfig
from cocotbext.uart import UartSink, UartSource

from bitweaver.asm import load_program
from bitweaver.proto import i2c_clkdiv, i2c_read_cmds, i2c_result, i2c_write_cmds, spi_frame, uart_clkdiv
from bwtb import TB
from i2c_bfm import FAST, I2cMonitor, I2cTarget, Line
from spi_models import StreamTarget


@cocotb.test()
async def test_four_protocols_concurrently(dut):
    tb = TB(dut)
    await tb.start()
    baud = 921600
    # Pins: UART TX PIN9, UART RX PIN3, SPI on PIN11..14 (CS, MOSI, MISO, SCK), I2C SCL PIN15 / SDA PIN16.
    await tb.load(load_program("uart_tx"), 0, clkdiv=uart_clkdiv(baud))
    await tb.load(load_program("uart_rx"), 1, clkdiv=uart_clkdiv(baud))
    await tb.load(load_program("spi_ctrl_cpha0", file="spi"), 2, clkdiv=3)
    await tb.load(load_program("i2c_ctrl"), 3, clkdiv=i2c_clkdiv(400e3), in_base=16, out_base=16, set_base=16,
                  side_base=15, dirs_pins=[15, 16], high_pins=[15, 16], od_pins=[15, 16])
    assert tb.origin == 4 + 11 + 11 + 25

    sink = UartSink(dut.pad9, baud=baud)
    source = UartSource(dut.in3, baud=baud)
    spi_target = StreamTarget(SpiBus(entity=dut, sclk_name="pad14", mosi_name="pad12", miso_name="ext13",
                                     cs_name="pad11"), SpiConfig(cpol=False, cpha=False, sclk_freq=None),
                              replies=[0x10, 0x20, 0x30, 0x40, 0x50, 0x60])
    scl, sda = Line(dut.pad15, dut.ext15), Line(dut.pad16, dut.ext16)
    i2c_mon = I2cMonitor(scl, sda, FAST)
    i2c_target = I2cTarget(scl, sda, 0x3C, replies=[0xC0, 0xFE])
    await tb.enable(0b1111)

    uart_out = b"Bitweaver says hi"
    uart_in = bytes([0x00, 0x7F, 0x80, 0xFF, 0x5A, 0xA5])

    async def uart_tx():
        await tb.push_all(0, uart_out)

    async def uart_rx():
        await source.write(uart_in)
        return bytes(await tb.collect(1, len(uart_in)))

    async def spi():
        await tb.push_all(2, spi_frame(b"\x9f\x01\x02\x03\x04\x05"))
        return bytes(await tb.collect(2, 6))

    async def i2c():
        words = i2c_write_cmds(0x3C, [0x00, 0xAF], stop=False) + i2c_read_cmds(0x3C, 2)
        pusher = cocotb.start_soon(tb.push_all(3, words))
        res = [i2c_result(w) for w in await tb.collect(3, len(words))]
        await pusher
        return res

    tasks = [cocotb.start_soon(c()) for c in (uart_tx, uart_rx, spi, i2c)]
    results = [await t for t in tasks]
    got_tx = bytearray()
    for _ in range(2000):
        if len(got_tx) == len(uart_out):
            break
        if sink.empty():
            await Timer(2, "us")
        else:
            got_tx += sink.read_nowait(1)
    await Timer(5, "us")

    assert bytes(got_tx) == uart_out
    assert results[1] == uart_in
    assert results[2] == bytes([0x10, 0x20, 0x30, 0x40, 0x50, 0x60])
    assert spi_target.frames == [b"\x9f\x01\x02\x03\x04\x05"]
    assert [b for b, _ in results[3][-2:]] == [0xC0, 0xFE]
    assert [a for _, a in results[3]] == [True, True, True, True, True, False]
    assert i2c_target.written == [[0x00, 0xAF]]
    assert not i2c_mon.errors, i2c_mon.errors
    tb.done()
