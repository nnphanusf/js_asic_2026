# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""1-Wire: the program from docs/programming_guide.md against an independent 1-Wire device model.

Uses the open-drain pin mode and the WAIT timeout (presence detection). Lock-step stays on.
"""

import cocotb

from bitweaver.asm import load_program
from bwtb import F_CLK, TB
from onewire_model import OneWireDevice, crc8

RESET = 0x0000


def byte(b):
    return 0x100 | b


async def run(tb, words):
    pusher = cocotb.start_soon(tb.push_all(0, words))
    out = await tb.collect(0, len(words), timeout_cycles=2_000_000, poll=2000)
    await pusher
    return out


@cocotb.test()
async def test_onewire(dut):
    tb = TB(dut)
    await tb.start()
    dev = OneWireDevice(dut.pad17, dut.ext17)
    await tb.load(load_program("onewire", F_CLK=F_CLK), 0)
    await tb.enable(1)
    # READ ROM: family code, serial number and CRC.
    res = await run(tb, [RESET, byte(0x33)] + [byte(0xFF)] * 8)
    assert res[0] == 0, "no presence pulse"
    rom = bytes(w >> 8 for w in res[2:])
    assert rom == dev.rom and rom[0] == 0x28 and crc8(rom[:7]) == rom[7]
    # SKIP ROM, WRITE SCRATCHPAD, then read it back.
    await run(tb, [RESET, byte(0xCC), byte(0x4E), byte(0x12), byte(0xA5)])
    res = await run(tb, [RESET, byte(0xCC), byte(0xBE), byte(0xFF), byte(0xFF)])
    assert [w >> 8 for w in res[3:]] == [0x12, 0xA5]
    assert dev.resets == 3
    # Nobody on the bus: the WAIT timeout reports no presence.
    dev.connected = False
    assert await run(tb, [RESET]) == [1]
    tb.done()
