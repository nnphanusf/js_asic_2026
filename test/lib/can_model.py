"""A classical CAN node for cocotb, written from the CAN 2.0A specification (ISO 11898-1).

It has its own frame encoder, bit stuffing and CRC15, independent of bitweaver.proto. The bus is
modelled as a wired AND: the level on the design's RX pad is (design TX) AND (model TX).
"""

from __future__ import annotations

import cocotb
from cocotb.triggers import FallingEdge, Timer


def crc15(bits):
    reg = 0
    for b in bits:
        top = (reg >> 14) & 1
        reg = (reg << 1) & 0x7FFF
        if b != top:
            reg ^= 0x4599
    return reg


def stuff(bits):
    out, run, last = [], 0, None
    for b in bits:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            out.append(1 - b)
            last, run = 1 - b, 1
    return out


def encode(ident, data, rtr=False, dlc=None):
    """A data frame, or with rtr a remote frame: no data field, DLC = the length it requests."""
    dlc = len(data) if dlc is None else dlc
    f = [0] + [(ident >> i) & 1 for i in range(10, -1, -1)] + [int(rtr), 0, 0]
    f += [(dlc >> i) & 1 for i in range(3, -1, -1)]
    for byte in (b"" if rtr else data):
        f += [(byte >> i) & 1 for i in range(7, -1, -1)]
    c = crc15(f)
    f += [(c >> i) & 1 for i in range(14, -1, -1)]
    s = stuff(f)
    ack_index = len(s) + 1
    return s + [1, 1, 1] + [1] * 7, ack_index  # CRC delimiter, ACK slot, ACK delimiter, EOF


class CanBus:
    """Wired-AND bus between the design (tx_pad -> rx_drive) and one model node."""

    def __init__(self, tx_pad, rx_drive):
        self.tx_pad, self.rx_drive = tx_pad, rx_drive
        self.model_level = 1
        self.rx_drive.value = 1
        cocotb.start_soon(self._watch())

    def level(self) -> int:
        return int(self.tx_pad.value) & self.model_level

    def drive(self, level: int):
        self.model_level = level
        self.rx_drive.value = self.level()

    async def _watch(self):
        while True:
            await self.tx_pad.value_change
            self.rx_drive.value = self.level()


class CanNode:
    def __init__(self, bus: CanBus, bitrate: float, ack: bool = True):
        self.bus, self.ack = bus, ack
        self.bit_ns = round(1e9 / bitrate)
        # (ident, data, crc_ok, stuff_ok, delim_ok); for a remote frame, data is the requested length
        self.received: list[tuple] = []
        self.transmitting = False
        cocotb.start_soon(self._rx())

    async def send(self, ident: int, data=b"", rtr: bool = False, dlc: int | None = None) -> bool:
        """Transmit one frame (rtr: a remote frame requesting dlc bytes); returns True if some other
        node acknowledged it."""
        bits, ack_i = encode(ident, bytes(data), rtr, dlc)
        self.transmitting = True
        acked = False
        for i, b in enumerate(bits):
            self.bus.drive(b)
            if i == ack_i:
                await Timer(round(self.bit_ns * 0.7), "ns")
                acked = self.bus.level() == 0
                await Timer(self.bit_ns - round(self.bit_ns * 0.7), "ns")
            else:
                await Timer(self.bit_ns, "ns")
        self.bus.drive(1)
        await Timer(3 * self.bit_ns, "ns")  # intermission
        self.transmitting = False
        return acked

    async def _rx(self):
        while True:
            await FallingEdge(self.bus.rx_drive)
            if self.transmitting:
                continue
            await Timer(round(self.bit_ns * 0.7), "ns")
            raw = []
            bits = []
            run, last, stuff_ok = 0, None, True

            def take(b):
                nonlocal run, last, stuff_ok
                raw.append(b)
                if run == 5:  # this is a stuff bit
                    if b == last:
                        stuff_ok = False
                    run, last = 1, b
                    return False
                run = run + 1 if b == last else 1
                last = b
                bits.append(b)
                return True

            def n_data():  # a remote frame (RTR, bit 12) has no data field whatever its DLC
                return 0 if bits[12] else min(int("".join(map(str, bits[15:19])), 2), 8)

            def need():
                if len(bits) < 19:
                    return 19 + 15
                return 19 + 8 * n_data() + 15

            while len(bits) < need():
                take(self.bus.level())
                await Timer(self.bit_ns, "ns")
            ident = int("".join(map(str, bits[1:12])), 2)
            end = 19 + 8 * n_data()
            data = bytes(int("".join(map(str, bits[19 + 8 * i:27 + 8 * i])), 2) for i in range(n_data()))
            if bits[12]:
                data = int("".join(map(str, bits[15:19])), 2)  # the requested length
            crc_ok = crc15(bits[:end]) == int("".join(map(str, bits[end:end + 15])), 2)
            # Stuffing covers the whole CRC sequence: if its last bit ends a run of five, a stuff bit
            # of the opposite level follows before the delimiter (ISO 11898-1, 10.5). take() checks it.
            if run == 5:
                take(self.bus.level())
                await Timer(self.bit_ns, "ns")
            # Now 70 % into the CRC delimiter, which must be recessive. The ACK slot starts 0.3 bit
            # times later.
            delim_ok = self.bus.level() == 1
            await Timer(self.bit_ns - round(self.bit_ns * 0.7), "ns")
            if self.ack and crc_ok and stuff_ok and delim_ok:
                self.bus.drive(0)
                await Timer(self.bit_ns, "ns")
                self.bus.drive(1)
            self.received.append((ident, data, crc_ok, stuff_ok, delim_ok))
            await Timer(9 * self.bit_ns, "ns")  # ACK delimiter, EOF
