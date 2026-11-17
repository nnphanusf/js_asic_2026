# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""SWD and PS/2 against independent models.

* SWD: swd.bw drives an Arm SW-DP model (test/lib/swd_model.py): line reset, DPIDR, CTRL/STAT
  power-up handshake, SELECT, posted AP reads, RDBUFF, and a malformed request that gets no answer.
* PS/2: a keyboard model sends scan codes; the receiver uses the input glitch filter (CLK carries
  short glitches) and the WAIT timeout (a frame cut short is dropped, the next one still decodes).
Lock-step against the reference model stays on throughout.
"""

import cocotb
from cocotb.triggers import Timer

from bitweaver.asm import load_program
from bitweaver.proto import SwdSeq, ps2_decode
from bwtb import TB
from swd_model import DPIDR, SwdTarget


@cocotb.test()
async def test_swd(dut):
    tb = TB(dut)
    await tb.start()
    target = SwdTarget(dut.pad11, dut.pad12, dut.ext12)
    await tb.load(load_program("swd"), 0, clkdiv=4)
    await tb.enable(1)
    seq = SwdSeq()
    seq.jtag_to_swd()
    seq.read(0, 0x0)  # DPIDR
    seq.write(0, 0x4, 0x5000_0000)  # CDBGPWRUPREQ | CSYSPWRUPREQ
    seq.read(0, 0x4)
    seq.write(0, 0x8, 0x0000_00F0)  # SELECT AP bank 0xF
    seq.write(1, 0xC, 0x1234_5678)
    seq.read(1, 0xC)  # posted: returns the previous AP read result
    seq.read(0, 0xC)  # RDBUFF: the AP register
    seq.read(0, 0x0, request=0b1010_0101 ^ 0b0010_0000)  # DPIDR request with a wrong parity bit
    seq.read(0, 0x0)  # the target still answers afterwards
    pusher = cocotb.start_soon(tb.push_all(0, seq.words))
    rx = await tb.collect(0, seq.n_results(), timeout_cycles=600000)
    await pusher
    res = seq.decode(rx)
    OK = SwdSeq.OK
    assert res[0] == ("read", OK, DPIDR, True), res[0]
    assert res[1] == ("write", OK)
    assert res[2] == ("read", OK, 0xF000_0000, True)
    assert res[3] == ("write", OK) and res[4] == ("write", OK)
    assert res[5][:2] == ("read", OK) and res[5][3]
    assert res[6] == ("read", OK, 0x1234_5678, True)
    assert res[7][1] == 0b111  # no response: the line floats high
    assert res[8] == ("read", OK, DPIDR, True)
    assert target.line_resets == 2 and not target.errors
    tb.done()


class Ps2Keyboard:
    """PS/2 device: drives CLK and DATA open drain at 12.5 kHz, data changes while CLK is high."""

    def __init__(self, clk, data, half_us=40):
        self.clk, self.data, self.half = clk, data, half_us
        clk.value = 1
        data.value = 1

    async def send(self, byte: int, glitch: bool = False, stop_after: int | None = None):
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [(bin(byte).count("1") + 1) & 1, 1]
        for i, b in enumerate(bits):
            if stop_after is not None and i == stop_after:
                break
            self.data.value = b
            await Timer(self.half // 2, "us")
            if glitch:  # a 20 ns spike low on CLK in the middle of the high phase
                self.clk.value = 0
                await Timer(20, "ns")
                self.clk.value = 1
            await Timer(self.half - self.half // 2, "us")
            self.clk.value = 0
            await Timer(self.half, "us")
            self.clk.value = 1
        self.data.value = 1
        await Timer(self.half * 2, "us")


@cocotb.test()
async def test_ps2(dut):
    tb = TB(dut)
    await tb.start()
    kb = Ps2Keyboard(dut.ext16, dut.ext15)
    await tb.load(load_program("ps2_rx", file="ps2"), 1)
    await tb.enable(2)
    codes = [0x1C, 0xF0, 0x1C, 0x5A, 0xE0, 0x75, 0xE0, 0xF0, 0x75]  # A down/up, Enter, up-arrow down/up
    collector = cocotb.start_soon(tb.collect(1, len(codes), timeout_cycles=400000))
    for c in codes[:3]:
        await kb.send(c)
    await kb.send(0x29, stop_after=5)  # the device gives up mid-frame
    await Timer(400, "us")  # longer than the watchdog
    for c in codes[3:]:
        await kb.send(c, glitch=True)
    got = [ps2_decode(w) for w in await collector]
    assert got == [(c, True) for c in codes], got
    assert await tb.rx_level(1) == 0
    tb.done()
