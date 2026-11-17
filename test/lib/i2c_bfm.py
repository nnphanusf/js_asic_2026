"""I2C bus models for cocotb, written from the I2C-bus specification (NXP UM10204).

These models know nothing about Bitweaver's programs: they see only the two open-drain lines.

* `I2cMonitor` decodes START, bytes, ACKs and STOP and checks the timing limits of standard mode
  (100 kHz) or fast mode (400 kHz).
* `I2cTarget` is a target device with a 7-bit address and optional clock stretching.
* `I2cController` is a controller that generates the bus timing itself and honours clock stretching.

Each model drives a line by writing 0 (pull low) or 1 (release) to an `ext` signal of the testbench
and reads the resolved line level from the matching `pad` signal.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import cocotb
from cocotb.triggers import FallingEdge, First, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time


@dataclass
class I2cTiming:
    name: str
    f_scl: float
    t_low: float  # all times in ns
    t_high: float
    t_su_sta: float
    t_hd_sta: float
    t_su_sto: float
    t_buf: float
    t_su_dat: float


STANDARD = I2cTiming("standard", 100e3, 4700, 4000, 4700, 4000, 4000, 4700, 250)
FAST = I2cTiming("fast", 400e3, 1300, 600, 600, 600, 600, 1300, 100)


def now() -> float:
    return get_sim_time("ns")


class Line:
    def __init__(self, pad, ext):
        self.pad, self.ext = pad, ext

    @property
    def level(self) -> int:
        return int(self.pad.value)

    def drive(self, level: int):
        self.ext.value = level


class I2cMonitor:
    """Watches SCL and SDA, records bus events and every timing violation."""

    def __init__(self, scl: Line, sda: Line, timing: I2cTiming, tol_ns: float = 1.0):
        self.scl, self.sda, self.t = scl, sda, timing
        self.tol = tol_ns
        self.events: list = []  # "S", "Sr", "P", ("byte", value, ack)
        self.errors: list[str] = []
        self.in_frame = False
        self._bits: list[int] = []
        self._bit_at_rise = 1
        self._scl_rise = self._scl_fall = self._sda_change = self._stop = self._start = None
        self._first_fall_after_start = False
        self._task = cocotb.start_soon(self._run())

    def stop(self):
        self._task.cancel()

    def _check(self, what: str, value: float, minimum: float):
        if value + self.tol < minimum:
            self.errors.append(f"{what} = {value:.0f} ns < {minimum:.0f} ns at {now():.0f} ns")

    async def _run(self):
        scl, sda = self.scl.pad, self.sda.pad
        p_scl, p_sda = int(scl.value), int(sda.value)
        while True:
            await First(scl.value_change, sda.value_change)
            await ReadOnly()
            c_scl, c_sda = int(scl.value), int(sda.value)
            t = now()
            if c_scl != p_scl:
                self._on_scl(c_scl, p_sda, t)
            if c_sda != p_sda:
                self._on_sda(c_scl, c_sda, t)
            p_scl, p_sda = c_scl, c_sda

    def _on_scl(self, s_scl, s_sda, t):
        if s_scl:  # rising
            if self.in_frame and self._scl_fall is not None:
                self._check("tLOW", t - self._scl_fall, self.t.t_low)
            if self.in_frame and self._sda_change is not None and self._scl_fall is not None \
                    and self._sda_change > self._scl_fall:
                self._check("tSU;DAT", t - self._sda_change, self.t.t_su_dat)
            self._scl_rise = t
        else:  # falling
            if self.in_frame:
                if self._scl_rise is not None:
                    self._check("tHIGH", t - self._scl_rise, self.t.t_high)
                if self._first_fall_after_start:
                    self._check("tHD;STA", t - self._start, self.t.t_hd_sta)
                    self._first_fall_after_start = False
                else:
                    self._bits.append(self._bit_at_rise)
                    if len(self._bits) == 9:
                        v = 0
                        for b in self._bits[:8]:
                            v = (v << 1) | b
                        self.events.append(("byte", v, self._bits[8]))
                        self._bits = []
            self._scl_fall = t
        if s_scl:
            self._bit_at_rise = s_sda

    def _on_sda(self, s_scl, s_sda, t):
        if s_scl:  # SDA changed while SCL is high: a bus condition
            if s_sda == 0:
                if self.in_frame:
                    self._check("tSU;STA", t - self._scl_rise, self.t.t_su_sta)
                    self.events.append("Sr")
                else:
                    if self._stop is not None:
                        self._check("tBUF", t - self._stop, self.t.t_buf)
                    self.events.append("S")
                self.in_frame = True
                self._start = t
                self._first_fall_after_start = True
                self._bits = []
            else:
                if self.in_frame:
                    self._check("tSU;STO", t - self._scl_rise, self.t.t_su_sto)
                    if self._bits:
                        self.errors.append(f"STOP after {len(self._bits)} bits at {t:.0f} ns")
                self.events.append("P")
                self.in_frame = False
                self._stop = t
                self._bits = []
        else:
            self._sda_change = t


class BusCondition(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind  # "Sr" or "P"


class I2cTarget:
    """A target device: records written bytes, answers reads from `replies` (0xFF when empty).

    `stretch_ns` > 0 holds SCL low for that long after every byte, before the ACK bit.
    """

    def __init__(self, scl: Line, sda: Line, address: int, replies=(), stretch_ns: float = 0):
        self.scl, self.sda, self.address = scl, sda, address
        self.replies = deque(replies)
        self.stretch_ns = stretch_ns
        self.written: list[list[int]] = []  # one list per write transaction
        self.reads = 0
        self.hold_scl_forever = False
        self._task = cocotb.start_soon(self._run())

    def stop(self):
        self._task.cancel()
        self.scl.drive(1)
        self.sda.drive(1)

    async def _wait_start(self):
        while True:
            await FallingEdge(self.sda.pad)
            if self.scl.level:
                return

    async def _bit_in(self) -> int:
        """Next bit: wait for SCL high, sample SDA, wait for SCL low. Raises on START/STOP."""
        if not self.scl.level:
            await RisingEdge(self.scl.pad)
        bit = self.sda.level
        while True:
            await First(FallingEdge(self.scl.pad), self.sda.pad.value_change)
            if not self.scl.level:
                return bit
            raise BusCondition("Sr" if self.sda.level == 0 else "P")

    async def _byte_in(self) -> int:
        v = 0
        for _ in range(8):
            v = (v << 1) | await self._bit_in()
        return v

    async def _stretch(self):
        if self.hold_scl_forever:
            self.scl.drive(0)
            await Timer(1000, "ms")
        if self.stretch_ns:
            self.scl.drive(0)
            await Timer(self.stretch_ns, "ns")
            self.scl.drive(1)

    async def _send_ack(self, ack: bool):
        """Called with SCL low after the 8th bit: drive the ACK bit for one clock.

        Any clock stretching happens after SDA is set, so the data is set up before SCL rises."""
        self.sda.drive(0 if ack else 1)
        await self._stretch()
        if not self.scl.level:
            await RisingEdge(self.scl.pad)
        await FallingEdge(self.scl.pad)
        self.sda.drive(1)

    async def _run(self):
        restart = False
        while True:
            if not restart:
                await self._wait_start()
            restart = False
            try:
                await FallingEdge(self.scl.pad)
                byte = await self._byte_in()
                if byte >> 1 != self.address:
                    while True:  # not for us: ignore until the next condition
                        await self._bit_in()
                await self._send_ack(True)
                if byte & 1 == 0:
                    data = []
                    self.written.append(data)
                    while True:
                        data.append(await self._byte_in())
                        await self._send_ack(True)
                else:
                    self.reads += 1
                    while True:
                        v = self.replies.popleft() if self.replies else 0xFF
                        for i in range(7, -1, -1):
                            self.sda.drive((v >> i) & 1)
                            if i == 7:
                                await self._stretch()
                            await RisingEdge(self.scl.pad)
                            await FallingEdge(self.scl.pad)
                        self.sda.drive(1)
                        await RisingEdge(self.scl.pad)
                        nack = self.sda.level
                        await FallingEdge(self.scl.pad)
                        if nack:
                            break
                    while True:
                        await self._bit_in()
            except BusCondition as c:
                self.sda.drive(1)
                restart = c.kind == "Sr"


class I2cController:
    """A controller that times the bus itself and waits for SCL to rise (clock stretching)."""

    def __init__(self, scl: Line, sda: Line, timing: I2cTiming, slack: float = 1.15):
        self.scl, self.sda, self.t = scl, sda, timing
        self.k = slack  # run a little slower than the minimum timings
        self.scl_low = False  # the level this model drives (a pad read in the same step is stale)

    async def _wait(self, ns: float):
        await Timer(round(ns * self.k), "ns")

    def _scl_drive_low(self):
        self.scl.drive(0)
        self.scl_low = True

    async def _release_scl(self, timeout_ns: float = 5e6):
        self.scl.drive(1)
        self.scl_low = False
        await Timer(1, "ns")
        waited = 0.0
        while not self.scl.level:
            await Timer(100, "ns")
            waited += 100
            assert waited < timeout_ns, "SCL held low by the target for too long"

    async def start(self):
        if self.scl_low:  # repeated START
            self.sda.drive(1)
            await self._wait(self.t.t_low)
            await self._release_scl()
            await self._wait(self.t.t_su_sta)
        self.sda.drive(0)
        await self._wait(self.t.t_hd_sta)
        self._scl_drive_low()

    async def stop(self):
        self.sda.drive(0)
        await self._wait(self.t.t_low)
        await self._release_scl()
        await self._wait(self.t.t_su_sto)
        self.sda.drive(1)
        await self._wait(self.t.t_buf)

    async def _bit(self, out: int) -> int:
        self.sda.drive(out)
        await self._wait(self.t.t_low)
        await self._release_scl()
        await self._wait(self.t.t_high / 2)
        seen = self.sda.level
        await self._wait(self.t.t_high / 2)
        self._scl_drive_low()
        return seen

    async def write_byte(self, v: int) -> bool:
        for i in range(7, -1, -1):
            seen = await self._bit((v >> i) & 1)
            assert seen == (v >> i) & 1, "arbitration lost"
        return await self._bit(1) == 0  # ACK

    async def read_byte(self, ack: bool) -> int:
        v = 0
        for _ in range(8):
            v = (v << 1) | await self._bit(1)
        await self._bit(0 if ack else 1)
        return v

    async def write(self, address: int, data, stop: bool = True) -> bool:
        await self.start()
        ok = await self.write_byte(address << 1)
        if ok:
            for b in data:
                ok = await self.write_byte(b) and ok
        if stop:
            await self.stop()
        return ok

    async def read(self, address: int, count: int) -> list[int] | None:
        await self.start()
        if not await self.write_byte((address << 1) | 1):
            await self.stop()
            return None
        out = [await self.read_byte(i < count - 1) for i in range(count)]
        await self.stop()
        return out
