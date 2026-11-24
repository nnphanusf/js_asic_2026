# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""CAN 2.0A: sw/programs/can.bw against an independent CAN node (test/lib/can_model.py).

The bit-level assist unit stuffs and destuffs, and computes the CRC15 on transmit. Frames with long
runs of equal bits (identifier 0, all-zero and all-one data) exercise stuffing hardest, and two
frames need a stuff bit after the last CRC bit.
Lock-step against the reference model stays on throughout.
"""

import cocotb
from cocotb.triggers import Timer

from bitweaver import regmap as R
from bitweaver.asm import load_program
from bitweaver.proto import can_rx_decode, can_tx_words
from bwtb import TB
from can_model import CanBus, CanNode

# The last two end their CRC with a run of five equal bits (ones, then zeros), so a stuff bit
# follows the CRC.
FRAMES = [(0x123, b"\x11\x22\x33"), (0x7FF, b""), (0x000, bytes(8)), (0x555, b"\xff" * 8),
          (0x0F0, b"\x0f\xf0"), (0x400, b"\x80"), (0x033, b"\x01"), (0x02B, b"\x01")]


async def wait_for(cond, us, step_us=20):
    for _ in range(int(us / step_us)):
        if cond():
            return
        await Timer(step_us, "us")
    assert cond(), "timed out"


@cocotb.test()
async def test_can_tx(dut):
    """The design transmits; the model node receives, checks stuffing and CRC, and acknowledges."""
    tb = TB(dut)
    await tb.start()
    bus = CanBus(dut.pad11, dut.ext12)
    for bitrate, clkdiv in ((500e3, 12), (1e6, 6)):
        node = CanNode(bus, bitrate)
        await tb.load(load_program("can_tx", file="can"), 0, origin=0, clkdiv=clkdiv)
        await tb.enable(1)
        for n, (ident, data) in enumerate(FRAMES):
            await tb.push_all(0, can_tx_words(ident, data))
            await wait_for(lambda: len(node.received) > n, 400)
        assert node.received == [(i, d, True, True, True) for i, d in FRAMES], node.received
        assert await tb.read1(R.addr("IRQ")) == 0
        # Nobody acknowledges: the program raises IRQ flag 0.
        node.ack = False
        await tb.push_all(0, can_tx_words(0x321, b"\x01"))
        await wait_for(lambda: len(node.received) > len(FRAMES), 400)
        await Timer(40, "us")
        assert await tb.read1(R.addr("IRQ")) == 0x01
        await tb.write(R.addr("IRQ"), [0x01])
        await tb.disable(1)
        node.ack = True
    tb.done()


@cocotb.test()
async def test_can_rx(dut):
    """The model node transmits; the design destuffs and the host checks every frame's CRC."""
    tb = TB(dut)
    await tb.start()
    bus = CanBus(dut.pad11, dut.ext12)
    node = CanNode(bus, 500e3, ack=False)
    await tb.load(load_program("can_rx", file="can"), 1, clkdiv=12)
    await tb.enable(2)
    for ident, data in FRAMES:
        await node.send(ident, data)
        await Timer(10, "us")
        n = await tb.rx_level(1)
        words = await tb.pop(1, n)
        assert can_rx_decode(words) == (ident, data, True), (ident, data, [hex(w) for w in words])
    tb.done()
