"""A 1-Wire slave device for cocotb, written from the Maxim 1-Wire timing specification.

It answers a reset with a presence pulse, then understands READ ROM (0x33), SKIP ROM (0xCC),
WRITE SCRATCHPAD (0x4E, two bytes) and READ SCRATCHPAD (0xBE). It samples written bits 30 us after
the master's falling edge and sends a 0 by holding the line low for 30 us from that edge.
"""

from __future__ import annotations

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time


def crc8(data):
    crc = 0
    for byte in data:
        for i in range(8):
            mix = (crc ^ (byte >> i)) & 1
            crc >>= 1
            if mix:
                crc ^= 0x8C
    return crc


class OneWireDevice:
    def __init__(self, pad, drive, serial=0x0000_1234_5678):
        self.pad, self.drv = pad, drive
        body = bytes([0x28]) + serial.to_bytes(6, "little")
        self.rom = body + bytes([crc8(body)])
        self.scratch = bytearray(2)
        self.connected = True
        self.resets = 0
        self.log: list = []
        self.drv.value = 1
        self._tx: list[int] = []
        self._rx: list[int] = []
        self._state = "idle"
        cocotb.start_soon(self._run())

    def _send(self, data):
        for byte in data:
            self._tx += [(byte >> i) & 1 for i in range(8)]

    def _on_byte(self, b):
        self.log.append(b)
        st = self._state
        if st == "rom":
            if b == 0x33:
                self._send(self.rom)
                self._state = "idle"
            elif b == 0xCC:
                self._state = "func"
        elif st == "func":
            if b == 0x4E:
                self._state = "wr0"
            elif b == 0xBE:
                self._send(self.scratch)
                self._state = "idle"
        elif st == "wr0":
            self.scratch[0] = b
            self._state = "wr1"
        elif st == "wr1":
            self.scratch[1] = b
            self._state = "idle"

    async def _run(self):
        while True:
            await FallingEdge(self.pad)
            t0 = get_sim_time("us")
            if not self.connected:
                continue
            if self._tx:
                bit = self._tx.pop(0)
                if bit == 0:
                    self.drv.value = 0
                    await Timer(30, "us")
                    self.drv.value = 1
            else:
                await Timer(30, "us")
                self._rx.append(int(self.pad.value))
            if not int(self.pad.value):
                await RisingEdge(self.pad)
            if get_sim_time("us") - t0 > 400:  # a reset pulse
                self.resets += 1
                self._tx, self._rx = [], []
                await Timer(30, "us")
                self.drv.value = 0  # presence pulse
                await Timer(120, "us")
                self.drv.value = 1
                self._state = "rom"
                continue
            if len(self._rx) == 8:
                self._on_byte(sum(b << i for i, b in enumerate(self._rx)))
                self._rx = []
