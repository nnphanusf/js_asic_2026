"""Shared cocotb testbench for Bitweaver.

`TB` starts the clock, resets the design, runs the lock-step checker (the design's outputs must
equal the reference model's on every clock cycle) and gives the tests host access over the real SPI
pins. The protocol models in the tests attach to the tb's pad signals, never to the model.
"""

from __future__ import annotations

import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Lock, ReadOnly, RisingEdge

from bitweaver import regmap as R
from bitweaver.asm import Program, assemble_one, enc_jmp, instr
from bitweaver.iss import Bitweaver
from bitweaver.loader import frame_bytes, load_ops, pad_masks, pinvec_ops

CLK_PERIOD_PS = 16666  # 60 MHz, even picoseconds
GL = os.environ.get("GATES") == "yes"
UIO_NAMES = [f"ext{n}" for n in range(11, 19)]
IN_NAMES = [f"in{n}" for n in range(5)]


class LockstepError(AssertionError):
    pass


class TB:
    def __init__(self, dut, lockstep: bool = True, spi_half: int = 4):
        self.dut = dut
        self.lockstep = lockstep and not GL
        self.iss = Bitweaver()
        self.spi_half = spi_half
        self.cycles = 0
        self.checked = 0
        self.en = 0
        self.od = 0
        self.filt = 0
        self.origin = 0
        self.first_bytes = (0, 0)
        self._mon = None
        self._lock = Lock()  # host frames from concurrent tasks must not interleave

    # ------------------------------------------------------------------ setup
    async def start(self, reset_cycles: int = 5):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_PS, unit="ps").start())
        dut.ena.value = 1
        dut.rst_n.value = 0
        dut.host_cs_n.value = 1
        dut.host_sck.value = 0
        dut.host_mosi.value = 0
        for name in IN_NAMES + UIO_NAMES:
            getattr(dut, name).value = 1
        await ClockCycles(dut.clk, reset_cycles)
        if self.lockstep:
            self._mon = cocotb.start_soon(self._check())
        await FallingEdge(dut.clk)
        dut.rst_n.value = 1
        await ClockCycles(dut.clk, 4)
        await FallingEdge(dut.clk)

    async def _check(self):
        dut, iss = self.dut, self.iss
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            self.cycles += 1
            try:
                ui, uio = int(dut.ui_q.value), int(dut.uio_q.value)
                rst, ena = int(dut.rst_q.value), int(dut.ena_q.value)
            except ValueError:
                continue
            exp = iss.step(ui, uio, rst_n=rst, ena=ena)
            got = (int(dut.uo_out.value), int(dut.uio_out.value), int(dut.uio_oe.value))
            self.checked += 1
            if exp != got:
                raise LockstepError(
                    f"lock-step mismatch at cycle {iss.cycle}: design uo/uio_out/uio_oe = "
                    f"{got[0]:02x}/{got[1]:02x}/{got[2]:02x}, model = {exp[0]:02x}/{exp[1]:02x}/{exp[2]:02x}")

    def check_ok(self):
        """Raise if the checker coroutine has stopped with a mismatch."""
        if self._mon is not None and self._mon.done():
            self._mon.result()

    async def cycles_(self, n: int):
        await ClockCycles(self.dut.clk, n)
        self.check_ok()

    # ------------------------------------------------------------------ pads
    def set_in(self, pin: int, level: int):
        getattr(self.dut, f"in{pin}").value = level

    def set_ext(self, pin: int, level: int):
        getattr(self.dut, f"ext{pin}").value = level

    def pad(self, pin: int) -> int:
        return int(getattr(self.dut, f"pad{pin}").value)

    def pads(self) -> int:
        return int(self.dut.pads.value)

    # ------------------------------------------------------------------ host SPI (mode 0, clock-synchronous)
    async def frame(self, data: bytes) -> bytes:
        async with self._lock:
            return await self._frame(data)

    async def _frame(self, data: bytes) -> bytes:
        dut, half = self.dut, self.spi_half
        clk = dut.clk
        out = bytearray()
        await FallingEdge(clk)
        dut.host_cs_n.value = 0
        await ClockCycles(clk, half, rising=False)
        for byte in data:
            v = 0
            for bit in range(7, -1, -1):
                dut.host_mosi.value = (byte >> bit) & 1
                await ClockCycles(clk, half, rising=False)
                v = (v << 1) | int(dut.host_miso.value)
                dut.host_sck.value = 1
                await ClockCycles(clk, half, rising=False)
                dut.host_sck.value = 0
            out.append(v)
        dut.host_mosi.value = 0
        await ClockCycles(clk, half, rising=False)
        dut.host_cs_n.value = 1
        await ClockCycles(clk, half + 2, rising=False)
        self.check_ok()
        self.first_bytes = (out[0], out[1])
        return bytes(out)

    async def write(self, addr: int, words, inc: bool = False):
        await self.frame(frame_bytes(addr, list(words), write=True, inc=inc))

    async def read(self, addr: int, count: int = 1, inc: bool = False) -> list[int]:
        raw = await self.frame(frame_bytes(addr, count=count, write=False, inc=inc))
        return [(raw[2 + 2 * i] << 8) | raw[3 + 2 * i] for i in range(count)]

    async def read1(self, addr: int) -> int:
        return (await self.read(addr))[0]

    # ------------------------------------------------------------------ high-level host operations
    async def load(self, prog: Program | str, sm: int, origin: int | None = None, **overrides) -> int:
        if isinstance(prog, str):
            prog = assemble_one(prog)
        if origin is None:
            origin = self.origin
            self.origin += len(prog.code)
        for addr, words, inc in load_ops(prog, sm, origin, self.en, **overrides):
            await self.write(addr, words, inc)
        self.en &= ~(1 << sm)
        od, filt = pad_masks(prog, **{k: v for k, v in overrides.items() if k in ("od_pins", "filter_pins")})
        if od & ~self.od:
            await self.set_open_drain(self.od | od)
        if filt & ~self.filt:
            await self.set_filter(self.filt | filt)
        return origin

    async def enable(self, mask: int):
        self.en |= mask & 0xF
        await self.write(R.addr("CTRL"), [self.en])

    async def disable(self, mask: int):
        self.en &= ~mask
        await self.write(R.addr("CTRL"), [self.en])

    async def set_open_drain(self, mask: int):
        self.od = mask
        for op in pinvec_ops("PAD_OD", mask):
            await self.write(*op)

    async def set_filter(self, mask: int):
        self.filt = mask
        for op in pinvec_ops("PAD_FILT", mask):
            await self.write(*op)

    async def set_pins(self, levels: int, dirs: int):
        for op in pinvec_ops("PIN_OUT", levels) + pinvec_ops("PIN_OE", dirs):
            await self.write(*op)

    async def push(self, sm: int, words):
        await self.write(R.addr("FIFO", sm), list(words))

    async def pop(self, sm: int, count: int = 1) -> list[int]:
        return await self.read(R.addr("FIFO", sm), count)

    async def exec(self, sm: int, text_or_word):
        word = instr(text_or_word) if isinstance(text_or_word, str) else text_or_word
        await self.write(R.addr("EXEC", sm), [word])

    async def read_imem(self, addr: int, sm: int = 0) -> int:
        """Read an instruction-memory word through engine `sm` (there is no host read port).

        Points the engine there with an injected JMP and reads its instruction register. The engine
        must be disabled; its ORG register (and JMP_PIN) and PC are changed.
        """
        await self.write(R.addr("ORG", sm), [R.pack("ORG", org=addr & 0x20)])
        await self.exec(sm, enc_jmp(R.COND_ALWAYS, addr & 31))
        return await self.read1(R.addr("EXEC", sm))

    async def stat(self, sm: int) -> int:
        return await self.read1(R.addr("STAT", sm))

    async def rx_level(self, sm: int) -> int:
        return (await self.stat(sm) >> 12) & 15

    async def tx_level(self, sm: int) -> int:
        return (await self.stat(sm) >> 8) & 15

    async def reg(self, sm: int, name: str) -> int:
        """Read X, Y, ISR, OSR or CRC of an engine through EXEC (clobbers its ISR and RX FIFO head)."""
        await self.exec(sm, f"mov isr, {name}")
        await self.exec(sm, "push noblock")
        return (await self.pop(sm))[0]

    async def wait_rx(self, sm: int, count: int, timeout_cycles: int = 200000, poll: int = 200):
        """Wait until the RX FIFO of `sm` holds at least `count` words (polls STAT over SPI)."""
        waited = 0
        while await self.rx_level(sm) < count:
            await self.cycles_(poll)
            waited += poll
            if waited > timeout_cycles:
                raise TimeoutError(f"SM{sm} RX FIFO did not reach {count} words")

    async def wait_tx_empty(self, sm: int, timeout_cycles: int = 200000, poll: int = 200):
        waited = 0
        while await self.tx_level(sm):
            await self.cycles_(poll)
            waited += poll
            if waited > timeout_cycles:
                raise TimeoutError(f"SM{sm} TX FIFO did not drain")

    async def push_all(self, sm: int, words, timeout_cycles: int = 400000, poll: int = 200, depth: int = 4):
        """Push words without ever overflowing the TX FIFO (waits for space). depth = 8 for a joined FIFO."""
        words = list(words)
        waited = 0
        while words:
            space = depth - await self.tx_level(sm)
            if space > 0:
                await self.push(sm, words[:space])
                words = words[space:]
            else:
                await self.cycles_(poll)
                waited += poll
                if waited > timeout_cycles:
                    raise TimeoutError(f"SM{sm} TX FIFO stayed full")

    async def collect(self, sm: int, count: int, timeout_cycles: int = 400000, poll: int = 200) -> list[int]:
        """Pop `count` words from the RX FIFO as they arrive."""
        out: list[int] = []
        waited = 0
        while len(out) < count:
            n = min(await self.rx_level(sm), count - len(out))
            if n:
                out += await self.pop(sm, n)
                waited = 0
            else:
                await self.cycles_(poll)
                waited += poll
                if waited > timeout_cycles:
                    raise TimeoutError(f"SM{sm}: got {len(out)} of {count} words: {out}")
        return out

    def done(self):
        self.check_ok()
        if self.lockstep:
            self.dut._log.info(f"lock-step: {self.checked} cycles matched the reference model")


def seed_from_env(default: int) -> int:
    s = int(os.environ.get("SEED", default))
    random.seed(s)
    return s
