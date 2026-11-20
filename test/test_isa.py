# SPDX-FileCopyrightText: © 2026 nnphanusf
# SPDX-License-Identifier: Apache-2.0
"""Per-instruction tests on the RTL.

Each test checks a rule of docs/isa.md against hand-computed expected values, while the lock-step
checker compares every output pin with the reference model on every clock cycle.
"""

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge

from bitweaver import regmap as R
from bitweaver.asm import assemble_one, instr
from bwtb import TB


async def setup(dut, src, sm=0, pushes=(), enable=True, **cfg):
    tb = TB(dut)
    await tb.start()
    await tb.load(src, sm, **cfg)
    if pushes:
        await tb.push(sm, pushes)
    if enable:
        await tb.enable(1 << sm)
    return tb


async def capture(tb, n, pin=None):
    """Sample the pads after each of the next n rising edges. Returns a string for one pin or a list."""
    out = []
    for _ in range(n):
        await RisingEdge(tb.dut.clk)
        await ReadOnly()
        out.append(int(tb.dut.pads.value))
    await RisingEdge(tb.dut.clk)
    tb.check_ok()
    if pin is None:
        return out
    return "".join(str((v >> pin) & 1) for v in out)


async def capture_during(tb, n, pin, action):
    """Start sampling a pin, then run `action` (e.g. the enable write), and return n samples."""
    task = cocotb.start_soon(capture(tb, n, pin))
    await action
    return await task


async def capture_oe(tb, n, bit):
    s = ""
    for _ in range(n):
        await RisingEdge(tb.dut.clk)
        await ReadOnly()
        s += str((int(tb.dut.uio_oe.value) >> bit) & 1)
    await RisingEdge(tb.dut.clk)
    return s


# ----------------------------------------------------------------------------- execution basics
@cocotb.test()
async def test_clock_divider_and_delay(dut):
    tb = await setup(dut, ".pins set=5 set_count=1\n.init set pindirs, 1\n set pins, 1 [1]\n set pins, 0", clkdiv=3)
    w = await capture(tb, 60, 5)
    assert "0001111110001111110" in w  # 2 ticks high, 1 tick low, 3 cycles per tick
    tb.done()


@cocotb.test()
async def test_fractional_divider(dut):
    tb = await setup(dut, ".pins set=5 set_count=1\n.init set pindirs, 1\n set pins, 1\n set pins, 0", clkdiv=2.5)
    w = await capture(tb, 100, 5)
    assert "110001100011000" in w or "111001110011100" in w
    assert w.count("1") in (40, 41, 59, 60)
    tb.done()


@cocotb.test()
async def test_divider_integer_part_one_and_carries(dut):
    """Integer part 1 and 2 with fractional carries, around the divider's registered zero flag.

    The pin toggles on every tick, so the number of transitions counts the ticks; lock-step checks
    the exact tick cycles against the reference model.
    """
    tb = await setup(dut, ".pins set=5 set_count=1\n.init set pindirs, 1\n set pins, 1\n set pins, 0",
                     enable=False)
    for div_int, div_frac, ticks in ((1, 255, 150), (1, 128, 200), (1, 1, 299), (2, 255, 100), (1, 0, 300)):
        await tb.write(R.addr("CLKDIV_INT", 0), [div_int, div_frac], inc=True)
        await tb.enable(1)
        w = await capture(tb, 301, 5)
        await tb.disable(1)
        edges = sum(a != b for a, b in zip(w, w[1:]))
        assert abs(edges - ticks) <= 1, (div_int, div_frac, edges)
    tb.done()


@cocotb.test()
async def test_divider_integer_part_zero(dut):
    """Integer part 0 counts 65536 cycles; a fractional carry then brings the next tick one cycle later."""
    tb = await setup(dut, ".pins set=5 set_count=1\n.init set pindirs, 1\n set pins, 1\n set pins, 0",
                     enable=False)
    await tb.write(R.addr("CLKDIV_INT", 0), [0, 128], inc=True)
    await tb.enable(1)
    await tb.cycles_(65536 - 400)
    w = await capture(tb, 420, 5)
    assert sum(a != b for a, b in zip(w, w[1:])) == 2  # the 65536-cycle tick, then one a cycle later
    tb.done()


