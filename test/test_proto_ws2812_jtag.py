# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""WS2812 and JTAG: protocols the engines were not designed around, in a few instructions each.

* WS2812: an independent decoder measures every high pulse against the WS2812B datasheet limits.
* JTAG: an IEEE 1149.1 TAP model (16-state controller, 4-bit IR, IDCODE, BYPASS and an 8-bit user
  register) answers the jtag.bw controller.
Lock-step against the reference model stays on throughout.
"""

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

from bitweaver.asm import load_program
from bitweaver.proto import JtagSeq, ws2812_words
from bwtb import TB


# ----------------------------------------------------------------------------- WS2812
class Ws2812Decoder:
    """Decodes a WS2812 data line and checks the pulse timing (WS2812B datasheet, in ns)."""
    T0H = (220, 380)
    T1H = (580, 1000)
    BIT = (1000, 1600)  # total bit period
    RESET = 50000

    def __init__(self, pin):
        self.pin = pin
        self.frames: list[list[int]] = []  # bits between resets
        self.errors: list[str] = []
        self._task = cocotb.start_soon(self._run())

    async def _run(self):
        bits: list[int] = []
        last_rise = None
        while True:
            await RisingEdge(self.pin)
            t = get_sim_time("ns")
            if last_rise is not None:
                period = t - last_rise
                if period >= self.RESET:
                    if bits:
                        self.frames.append(bits)
                    bits = []
                elif not self.BIT[0] <= period <= self.BIT[1]:
                    self.errors.append(f"bit period {period:.0f} ns at {t:.0f} ns")
            await FallingEdge(self.pin)
            high = get_sim_time("ns") - t
            if self.T0H[0] <= high <= self.T0H[1]:
                bits.append(0)
            elif self.T1H[0] <= high <= self.T1H[1]:
                bits.append(1)
            else:
                self.errors.append(f"high pulse {high:.0f} ns at {t:.0f} ns")
            last_rise = t

    def flush(self):
        """Call after the line has been low for the reset time."""


def pixels_from_bits(bits):
    out = []
    for i in range(0, len(bits) - 23, 24):
        v = [int("".join(map(str, bits[i + 8 * k:i + 8 * k + 8])), 2) for k in range(3)]
        out.append((v[1], v[0], v[2]))  # GRB on the wire
    return out


@cocotb.test()
async def test_ws2812(dut):
    tb = TB(dut)
    await tb.start()
    dec = Ws2812Decoder(dut.pad5)
    await tb.load(load_program("ws2812"), 0)
    await tb.enable(1)
    frames = [[(255, 0, 0), (0, 255, 0), (0, 0, 255), (0x12, 0x34, 0x56)],
              [(1, 2, 3), (0x80, 0x7F, 0xAA), (0, 0, 0), (255, 255, 255), (0x5A, 0xA5, 0x3C)]]
    for pixels in frames:
        await tb.push_all(0, ws2812_words(pixels))
        await tb.wait_tx_empty(0)
        await Timer(80, "us")  # the line stays low: the LEDs latch
    # Close the last frame with one more pulse so the decoder sees the reset gap.
    await tb.push_all(0, ws2812_words([(0, 0, 0)]))
    await Timer(40, "us")
    assert not dec.errors, dec.errors[:5]
    assert len(dec.frames) == 2
    for pixels, bits in zip(frames, dec.frames):
        assert len(bits) - 24 * len(pixels) in (0, 8)  # at most one padding byte of zeros
        assert pixels_from_bits(bits)[:len(pixels)] == pixels
        assert not any(bits[24 * len(pixels):])
    tb.done()


# ----------------------------------------------------------------------------- JTAG
TLR, RTI, SEL_DR, CAP_DR, SH_DR, EX1_DR, PAU_DR, EX2_DR, UPD_DR = range(9)
SEL_IR, CAP_IR, SH_IR, EX1_IR, PAU_IR, EX2_IR, UPD_IR = range(9, 16)
NEXT = {  # state: (next if TMS = 0, next if TMS = 1), IEEE 1149.1 figure 6-1
    TLR: (RTI, TLR), RTI: (RTI, SEL_DR), SEL_DR: (CAP_DR, SEL_IR), CAP_DR: (SH_DR, EX1_DR),
    SH_DR: (SH_DR, EX1_DR), EX1_DR: (PAU_DR, UPD_DR), PAU_DR: (PAU_DR, EX2_DR), EX2_DR: (SH_DR, UPD_DR),
    UPD_DR: (RTI, SEL_DR), SEL_IR: (CAP_IR, TLR), CAP_IR: (SH_IR, EX1_IR), SH_IR: (SH_IR, EX1_IR),
    EX1_IR: (PAU_IR, UPD_IR), PAU_IR: (PAU_IR, EX2_IR), EX2_IR: (SH_IR, UPD_IR), UPD_IR: (RTI, SEL_DR),
}
IR_IDCODE, IR_USER, IR_BYPASS = 0b0001, 0b1000, 0b1111
IDCODE = 0x4BA00477


class TapModel:
    def __init__(self, tck, tms, tdi, tdo):
        self.tck, self.tms, self.tdi, self.tdo = tck, tms, tdi, tdo
        self.state = TLR
        self.ir = IR_IDCODE
        self.user = 0
        self.sr = 0
        self.sr_len = 0
        self.visited = set()
        self.tdo.value = 1
        cocotb.start_soon(self._run())

    def _dr_len(self):
        return {IR_IDCODE: 32, IR_USER: 8}.get(self.ir, 1)

    async def _run(self):
        while True:
            await RisingEdge(self.tck)
            tms, tdi = int(self.tms.value), int(self.tdi.value)
            s = self.state
            if s in (SH_DR, SH_IR):  # shift on the rising edge while in Shift
                self.sr = (self.sr >> 1) | (tdi << (self.sr_len - 1))
            self.state = NEXT[s][tms]
            self.visited.add(self.state)
            n = self.state
            if n == TLR:
                self.ir = IR_IDCODE
            elif n == CAP_IR:
                self.sr, self.sr_len = 0b0001, 4
            elif n == CAP_DR:
                self.sr_len = self._dr_len()
                self.sr = {IR_IDCODE: IDCODE, IR_USER: self.user}.get(self.ir, 0)
            elif n == UPD_IR:
                self.ir = self.sr & 0xF
            elif n == UPD_DR and self.ir == IR_USER:
                self.user = self.sr & 0xFF
            await FallingEdge(self.tck)  # TDO changes on the falling edge
            self.tdo.value = self.sr & 1 if self.state in (SH_DR, SH_IR) else 1


@cocotb.test()
async def test_jtag(dut):
    tb = TB(dut)
    await tb.start()
    tap = TapModel(dut.pad5, dut.pad7, dut.pad6, dut.in0)
    for clkdiv in (2, 9):
        await tb.load(load_program("jtag"), 0, origin=0, clkdiv=clkdiv)
        await tb.enable(1)
        seq = JtagSeq()
        seq.reset()
        seq.shift_dr(0, 32)  # IDCODE is selected after reset
        seq.shift_ir(IR_USER, 4)
        seq.shift_dr(0xA5, 8)  # write the user register
        seq.shift_dr(0x3C, 8)  # read it back while writing a new value
        seq.shift_ir(IR_BYPASS, 4)
        seq.shift_dr(0b1011001, 7)  # BYPASS: one-bit delay
        words = seq.words()
        pusher = cocotb.start_soon(tb.push_all(0, words))
        rx = await tb.collect(0, len(words))
        await pusher
        idcode, ir_cap1, w1, rd, ir_cap2, byp = seq.decode(rx)
        assert idcode == IDCODE, hex(idcode)
        assert ir_cap1 & 0b11 == 0b01 and ir_cap2 & 0b11 == 0b01  # IR capture value
        assert rd == 0xA5 and tap.user == 0x3C
        assert byp == (0b1011001 << 1) & 0x7F  # shifted by one bit through BYPASS
        await tb.disable(1)
    assert len(tap.visited) == 12  # every state except the Pause/Exit2 detours
    tb.done()
