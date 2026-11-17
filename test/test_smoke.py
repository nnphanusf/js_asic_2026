# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""Smoke tests: reset state, host port, register read-back. Every test runs in lock-step."""

import cocotb
from cocotb.triggers import ClockCycles

from bitweaver import regmap as R
from bwtb import TB


@cocotb.test()
async def test_idle_after_reset(dut):
    """Every output is low and every bidirectional pin is Hi-Z after reset."""
    tb = TB(dut)
    await tb.start()
    assert int(dut.uo_out.value) == 0
    assert int(dut.uio_out.value) == 0
    assert int(dut.uio_oe.value) == 0
    for pattern in (0x1F, 0x0A, 0x15, 0x00):
        for i in range(5):
            tb.set_in(i, (pattern >> i) & 1)
        for i in range(8):
            tb.set_ext(11 + i, (pattern >> (i % 5)) & 1)
        await ClockCycles(dut.clk, 5)
        assert int(dut.uo_out.value) == 0 and int(dut.uio_oe.value) == 0
    tb.done()


@cocotb.test()
async def test_id_version_and_status_byte(dut):
    tb = TB(dut)
    await tb.start()
    assert await tb.read(R.addr("ID"), 2, inc=True) == [R.CHIP_ID, R.CHIP_VERSION]
    assert tb.first_bytes == (R.SPI_ID_BYTE, 0x0F)
    tb.done()


@cocotb.test()
async def test_register_readback(dut):
    """Every engine configuration register resets to its documented value and keeps its writable bits."""
    tb = TB(dut)
    await tb.start()
    for k in range(R.NUM_SM):
        for r in R.SM_REGS:
            if r.name in ("FIFO", "EXEC", "STAT"):
                continue
            assert await tb.read1(R.addr(r.name, k)) == r.reset, (k, r.name)
            await tb.write(R.addr(r.name, k), [0xFFFF])
            assert await tb.read1(R.addr(r.name, k)) == r.mask, (k, r.name)
    for name, lo, hi in (("PIN_OUT", 0xFFE0, 7), ("PIN_OE", 0xFFE0, 7), ("PAD_OD", 0xF800, 7),
                         ("PAD_FILT", 0xF81F, 7)):
        await tb.write(R.addr(name + "_LO"), [0xFFFF, 0xFFFF], inc=True)
        assert await tb.read(R.addr(name + "_LO"), 2, inc=True) == [lo, hi], name
    for a in (0x1F, 0x2E, 0x60, 0x7F, 0xC0, 0xFF):
        assert await tb.read1(a) == 0
    tb.done()


@cocotb.test()
async def test_imem_write_and_readback(dut):
    tb = TB(dut)
    await tb.start()
    words = [(i * 0x9E37 + 0x1234) & 0xFFFF for i in range(R.IMEM_DEPTH)]
    await tb.write(R.IMEM_BASE, words, inc=True)
    assert await tb.read(R.IMEM_BASE, R.IMEM_DEPTH, inc=True) == words
    tb.done()


@cocotb.test()
async def test_reset_mid_frame(dut):
    """Releasing CS_N in the middle of a frame drops it; the next frame works normally."""
    tb = TB(dut)
    await tb.start()
    await tb.frame(bytes([0x80 | 0x40, R.IMEM_BASE])[:2])  # command and address, no data
    raw = bytes([0x80, R.addr("TS_PRESCALE"), 0x12])  # a frame cut after one data byte
    await tb.frame(raw)
    assert await tb.read1(R.addr("TS_PRESCALE")) == 0
    assert await tb.read1(R.addr("ID")) == R.CHIP_ID
    tb.done()