@cocotb.test()
async def test_imem_rewrite_on_every_fetch_phase(dut):
    """Host writes to the word an engine is fetching, landing on each cycle of a two-cycle loop.

    The instruction memory is write-first, so a word written in the cycle the engine fetches it is
    the word that runs; lock-step checks every cycle against the reference model.
    """
    tb = await setup(dut, ".pins set=5 set_count=1\n.init set pindirs, 1\n.wrap_target\n set pins, 1\n"
                     " set pins, 0\n.wrap", clkdiv=1)
    first = R.IMEM_BASE + tb.origin - 2
    for delay in range(4):
        await tb.cycles_(delay)
        await tb.write(first, [instr("set pins, 0")])  # the loop now drives 0 on both steps
        assert "1" not in await capture(tb, 20, 5)
        await tb.cycles_(delay)
        await tb.write(first, [instr("set pins, 1")])
        assert "1010" in await capture(tb, 20, 5)
    tb.done()


@cocotb.test()
async def test_wrap_and_position_independence(dut):
    src = ".pins set=5 set_count=1\n.init set pindirs, 1\n set x, 3\n.wrap_target\nl: set pins, 1\n " \
          "jmp x-- l\n set pins, 0 [3]\n.wrap"
    tb = TB(dut)
    await tb.start()
    for origin in (0, 17, 60):
        await tb.load(src, 0, origin=origin)
        w = await capture_during(tb, 400, 5, tb.enable(1))
        assert "000011111111" in w or "0000111111110000" in w, w
        await tb.disable(1)
    tb.done()


@cocotb.test()
async def test_jmp_conditions(dut):
    src = """
    set x, 2
    set y, 0
a:  jmp x-- a
    jmp !y b
    set y, 31
b:  jmp !x c
    jmp x!=y d
c:  set y, 30
d:  jmp y-- e
    mov isr, y
e:  jmp pin f
    set x, 1
f:  jmp !osre g
    push
g:  jmp g
"""
    tb = await setup(dut, src, jmp_pin=0)
    await tb.cycles_(40)
    assert await tb.pop(0) == [0xFFFF]
    assert await tb.reg(0, "x") == 0xFFFF
    tb.done()


@cocotb.test()
async def test_in_out_shift_directions(dut):
    prog = "pull\n out x, 4\n out y, 8\n in y, 4\n in x, 8\n push\nh: jmp h"
    tb = await setup(dut, prog, pushes=[0xABCD])
    assert await tb.pop(0) == [0x0DC0]
    await tb.load(prog, 1, origin=10, in_shiftdir=0, out_shiftdir=0)
    await tb.push(1, [0xABCD])
    await tb.enable(2)
    assert await tb.pop(1) == [0x0C0A]
    tb.done()


@cocotb.test()
async def test_autopull_autopush(dut):
    tb = await setup(dut, ".shift autopull=8 autopush=8\n.wrap_target\n out x, 4\n in x, 4\n.wrap",
                     pushes=[0x21, 0x43])
    await tb.cycles_(40)
    assert await tb.pop(0, 2) == [0x2100, 0x4300]
    assert await tb.read1(R.addr("FDEBUG")) & 0x0100
    tb.done()


@cocotb.test()
async def test_autopush_stalls_when_full(dut):
    tb = await setup(dut, ".shift autopush=4\n.wrap_target\n set x, 5\n in x, 4\n.wrap")
    await tb.cycles_(60)
    assert await tb.rx_level(0) == 4
    assert await tb.read1(R.addr("FDEBUG")) & 0x1000
    assert await tb.pop(0, 4) == [0x5000] * 4
    tb.done()


@cocotb.test()
async def test_push_pull_variants(dut):
    src = """
    pull noblock
    set x, 9
    pull noblock
    mov isr, osr
    push
    push iffull
    in null, 16
    push iffull
    pull ifempty
    out null, 16
    pull ifempty
    mov isr, osr
    push
h:  jmp h
"""
    tb = await setup(dut, src)
    await tb.cycles_(40)
    assert await tb.pop(0, 2) == [9, 0]
    assert await tb.stat(0) & 63 == 10
    await tb.push(0, [0x7777])
    assert await tb.pop(0) == [0x7777]
    tb.done()


