"""SPI models for cocotb, built on cocotbext-spi's SpiSlaveBase. They see only the SPI wires."""

from collections import deque

from cocotb.triggers import First
from cocotbext.spi.spi import SpiSlaveBase


class StreamTarget(SpiSlaveBase):
    """SPI target model: any CPOL/CPHA, any number of bytes per CS frame, MSB first.

    Records the bytes received in each frame and answers with bytes from `replies` (0xFF when empty).
    """

    def __init__(self, bus, config, replies=()):
        self._config = config
        self.replies = deque(replies)
        self.frames = []
        super().__init__(bus)

    def _next(self):
        return self.replies.popleft() if self.replies else 0xFF

    async def _transaction(self, frame_start, frame_end):
        await frame_start
        self.idle.clear()
        cpol, cpha = int(self._config.cpol), int(self._config.cpha)
        got, cur, nbits = [], 0, 0
        tx = self._next()
        need_pop = False  # CPHA 0: the next byte's first bit is shown before we know the byte is wanted
        if not cpha:
            self._miso.value = (tx >> 7) & 1
        while True:
            await First(self._sclk.value_change, frame_end)
            if int(self._cs.value) == 1:
                break
            leading = int(self._sclk.value) != cpol
            if leading != bool(cpha):  # sample edge
                if need_pop:
                    tx, need_pop = self._next(), False
                cur = ((cur << 1) | int(self._mosi.value)) & 0xFF
                nbits += 1
                if nbits == 8:
                    got.append(cur)
                    cur, nbits = 0, 0
            elif cpha:  # leading edge with CPHA = 1: present the next bit
                if nbits == 0 and got:
                    tx = self._next()
                self._miso.value = (tx >> (7 - nbits)) & 1
            else:  # trailing edge with CPHA = 0
                if nbits == 0:
                    head = self.replies[0] if self.replies else 0xFF
                    self._miso.value = (head >> 7) & 1
                    need_pop = True
                else:
                    self._miso.value = (tx >> (7 - nbits)) & 1
        assert nbits == 0, f"frame ended after {nbits} extra bits"
        self.frames.append(bytes(got))
        self.idle.set()
