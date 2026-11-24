"""Low-speed USB line models for cocotb, written from the USB 2.0 specification (chapters 7 and 8).

Independent of bitweaver.proto: the CRCs are computed the way the specification describes them
(MSb-first shift register over the bits in transmission order, generator x^16+x^15+x^2+1 or
x^5+x^2+1, initial all ones, the inverted remainder sent MSb first).
"""

from __future__ import annotations

from cocotb.triggers import FallingEdge, ReadOnly, Timer

PID = {"OUT": 0x1, "IN": 0x9, "SOF": 0x5, "SETUP": 0xD, "DATA0": 0x3, "DATA1": 0xB, "ACK": 0x2,
       "NAK": 0xA, "STALL": 0xE}


def crc_bits(bits, poly, width):
    reg = (1 << width) - 1
    top = 1 << (width - 1)
    for b in bits:
        fb = b ^ (1 if reg & top else 0)
        reg = (reg << 1) & ((1 << width) - 1)
        if fb:
            reg ^= poly
    reg ^= (1 << width) - 1
    return [(reg >> i) & 1 for i in range(width - 1, -1, -1)]  # MSb first


def lsb_bits(value, n):
    return [(value >> i) & 1 for i in range(n)]


def packet_bits(pid: str, data=None, addr=None, endp=0) -> list[int]:
    p = PID[pid]
    bits = lsb_bits(0x80, 8) + lsb_bits(p | ((~p & 0xF) << 4), 8)
    if addr is not None:
        field = lsb_bits(addr, 7) + lsb_bits(endp, 4)
        bits += field + crc_bits(field, 0x05, 5)
    if data is not None:
        payload = [b for byte in bytes(data) for b in lsb_bits(byte, 8)]
        bits += payload + crc_bits(payload, 0x8005, 16)
    return bits


def stuff(bits):
    out, ones = [], 0
    for b in bits:
        out.append(b)
        ones = ones + 1 if b else 0
        if ones == 6:
            out.append(0)
            ones = 0
    return out


class UsbLines:
    """D- and D+ on the design's pads; the model drives them through the tb ext signals."""

    J, K, SE0 = (1, 0), (0, 1), (0, 0)

    def __init__(self, dut, dm=11, dp=12, bit_ps=40 * 16666):
        self.dm_pad, self.dp_pad = getattr(dut, f"pad{dm}"), getattr(dut, f"pad{dp}")
        self.dm_drv, self.dp_drv = getattr(dut, f"ext{dm}"), getattr(dut, f"ext{dp}")
        self.bit_ps = bit_ps
        self.release()

    def release(self):
        self.dm_drv.value, self.dp_drv.value = 1, 0  # pull-up on D- (low-speed device), pull-down on D+

    def state(self):
        return int(self.dm_pad.value), int(self.dp_pad.value)

    async def send(self, bits):
        """Drive a packet: stuffing, NRZI from J, then SE0 SE0 J and release."""
        level = 1  # 1 = J
        for b in stuff(bits):
            if b == 0:
                level ^= 1
            self.dm_drv.value, self.dp_drv.value = (1, 0) if level else (0, 1)
            await Timer(self.bit_ps, "ps")
        for st in (self.SE0, self.SE0, self.J):
            self.dm_drv.value, self.dp_drv.value = st
            await Timer(self.bit_ps, "ps")
        self.release()

    async def receive(self) -> dict:
        """Wait for a packet on the lines and decode it, checking stuffing and the EOP."""
        while True:
            await FallingEdge(self.dm_pad)  # first K of SYNC
            await ReadOnly()
            # Icarus can glitch D- low for zero time when the design releases the lines after an EOP
            # (the pad expression is evaluated piecewise); only a K that is really there counts.
            if self.state() == self.K:
                break
        await Timer(self.bit_ps // 2, "ps")
        raw, level = [], 1
        while True:
            st = self.state()
            if st == self.SE0:
                break
            new = 1 if st == self.J else 0
            raw.append(1 if new == level else 0)
            level = new
            await Timer(self.bit_ps, "ps")
        await Timer(self.bit_ps, "ps")
        eop_ok = self.state() == self.SE0
        await Timer(self.bit_ps, "ps")
        eop_ok = eop_ok and self.state() == self.J
        bits, ones, stuff_ok = [], 0, True
        for b in raw:
            if ones == 6:
                stuff_ok = stuff_ok and b == 0
                ones = 0
                continue
            bits.append(b)
            ones = ones + 1 if b else 0
        n = len(bits) // 8
        data = bytes(sum(bits[8 * i + j] << j for j in range(8)) for i in range(n))
        res = {"raw_bits": len(raw), "bits": bits, "bytes": data, "stuff_ok": stuff_ok, "eop_ok": eop_ok,
               "whole_bytes": len(bits) % 8 == 0}
        pid = data[1] & 0xF if n > 1 else None
        names = {v: k for k, v in PID.items()}
        res["pid"] = names.get(pid)
        res["pid_ok"] = n > 1 and data[0] == 0x80 and (data[1] >> 4) == (~data[1] & 0xF)
        body = bits[16:]
        if res["pid"] in ("DATA0", "DATA1"):
            res["payload"] = data[2:-2]
            res["crc_ok"] = crc_bits(body[:-16], 0x8005, 16) == body[-16:]
        elif res["pid"] in ("OUT", "IN", "SETUP", "SOF"):
            res["crc_ok"] = crc_bits(body[:11], 0x05, 5) == body[11:16]
            field = sum(b << i for i, b in enumerate(body[:11]))
            res["addr"], res["endp"] = field & 0x7F, field >> 7
        else:
            res["crc_ok"] = True
        return res