@cocotb.test()
async def test_push_noblock_drops_when_full(dut):
    tb = await setup(dut, ".wrap_target\n set x, 1\n mov isr, x\n push noblock\n.wrap")
    await tb.cycles_(60)
    assert await tb.rx_level(0) == 4
    assert await tb.read1(R.addr("FDEBUG")) & 0x1000
    tb.done()


@cocotb.test()
async def test_mov_ops_and_sources(dut):
    src = """
    pull
    mov x, osr
    mov y, !x
    mov isr, y
    push
    mov isr, ::x
    push
    mov isr, <>x
    push
    mov isr, null
    push
h:  jmp h
"""
    tb = await setup(dut, src, pushes=[0x12C4])
    assert await tb.pop(0, 4) == [0xED3B, 0x2348, 0xC412, 0]
    tb.done()


@cocotb.test()
async def test_mov_pins_and_in_pins(dut):
    tb = TB(dut)
    await tb.start()
    for i, v in enumerate([0, 1, 1, 0, 1]):
        tb.set_in(i, v)
    await tb.load(".pins in=1 out=5 out_count=6\n.init mov osr, !null\n.init out pindirs, 6\n"
                  " mov x, pins\n mov pins, x\n in pins, 3\n push\nh: jmp h", 0)
    await tb.enable(1)
    await tb.cycles_(20)
    assert (tb.pads() >> 5) & 0x3F == 0b001011
    assert (await tb.pop(0))[0] >> 13 == 0b011
    tb.done()


@cocotb.test()
async def test_set_and_out_destinations(dut):
    tb = await setup(dut, ".pins set=11 set_count=3\n set pindirs, 7\n set pins, 5\n set x, 31\n set y, 17\nh: jmp h")
    await tb.cycles_(20)
    assert (int(dut.uio_out.value), int(dut.uio_oe.value)) == (0b101, 0b111)
    assert (await tb.reg(0, "x"), await tb.reg(0, "y")) == (31, 17)
    src = """
.pins out=11 out_count=4
    pull
    out pindirs, 4
    out pins, 4
    out isr, 4
    out null, 4
    pull
    out pc, 5
    set x, 1
    set x, 2
    in null, 12
    push iffull
h:  jmp h
"""
    await tb.load(src, 1, origin=20, in_shiftdir=0)
    await tb.push(1, [0x03AF, 9])
    await tb.enable(2)
    await tb.cycles_(40)
    assert (int(dut.uio_out.value) & 0xF, int(dut.uio_oe.value) & 0xF) == (0xA, 0xF)
    assert await tb.pop(1) == [0x3000]
    assert await tb.reg(1, "x") == 0
    tb.done()


@cocotb.test()
async def test_exec(dut):
    tb = await setup(dut, "pull\n out exec, 16\n mov isr, x\n push\nh: jmp h", pushes=[instr("set x, 21")])
    assert await tb.pop(0) == [21]
    # Host EXEC works on a disabled engine and does not advance the PC.
    await tb.load("set x, 1\n set x, 2", 1, origin=10)
    await tb.exec(1, "set y, 9")
    assert await tb.stat(1) & 0x7F == 10
    assert await tb.reg(1, "y") == 9
    await tb.exec(1, "jmp 1")
    assert await tb.stat(1) & 63 == 11
    await tb.exec(1, "pull block")
    assert await tb.stat(1) & 0x40
    await tb.push(1, [5])
    assert not await tb.stat(1) & 0x40
    assert await tb.reg(1, "osr") == 5
    tb.done()


@cocotb.test()
async def test_stream_interpreter(dut):
    tb = await setup(dut, ".wrap_target\n pull\n mov exec, osr\n.wrap")
    for text in ("set x, 3", "mov y, !x", "mov isr, y", "push"):
        await tb.push(0, [instr(text)])
    assert await tb.pop(0) == [0xFFFC]
    tb.done()


@cocotb.test()
async def test_side_set(dut):
    tb = await setup(dut, ".side_set 2\n.pins side=5\n.init set pindirs, 3\n nop side 1\n nop side 2 [1]\n nop side 3",
                     set_base=5, set_count=2)
    pads = await capture(tb, 24)
    w5 = "".join(str((v >> 5) & 1) for v in pads)
    w6 = "".join(str((v >> 6) & 1) for v in pads)
    assert "1001100110011" in w5 and "0111011101110" in w6
    await tb.disable(1)
    await tb.load(".side_set 1 opt pindirs\n.pins side=11\n nop side 1\n nop\n nop side 0 [2]", 1, origin=10)
    await tb.enable(2)
    assert "1100011000" in await capture_oe(tb, 16, 0)
    tb.done()


