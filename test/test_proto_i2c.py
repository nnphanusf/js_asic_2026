# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""I2C: sw/programs/i2c.bw against the independent models in test/lib/i2c_bfm.py.

The bus monitor checks every standard-mode / fast-mode timing limit on every transfer. Lock-step
against the reference model stays on throughout.
"""

import cocotb
from cocotb.triggers import Timer

from bitweaver import regmap as R
from bitweaver.asm import load_program
from bitweaver.proto import i2c_clkdiv, i2c_read_cmds, i2c_result, i2c_write_cmds
from bwtb import TB
from i2c_bfm import FAST, STANDARD, I2cController, I2cMonitor, I2cTarget, Line

SCL, SDA = 13, 14


def lines(dut):
    return Line(dut.pad13, dut.ext13), Line(dut.pad14, dut.ext14)


async def run_cmds(tb, sm, words):
    """Stream command words through the 4-deep FIFOs; return the (byte, acked) results."""
    out = []
    for i in range(0, len(words), 4):
        chunk = words[i:i + 4]
        await tb.push(sm, chunk)
        await tb.wait_rx(sm, len(chunk), timeout_cycles=400000, poll=400)
        out += [i2c_result(w) for w in await tb.pop(sm, len(chunk))]
    return out


@cocotb.test()
async def test_i2c_controller(dut):
    """Writes, reads, repeated START, NACK and target clock stretching at 100 kHz and 400 kHz."""
    tb = TB(dut)
    await tb.start()
    scl, sda = lines(dut)
    await tb.load(load_program("i2c_ctrl"), 0)
    for timing in (STANDARD, FAST):
        mon = I2cMonitor(scl, sda, timing)
        target = I2cTarget(scl, sda, 0x50, replies=[0x11, 0x22, 0x33, 0x44, 0x55])
        await tb.write(R.addr("CLKDIV_INT", 0), [i2c_clkdiv(timing.f_scl)])
        await tb.enable(1)
        # Write three bytes.
        res = await run_cmds(tb, 0, i2c_write_cmds(0x50, [0x10, 0xAB, 0xCD]))
        assert all(ack for _, ack in res) and [b for b, _ in res] == [0xA0, 0x10, 0xAB, 0xCD]
        # Write a register address, then read four bytes after a repeated START.
        res = await run_cmds(tb, 0, i2c_write_cmds(0x50, [0x20], stop=False) + i2c_read_cmds(0x50, 4))
        assert [b for b, _ in res[3:]] == [0x11, 0x22, 0x33, 0x44]
        assert [a for _, a in res] == [True, True, True, True, True, True, False]
        # Nobody answers address 0x51.
        res = await run_cmds(tb, 0, i2c_write_cmds(0x51, []))
        assert res == [(0xA2, False)]
        # Clock stretching by the target (30 us after every byte).
        target.stretch_ns = 30000
        res = await run_cmds(tb, 0, i2c_write_cmds(0x50, [0x5A, 0xA5]))
        assert all(ack for _, ack in res)
        target.stretch_ns = 0
        await Timer(20, "us")
        assert target.written == [[0x10, 0xAB, 0xCD], [0x20], [0x5A, 0xA5]], target.written
        dut._log.info(f"{timing.name} mode bus events: {mon.events}")
        assert mon.events == ["S", ("byte", 0xA0, 0), ("byte", 0x10, 0), ("byte", 0xAB, 0), ("byte", 0xCD, 0), "P",
                              "S", ("byte", 0xA0, 0), ("byte", 0x20, 0), "Sr", ("byte", 0xA1, 0),
                              ("byte", 0x11, 0), ("byte", 0x22, 0), ("byte", 0x33, 0), ("byte", 0x44, 1), "P",
                              "S", ("byte", 0xA2, 1), "P",
                              "S", ("byte", 0xA0, 0), ("byte", 0x5A, 0), ("byte", 0xA5, 0), "P"], mon.events
        assert not mon.errors, (timing.name, mon.errors)
        mon.stop()
        target.stop()
        await tb.disable(1)
    tb.done()


@cocotb.test()
async def test_i2c_controller_stretch_timeout(dut):
    """A target that never releases SCL makes the controller raise IRQ flag 0 instead of hanging."""
    tb = TB(dut)
    await tb.start()
    scl, sda = lines(dut)
    await tb.load(load_program("i2c_ctrl"), 0, clkdiv=i2c_clkdiv(400e3), tmo=300)
    await tb.enable(1)
    target = I2cTarget(scl, sda, 0x50)
    target.hold_scl_forever = True
    await tb.push(0, i2c_write_cmds(0x50, [0x01]))
    await tb.cycles_(20000)
    assert await tb.read1(R.addr("IRQ")) == 0x01
    target.stop()
    tb.done()


@cocotb.test()
async def test_i2c_target(dut):
    """The two-engine target (watcher on SM2, target on SM3) against an independent controller."""
    tb = TB(dut)
    await tb.start()
    scl, sda = lines(dut)
    await tb.load(load_program("i2c_cond"), 2)
    await tb.load(load_program("i2c_target"), 3)
    await tb.enable(0b1100)
    for timing in (STANDARD, FAST):
        mon = I2cMonitor(scl, sda, timing)
        ctrl = I2cController(scl, sda, timing)
        # Write: the target ACKs and hands the bytes to the host.
        assert await ctrl.write(0x42, [0x01, 0x80, 0xFF])
        await tb.wait_rx(3, 3, timeout_cycles=20000)
        assert [w & 0xFF for w in await tb.pop(3, 3)] == [0x01, 0x80, 0xFF]
        # Read: bytes come from the TX FIFO.
        await tb.push(3, [0xDE, 0xAD])
        assert await ctrl.read(0x42, 2) == [0xDE, 0xAD]
        # Another address: no ACK, nothing reaches the host.
        assert not await ctrl.write(0x43, [0x55])
        # Write then read with a repeated START; the read stretches SCL until the host supplies data.
        assert await ctrl.write(0x42, [0x07], stop=False)
        reader = cocotb.start_soon(ctrl.read(0x42, 1))

        await Timer(40, "us")
        assert not reader.done()
        await tb.push(3, [0x99])
        got = await reader
        dut._log.info(f"{timing.name}: stretched read returned {got}, bus events {mon.events}")
        assert got == [0x99], timing.name
        await tb.wait_rx(3, 1, timeout_cycles=20000)
        assert [w & 0xFF for w in await tb.pop(3, 1)] == [0x07]
        assert await tb.rx_level(3) == 0
        assert not mon.errors, (timing.name, mon.errors)
        assert mon.events.count("Sr") == 1 and mon.events.count("P") == 4
        mon.stop()
    tb.done()
