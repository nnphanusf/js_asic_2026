# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

CLK_PERIOD_PS = 16666  # ~60 MHz, matches info.yaml clock_hz (even ps so the clock splits evenly)


async def reset(dut):
    clock = Clock(dut.clk, CLK_PERIOD_PS, unit="ps")
    cocotb.start_soon(clock.start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


@cocotb.test()
async def test_idle_after_reset(dut):
    """Phase 0 skeleton: every output is low and every bidirectional pin is Hi-Z."""
    await reset(dut)
    assert dut.uo_out.value == 0
    assert dut.uio_out.value == 0
    assert dut.uio_oe.value == 0


@cocotb.test()
async def test_idle_ignores_inputs(dut):
    """Toggling the host and protocol inputs must not drive any pin yet."""
    await reset(dut)
    for pattern in (0xFF, 0xA5, 0x5A, 0x00):
        dut.ui_in.value = pattern
        dut.uio_in.value = pattern ^ 0xFF
        await ClockCycles(dut.clk, 3)
        assert dut.uo_out.value == 0
        assert dut.uio_oe.value == 0