@cocotb.test()
async def test_side_set_applies_while_stalled(dut):
    tb = await setup(dut, ".side_set 1\n.pins side=5\n.init set pindirs, 1\n pull side 1\n nop side 0",
                     set_base=5, set_count=1)
    await tb.cycles_(10)
    assert tb.pad(5) == 1 and await tb.stat(0) & 63 == 0
    tb.done()


@cocotb.test()
async def test_irq(dut):
    tb = TB(dut)
    await tb.start()
    await tb.load("irq wait 1 rel\n set x, 7\nh: jmp h", 1)
    await tb.load("wait 1 irq 2\n set y, 3\n irq 5\n irq clear 5\n irq 6\nh: jmp h", 0)
    await tb.enable(0b10)
    assert await tb.read1(R.addr("IRQ")) == 0b100  # SM1's relative flag 1 is flag 2
    await tb.enable(0b01)
    await tb.cycles_(10)
    assert await tb.read1(R.addr("IRQ")) == 0b1000000
    assert await tb.reg(1, "x") == 7 and await tb.reg(0, "y") == 3
    # Host force / clear and the interrupt pin.
    await tb.load("wait 1 irq 4\n irq 0\nh: jmp h", 0, origin=30)
    await tb.enable(1)
    assert int(dut.host_irq.value) == 0
    await tb.write(R.addr("INT_EN"), [0x0001])
    await tb.write(R.addr("IRQ_FORCE"), [0x10])
    assert await tb.read1(R.addr("IRQ")) & 0x11 == 0x01 and int(dut.host_irq.value) == 1
    await tb.write(R.addr("IRQ"), [0x41])
    assert int(dut.host_irq.value) == 0
    await tb.write(R.addr("INT_EN"), [0x0100])
    await tb.exec(0, "push")
    assert int(dut.host_irq.value) == 1
    tb.done()


@cocotb.test()
async def test_wait_pin_and_timeout(dut):
    src = """
.timeout 9
    wait 0 pin 2
    wait 1 pin 2 timeout
    jmp late
    set x, 1
h:  jmp h
late:
    set x, 2
    jmp h
"""
    tb = TB(dut)
    await tb.start()
    for k, (rise_after, expect) in enumerate(((6, 1), (40, 2))):
        await tb.load(src, k, origin=10 * k)
        await tb.enable(1 << k)
        await tb.cycles_(5)
        tb.set_in(2, 0)
        await tb.cycles_(rise_after)
        tb.set_in(2, 1)
        await tb.cycles_(40)
        assert await tb.reg(k, "x") == expect
        await tb.disable(1 << k)
    tb.done()


@cocotb.test()
async def test_timeout_counts_ticks(dut):
    tb = await setup(dut, ".timeout 4\n.pins set=5 set_count=1\n.init set pindirs, 1\n set pins, 1\n"
                     " wait 0 pin 0 timeout\n set pins, 0\nh: jmp h", clkdiv=2, enable=False)
    w = await capture_during(tb, 400, 5, tb.enable(1))
    assert "0" + "1" * 12 + "0" in w  # SET (1 tick) + 5 failed checks, 2 cycles per tick
    tb.done()


@cocotb.test()
async def test_virtual_pins_and_timestamp(dut):
    tb = await setup(dut, "wait 0 pin 19\n set x, 1\n wait 1 pin 19\n set x, 2\n wait 1 pin 28\n set x, 3\nh: jmp h")
    assert await tb.stat(0) & 63 == 0
    await tb.push(0, [1])
    assert await tb.reg(0, "x") == 1
    await tb.exec(0, "pull")
    assert await tb.reg(0, "x") == 2
    await tb.write(R.addr("IRQ_FORCE"), [0x10])
    assert await tb.reg(0, "x") == 3
    src = "wait 0 pin 0\n mov x, time\n wait 1 pin 0\n in time, 16\n push\n mov isr, x\n push\nh: jmp h"
    await tb.load(src, 1, origin=20)
    await tb.enable(2)
    await tb.cycles_(5)
    tb.set_in(0, 0)
    await tb.cycles_(100)
    tb.set_in(0, 1)
    await tb.cycles_(20)
    t1, t0 = await tb.pop(1, 2)
    assert (t1 - t0) & 0xFFFF == 100
    await tb.write(R.addr("TS_PRESCALE"), [9])
    await tb.write(R.addr("TIME"), [0])
    t = await tb.read1(R.addr("TIME"))
    assert 12 <= t <= 17  # ~150 cycles from the TIME write to the read latch, divided by 10
    tb.done()


