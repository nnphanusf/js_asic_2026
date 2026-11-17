"""Arm SWD target model (SW-DP, ADIv5), written from the Arm Debug Interface specification.

It sees only SWCLK and SWDIO. It implements line reset, the request/turnaround/ACK/data/parity phases,
DPIDR, CTRL/STAT with the power-up request/acknowledge handshake, SELECT and RDBUFF, and an AP whose
registers read back what was written. A request with a bad parity, start, stop or park bit gets no
response (the line stays high, so the host reads ACK 0b111).
"""

from __future__ import annotations

import cocotb
from cocotb.triggers import RisingEdge

DPIDR = 0x2BA01477


class SwdTarget:
    def __init__(self, swclk, swdio_pad, swdio_drive):
        self.clk, self.io, self.drv = swclk, swdio_pad, swdio_drive
        self.drv.value = 1
        self.ctrl_stat = 0
        self.select = 0
        self.ap = {}
        self.rdbuff = 0
        self.line_resets = 0
        self.log: list[tuple] = []
        self.errors: list[str] = []
        self._ones = 0
        self._reset_seen = False
        self._task = cocotb.start_soon(self._run())

    async def _edge(self) -> int:
        await RisingEdge(self.clk)
        b = int(self.io.value)
        if b:
            self._ones += 1
        else:
            if self._ones >= 50:
                self.line_resets += 1
                self._reset_seen = True
            self._ones = 0
        return b

    def _release(self):
        self.drv.value = 1

    async def _run(self):
        while True:
            b = await self._edge()
            if not (b and self._reset_seen and self._ones == 1):
                continue  # idle, or not yet out of line reset
            req = [1] + [await self._edge() for _ in range(7)]
            apndp, rnw, a2, a3, par, stop, park = req[1:]
            if par != (apndp + rnw + a2 + a3) & 1 or stop != 0 or park != 1:
                self.log.append(("bad request", req))
                continue
            addr = (a3 << 3) | (a2 << 2)
            await self._edge()  # turnaround: neither side drives
            ack = [1, 0, 0]  # OK, LSB first
            if rnw:
                value = self._read(apndp, addr)
                bits = ack + [(value >> i) & 1 for i in range(32)] + [bin(value).count("1") & 1]
                for bit in bits:  # driven after each rising edge, sampled by the host at the next
                    self.drv.value = bit
                    await self._edge()
                self._release()  # turnaround back to the host
                self.log.append(("read", apndp, addr, value))
            else:
                for bit in ack:
                    self.drv.value = bit
                    await self._edge()
                self._release()
                await self._edge()  # turnaround
                data = [await self._edge() for _ in range(32)]
                parity = await self._edge()
                value = sum(b << i for i, b in enumerate(data))
                if parity != bin(value).count("1") & 1:
                    self.errors.append(f"write parity error, value {value:#x}")
                self._write(apndp, addr, value)
                self.log.append(("write", apndp, addr, value))
            self._ones = 0

    def _read(self, apndp, addr) -> int:
        if apndp:
            v = self.rdbuff
            self.rdbuff = self.ap.get((self.select >> 4 & 0xF, addr), 0)  # AP reads are posted
            return v
        if addr == 0x0:
            return DPIDR
        if addr == 0x4:
            return self.ctrl_stat
        if addr == 0xC:
            return self.rdbuff
        return 0

    def _write(self, apndp, addr, value):
        if apndp:
            self.ap[(self.select >> 4 & 0xF, addr)] = value
        elif addr == 0x4:
            # CDBGPWRUPREQ (28) and CSYSPWRUPREQ (30) are acknowledged in bits 29 and 31.
            self.ctrl_stat = value | ((value & (1 << 28)) << 1) | ((value & (1 << 30)) << 1)
        elif addr == 0x8:
            self.select = value
