# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""Low-speed USB packets: sw/programs/usb.bw against an independent line model (test/lib/usb_model.py).

The bit-level assist unit does NRZI and bit stuffing both ways and the CRC16 of DATA packets on
transmit. Payloads of 0xFF force a stuff bit every six bits. Lock-step stays on throughout.
"""

import cocotb
from cocotb.triggers import Timer

from bitweaver.asm import load_program
from bitweaver.loader import clkdiv_fields
from bitweaver.proto import usb_rx_decode, usb_tx_words
from bwtb import F_CLK, TB
from usb_model import UsbLines, packet_bits

PACKETS = [
    dict(pid="DATA0", data=b"\x80\x06\x00\x01\x00\x00\x40\x00"),  # a GET_DESCRIPTOR setup payload
    dict(pid="DATA1", data=b"\xff\xff\xff"),
    dict(pid="DATA0", data=b""),
    dict(pid="ACK"),
    dict(pid="IN", addr=5, endp=1),
    dict(pid="SETUP", addr=0x7F, endp=0xF),
]


@cocotb.test()
async def test_usb_tx(dut):
    tb = TB(dut)
    await tb.start()
    lines = UsbLines(dut)
    prog = load_program("usb_tx", F_CLK=F_CLK)
    await tb.load(prog, 0)
    await tb.enable(1)
    for p in PACKETS:
        rx = cocotb.start_soon(lines.receive())
        await tb.push_all(0, usb_tx_words(prog, **p), depth=8)
        res = await rx
        assert res["pid"] == p["pid"] and res["pid_ok"], res
        assert res["stuff_ok"] and res["eop_ok"] and res["whole_bytes"] and res["crc_ok"], res
        if "data" in p:
            assert res["payload"] == p["data"]
        if "addr" in p:
            assert (res["addr"], res["endp"]) == (p["addr"], p["endp"])
        await Timer(5, "us")
        assert lines.state() == UsbLines.J and int(dut.uio_oe.value) & 3 == 0  # bus released
    tb.done()


@cocotb.test()
async def test_usb_tx_at_50mhz_clk16_host(dut):
    """At the rated 50 MHz with the host port at SCK = clk/16: push_packet preloads the joined FIFO
    before starting the engine, so even the 13-word DATA packet never runs dry on the wire."""
    tb = TB(dut, spi_half=8, clk_ps=20000)
    await tb.start()
    prog = load_program("usb_tx", F_CLK=50_000_000)
    i, f = clkdiv_fields(prog.config["clkdiv"])
    lines = UsbLines(dut, bit_ps=round(10 * (i + f / 256) * 20000))
    await tb.load(prog, 0)
    await tb.enable(1)
    for p in PACKETS + [dict(pid="DATA1", data=b"abcdefgh")]:
        rx = cocotb.start_soon(lines.receive())
        await tb.push_packet(0, usb_tx_words(prog, **p), idle_pins=(1 << 11) | (1 << 12))
        res = await rx
        assert res["pid"] == p["pid"] and res["pid_ok"], res
        assert res["stuff_ok"] and res["eop_ok"] and res["whole_bytes"] and res["crc_ok"], res
        if "data" in p:
            assert res["payload"] == p["data"]
    tb.done()


@cocotb.test()
async def test_usb_rx(dut):
    tb = TB(dut)
    await tb.start()
    lines = UsbLines(dut)
    await tb.load(load_program("usb_se0"), 0)
    await tb.load(load_program("usb_rx", F_CLK=F_CLK), 1)
    await tb.enable(0b11)
    for p in PACKETS:
        await lines.send(packet_bits(**p))
        await Timer(3, "us")
        words = await tb.pop(1, await tb.rx_level(1))
        name, body, ok = usb_rx_decode(words)
        assert (name, ok) == (p["pid"], True), (p, [hex(w) for w in words])
        if "data" in p:
            assert body == p["data"]
    tb.done()