@cocotb.test()
async def test_pin_priority_open_drain_filter(dut):
    tb = TB(dut)
    await tb.start()
    await tb.set_pins(0, 0x7FFE0)
    await tb.load(".pins set=5 set_count=2\n.wrap_target\n set pins, 1\n.wrap", 0)
    await tb.load(".pins set=6 set_count=2\n.wrap_target\n set pins, 0\n.wrap", 1)
    await tb.enable(0b11)
    assert (tb.pads() >> 5) & 7 == 0b001
    await tb.write(R.addr("PIN_OUT_LO"), [0xFFE0])
    assert (tb.pads() >> 5) & 0x3F == 0b111001
    await tb.disable(0b11)
    # Open drain: level 1 releases the pad, level 0 drives it low.
    await tb.set_open_drain(1 << 11)
    await tb.set_pins(0x7FFE0, 1 << 11)
    assert (int(dut.uio_out.value) & 1, int(dut.uio_oe.value) & 1) == (0, 0)
    tb.set_ext(11, 0)
    await tb.cycles_(4)
    assert await tb.read1(R.addr("PIN_IN_LO")) & (1 << 11) == 0  # another device pulls the bus low
    tb.set_ext(11, 1)
    await tb.write(R.addr("PIN_OUT_LO"), [0])
    assert (int(dut.uio_out.value) & 1, int(dut.uio_oe.value) & 1) == (0, 1)
    # The glitch filter hides pulses shorter than three cycles.
    await tb.set_filter(1 << 0)
    tb.set_in(0, 0)
    await tb.cycles_(2)
    tb.set_in(0, 1)
    await tb.cycles_(8)
    assert await tb.read1(R.addr("PIN_IN_LO")) & 1 == 1
    tb.set_in(0, 0)
    await tb.cycles_(8)
    assert await tb.read1(R.addr("PIN_IN_LO")) & 1 == 0
    tb.done()


@cocotb.test()
async def test_fifo_join_chain_and_host_errors(dut):
    tb = TB(dut)
    await tb.start()
    await tb.push(0, [1, 2, 3, 4, 5])
    assert await tb.read1(R.addr("FSTAT")) == 0x3021
    assert await tb.read1(R.addr("FDEBUG")) == 0x0001
    await tb.write(R.addr("FDEBUG"), [0x0001])
    assert await tb.pop(1) == [0]
    assert await tb.read1(R.addr("FDEBUG")) == 0x0020
    await tb.load(".fifo join_tx\n nop", 0)
    await tb.push(0, range(1, 10))
    assert await tb.tx_level(0) == 8
    await tb.load(".fifo join_rx\n.wrap_target\n set x, 3\n mov isr, x\n push\n.wrap", 1)
    await tb.enable(0b10)
    await tb.cycles_(60)
    assert await tb.rx_level(1) == 8
    await tb.load(".wrap_target\n pull\n mov isr, osr\n push\n.wrap", 1)
    await tb.load(".fifo chain=1\n.wrap_target\n pull\n mov isr, !osr\n push\n.wrap", 0)
    await tb.enable(0b11)
    await tb.push(1, [0x00FF, 0x1234])
    await tb.cycles_(30)
    assert await tb.pop(0, 2) == [0xFF00, 0xEDCB]
    tb.done()


@cocotb.test()
async def test_restart(dut):
    tb = await setup(dut, "set x, 5\n set y, 6\nh: jmp h", enable=False)
    await tb.exec(0, "set y, 9")
    await tb.write(R.addr("CTRL"), [0x10])
    assert await tb.reg(0, "y") == 0 and await tb.stat(0) & 63 == 0
    tb.done()


