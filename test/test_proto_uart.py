# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""UART: sw/programs/uart.bw checked against the independent cocotbext-uart models.

The design's TX pin drives a cocotbext-uart UartSink and a UartSource drives its RX pin, so the
frames on the wire are judged by code that knows nothing about Bitweaver. The lock-step checker
still compares every pin with the reference model on every cycle.
"""

import cocotb
from cocotb.triggers import ClockCycles, Timer
from cocotbext.uart import UartSink, UartSource

from bitweaver import regmap as R
from bitweaver.asm import load_program
from bwtb import TB, GL

F_CLK = 60e6
TX_PIN, RX_PIN = 9, 3


def div_for(baud):
    return F_CLK / (8 * baud)


def parity_even(v):
    return bin(v).count("1") & 1


async def sink_read(sink, count, timeout_ns):
    out = []
    waited = 0
    while len(out) < count:
        if sink.empty():
            await Timer(1000, "ns")
            waited += 1000
            assert waited < timeout_ns, f"UART sink received only {out}"
        else:
            out.extend(sink.read_nowait(1))
    return out


@cocotb.test()
async def test_uart_tx_baud_rates(dut):
    """8N1 transmit at 9600, 115200 and 921600 baud."""
    tb = TB(dut)
    await tb.start()
    for baud, data in ((921600, b"\x55\x00\xffBW"), (115200, b"Hi!\x81"), (9600, b"\xa5\x3c")):
        sink = UartSink(dut.pad9, baud=baud, bits=8)
        await tb.load(load_program("uart_tx"), 0, origin=0, clkdiv=div_for(baud))
        await tb.enable(1)
        await tb.push(0, list(data))
        got = await sink_read(sink, len(data), timeout_ns=int(len(data) * 12e9 / baud) + 200000)
        assert bytes(got) == data, (baud, got)
        await tb.wait_tx_empty(0)
        await Timer(int(12e9 / baud), "ns")
        await tb.disable(1)
        sink._run_cr.cancel()
    tb.done()


@cocotb.test()
async def test_uart_tx_8e1(dut):
    """Even parity: the program sends 9 bits and the host supplies the parity bit."""
    tb = TB(dut)
    await tb.start()
    baud = 230400
    sink = UartSink(dut.pad9, baud=baud, bits=9)
    await tb.load(load_program("uart_tx", DATA_BITS=9), 0, clkdiv=div_for(baud))
    await tb.enable(1)
    data = [0x00, 0x01, 0x7F, 0xFF, 0xA5]
    await tb.push(0, [b | (parity_even(b) << 8) for b in data[:4]])
    await tb.push(0, [data[4] | (parity_even(data[4]) << 8)])
    got = await sink_read(sink, len(data), timeout_ns=int(len(data) * 13e9 / baud) + 400000)
    assert [g & 0xFF for g in got] == data
    assert all((g >> 8) == parity_even(g & 0xFF) for g in got)
    tb.done()


@cocotb.test()
async def test_uart_rx_baud_rates(dut):
    """8N1 receive at 9600, 115200 and 921600 baud from an independent UART source."""
    tb = TB(dut)
    await tb.start()
    for baud, data in ((921600, b"\x00\xff\x55\xaa\x0f"), (115200, b"rx!"), (9600, b"\x81\x7e")):
        src = UartSource(dut.in3, baud=baud, bits=8)
        await tb.load(load_program("uart_rx"), 1, origin=10, clkdiv=div_for(baud))
        await tb.enable(2)
        got = []
        for b in data:
            await src.write([b])
            await src.wait()
            await tb.wait_rx(1, 1, timeout_cycles=int(12 * F_CLK / baud) + 20000)
            got += await tb.pop(1)
        assert bytes(got) == data, (baud, got)
        await tb.disable(2)
        src._run_cr.cancel()
    assert await tb.read1(R.addr("IRQ")) == 0
    tb.done()


@cocotb.test()
async def test_uart_rx_8e1_and_framing_error(dut):
    tb = TB(dut)
    await tb.start()
    baud = 460800
    await tb.load(load_program("uart_rx", DATA_BITS=9), 1, clkdiv=div_for(baud))
    await tb.enable(2)
    src = UartSource(dut.in3, baud=baud, bits=9)
    data = [0x00, 0x80, 0x7F, 0x33]
    for b in data:
        await src.write([b | (parity_even(b) << 8)])
    await src.wait()
    await tb.wait_rx(1, 4, timeout_cycles=40000)
    got = await tb.pop(1, 4)
    assert [g & 0xFF for g in got] == data and all((g >> 8) == parity_even(g & 0xFF) for g in got)
    src._run_cr.cancel()
    # A frame with a low stop bit raises IRQ flag 4 relative to SM1 (flag 5) and is dropped.
    bit_ns = int(1e9 / baud)
    for level in [0] + [1, 0] * 4 + [1] + [0, 0]:  # start, 9 data bits, then a low "stop" bit
        dut.in3.value = level
        await Timer(bit_ns, "ns")
    dut.in3.value = 1
    await ClockCycles(dut.clk, 400)
    assert await tb.read1(R.addr("IRQ")) == 1 << 5
    assert await tb.rx_level(1) == 0
    tb.done()


@cocotb.test()
async def test_uart_full_duplex_loopback(dut):
    """TX on SM0 and RX on SM1 share PIN11 at 921600 baud; both run while the host streams data."""
    tb = TB(dut)
    await tb.start()
    div = div_for(921600)
    await tb.load(load_program("uart_tx"), 0, clkdiv=div, out_base=11, set_base=11, side_base=11)
    await tb.load(load_program("uart_rx"), 1, clkdiv=div, in_base=11, jmp_pin=11)
    await tb.enable(0b11)
    data = list(range(0, 256, 17))
    got = []
    for i in range(0, len(data), 4):
        await tb.push(0, data[i:i + 4])
        await tb.wait_rx(1, len(data[i:i + 4]), timeout_cycles=20000)
        got += await tb.pop(1, len(data[i:i + 4]))
    assert got == data
    tb.done()