# ----------------------------------------------------------------------------- bit-level assist unit
@cocotb.test()
async def test_bau_crc16(dut):
    src = """
.bau poly=0xA001
.shift autopull=8
    mov crc, !null
    set bau, BAU_CRC
    set x, 31
l:  out null, bau
    jmp x-- l
    mov isr, !crc
    push
h:  jmp h
"""
    tb = await setup(dut, src, pushes=[0x00, 0x01, 0x02, 0x03])
    got = (await tb.pop(0))[0]
    assert int(f"{got:016b}"[::-1], 2) == 0xF75E  # USB CRC16 of 00 01 02 03
    tb.done()


@cocotb.test()
async def test_bau_nrzi_stuffing_loopback(dut):
    tx = """
.bau stuff=6 ones
.pins out=5 out_count=1
.shift autopull=16
.init set pins, 1
.init set pindirs, 1
.init set bau, BAU_RESET | BAU_NRZI | BAU_STUFF
.wrap_target
    out pins, bau [1]
.wrap
"""
    rx = """
.bau stuff=6 ones
.pins in=5
.shift autopush=16
.init set bau, BAU_RESET | BAU_NRZI | BAU_STUFF
    nop [1]
.wrap_target
    in pins, bau [1]
.wrap
"""
    tb = TB(dut)
    await tb.start()
    await tb.load(tx, 0, set_base=5, set_count=1)
    await tb.load(rx, 1)
    words = [0xFFFF, 0x7EFE, 0x0000, 0xFFFF]
    await tb.push(0, words)
    w = await capture_during(tb, 520, 5, tb.enable(0b11))
    line = w[w.index("0"):][::2][:50]  # from the first transition, one sample per bit
    assert "1" * 8 not in line and "0" * 8 not in line and "0" * 7 in line
    assert await tb.pop(1, 4) == words
    tb.done()


@cocotb.test()
async def test_bau_stuff_any_and_manchester(dut):
    src = """
.bau stuff=5 any
.pins out=5 out_count=1
.shift autopull=16 out=left
.init set pindirs, 1
.init set bau, BAU_RESET | BAU_STUFF
.wrap_target
    out pins, bau
.wrap
"""
    tb = await setup(dut, src, pushes=[0x0000, 0xFFFF], set_base=5, set_count=1, enable=False)
    w = await capture_during(tb, 400, 5, tb.enable(1))
    assert "00000100000100000101111101111101" in w
    await tb.disable(1)
    man = """
.pins out=11 out_count=2
.shift autopull=8
.init mov osr, !null
.init out pindirs, 2
.init set bau, BAU_RESET | BAU_MAN
.wrap_target
    out pins, bau
.wrap
"""
    await tb.load(man, 1, origin=20)
    await tb.push(1, [0b10110010])
    await tb.enable(2)
    w = await capture(tb, 40, 11)
    assert "10" + "01" + "10" + "10" + "01" + "01" + "10" + "01" in w
    tb.done()


@cocotb.test()
async def test_bau_destuff_error(dut):
    rx = """
.bau stuff=2 ones
.pins in=0
.init set bau, BAU_RESET | BAU_STUFF
.wrap_target
    in pins, bau
.wrap
"""
    tb = await setup(dut, rx)
    await tb.cycles_(10)
    await tb.disable(1)
    await tb.write(R.addr("ORG", 0), [R.pack("ORG", org=0, jmp_pin=R.VPIN_BERR)])
    await tb.exec(0, "jmp pin 5")
    assert await tb.stat(0) & 63 == 5
    tb.done()


@cocotb.test()
async def test_bau_stuff_bit_into_register(dut):
    """Closes the last functional-coverage hole: OUT X, BAU while a stuff bit is due.

    With STUFF_N = 1 every 1 is followed by a stuff 0. OUT with BAU writes the encoded pair
    {~level, level}: the data bit 1 gives 0b01, the stuff bit that follows gives 0b10, and the OSR does
    not shift for the stuff bit.
    """
    src = """
.bau stuff=1 ones
    pull
    set bau, BAU_RESET | BAU_STUFF
    out x, bau             ; data bit 1 -> X = 0b01
    mov isr, x
    push
    out x, bau             ; stuff bit 0 -> X = 0b10
    mov isr, x
    push
    out x, bau             ; next data bit 1 again
    mov isr, x
    push
h:  jmp h
"""
    tb = await setup(dut, src, pushes=[0xFFFF])
    assert await tb.pop(0, 3) == [0b01, 0b10, 0b01]
    tb.done()
