"""Instruction-level tests of the simulator. Each test states one rule of docs/isa.md."""

import pytest

from bitweaver import regmap as R
from bitweaver.asm import assemble_one, instr
from bitweaver.loader import Bench, FastDriver, SpiDriver


def setup(src, sm=0, pushes=(), enable=True, driver=FastDriver, **cfg):
    b = Bench(trace=True)
    d = driver(b)
    p = assemble_one(src)
    d.load(p, sm, **cfg)
    if pushes:
        d.push(sm, pushes)
    if enable:
        d.enable(1 << sm)
    return b, d, p


def reg(b, d, name, sm=0):
    """Read an engine register (x, y, isr, osr, crc) through the documented EXEC method."""
    d.exec(sm, instr(f"mov isr, {name}"))
    d.exec(sm, instr("push noblock"))
    return d.pop(sm)[0]


def wave(b, pin, last):
    return "".join(str((t >> pin) & 1) for t in b.trace[-last:])


# ----------------------------------------------------------------------------- host port
@pytest.mark.parametrize("driver", [FastDriver, SpiDriver])
def test_id_and_config_readback(driver):
    b = Bench()
    d = driver(b)
    assert d.read(R.addr("ID"), 2, inc=True) == [R.CHIP_ID, R.CHIP_VERSION]
    d.write(R.IMEM_BASE, [0x1234, 0xABCD, 0x5555], inc=True)
    assert d.read(R.IMEM_BASE, 3, inc=True) == [0, 0, 0]  # instruction memory is write only
    assert b.iss.imem[:3] == [0x1234, 0xABCD, 0x5555]
    for k in range(R.NUM_SM):
        for r in R.SM_REGS:
            if r.name in ("FIFO", "EXEC", "STAT"):
                continue
            assert d.read(R.addr(r.name, k)) == [r.reset]
            d.write(R.addr(r.name, k), [0xFFFF])
            assert d.read(R.addr(r.name, k)) == [r.mask], r.name
    d.write(R.addr("PIN_OUT_LO"), [0xFFFF, 0xFFFF], inc=True)
    assert d.read(R.addr("PIN_OUT_LO"), 2, inc=True) == [0xFFE0, 7]
    d.write(R.addr("PAD_OD_LO"), [0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF], inc=True)
    assert d.read(R.addr("PAD_OD_LO"), 4, inc=True) == [0xF800, 7, 0xF81F, 7]
    assert d.read(0x1F) == [0] and d.read(0x6E) == [0] and d.read(0xC0) == [0]
    assert d.read(R.SM_BASE + R.NUM_SM * R.SM_STRIDE) == [0]  # no engine behind this window
    # Words are read back through an engine instead.
    d.write(R.IMEM_BASE + 40, [0x7777])
    assert [d.read_imem(a, sm=1) for a in (0, 1, 2, 40)] == [0x1234, 0xABCD, 0x5555, 0x7777]


def test_spi_first_bytes_are_id_and_status():
    b = Bench()
    d = SpiDriver(b)
    d.read(R.addr("ID"))
    assert d.first_bytes == (R.SPI_ID_BYTE, 0x03)  # both TX FIFOs not full, both RX FIFOs empty
    d.push(1, [1, 2, 3, 4])
    d.exec(0, instr("push noblock"))
    d.read(R.addr("ID"))
    assert d.first_bytes == (R.SPI_ID_BYTE, 0x11)  # SM0: TX not full, RX not empty; SM1: TX full


def test_fifo_host_side():
    b = Bench()
    d = SpiDriver(b)
    d.push(0, [1, 2, 3, 4, 5])  # the fifth word overflows
    assert d.read(R.addr("FSTAT")) == [0x3021]  # SM0 TX full; SM1 TX empty; both RX empty
    assert d.read(R.addr("FDEBUG")) == [0x0001]
    d.write(R.addr("FDEBUG"), [0x0001])
    assert d.read(R.addr("FDEBUG")) == [0]
    assert d.pop(0) == [0]  # RX empty: reads 0 and flags an underflow
    assert d.read(R.addr("FDEBUG")) == [0x0010]
    assert d.tx_level(0) == 4
    d.write(R.addr("FIFOCTRL", 0), [0])  # any write clears the FIFOs
    assert d.tx_level(0) == 0


def test_spi_read_does_not_pop_unless_clocked():
    """Selecting the FIFO register without clocking a data bit leaves the FIFO alone."""
    from bitweaver.loader import frame_bytes
    b = Bench()
    d = SpiDriver(b)
    for v in (0x1111, 0x2222):
        d.exec(0, instr("set x, 0"))
        d.write(R.addr("EXEC", 0), [instr("mov isr, !x")])
        d.exec(0, instr("push"))
    d._frame(frame_bytes(R.addr("FIFO", 0), count=0, write=False))
    assert d.rx_level(0) == 2
    assert d.pop(0, 2) == [0xFFFF, 0xFFFF]
    assert d.rx_level(0) == 0


# ----------------------------------------------------------------------------- execution basics
def test_clock_divider_and_delay():
    b, d, _ = setup(".pins set=5 set_count=1\n.init set pindirs, 1\n set pins, 1 [1]\n set pins, 0",
                    clkdiv=3)
    b.run(60)
    assert "0001111110001111110" in wave(b, 5, 60)  # 2 ticks high, 1 tick low, 3 cycles per tick


def test_fractional_divider():
    b, d, _ = setup(".pins set=5 set_count=1\n.init set pindirs, 1\n set pins, 1\n set pins, 0", clkdiv=2.5)
    b.run(100)
    w = wave(b, 5, 100)
    assert "110001100011000" in w  # ticks alternate between 2 and 3 cycles
    assert w.count("1") == 40


def test_wrap_and_position_independence():
    src = ".pins set=5 set_count=1\n.init set pindirs, 1\n set x, 3\n.wrap_target\nl: set pins, 1\n " \
          "jmp x-- l\n set pins, 0 [3]\n.wrap"
    for origin in (0, 17, 60):
        b = Bench(trace=True)
        d = FastDriver(b)
        d.load(assemble_one(src), 0, origin=origin)
        d.enable(1)
        b.run(40)
        assert wave(b, 5, 44).lstrip("0").startswith("111111110000111111")
        assert d.read(R.addr("STAT", 0))[0] & 63 in (origin + 1, origin + 2, origin + 3)


def test_jmp_conditions():
    src = """
    set x, 2
    set y, 0
a:  jmp x-- a          ; runs 3 times, leaves x = 0xFFFF
    jmp !y b
    set y, 31          ; skipped
b:  jmp !x c           ; not taken
    jmp x!=y d
c:  set y, 30          ; skipped
d:  jmp y-- e          ; y = 0, not taken, y becomes 0xFFFF
    mov isr, y
e:  jmp pin f          ; pin 0 is high
    set x, 1           ; skipped
f:  jmp !osre g        ; OSR is empty after restart: not taken
    push
g:  jmp g
"""
    b, d, _ = setup(src, jmp_pin=0)
    b.run(40)
    assert d.pop(0) == [0xFFFF]
    assert reg(b, d, "x") == 0xFFFF


def test_in_out_shift_directions():
    b, d, _ = setup("pull\n out x, 4\n out y, 8\n in y, 4\n in x, 8\n push\nh: jmp h", pushes=[0xABCD])
    b.run(30)
    assert d.pop(0) == [0x0DC0]  # right shifts: x=0xD, y=0xBC; ISR fills from the top
    b, d, _ = setup("pull\n out x, 4\n out y, 8\n in y, 4\n in x, 8\n push\nh: jmp h", pushes=[0xABCD],
                    in_shiftdir=0, out_shiftdir=0)
    b.run(30)
    assert d.pop(0) == [0x0C0A]  # left shifts: x=0xA, y=0xBC; ISR = (0xC << 8) | 0x0A


def test_autopull_autopush():
    src = ".shift autopull=8 autopush=8\n.wrap_target\n out x, 4\n in x, 4\n.wrap"
    b, d, _ = setup(src, pushes=[0x21, 0x43])
    b.run(40)
    assert d.pop(0, 2) == [0x2100, 0x4300]
    assert d.read(R.addr("FDEBUG"))[0] & 0x0100  # stalled on the empty TX FIFO afterwards


def test_autopush_stalls_when_full():
    b, d, _ = setup(".shift autopush=4\n.wrap_target\n set x, 5\n in x, 4\n.wrap")
    b.run(60)
    assert d.rx_level(0) == 4
    assert d.read(R.addr("FDEBUG"))[0] & 0x1000
    assert d.pop(0, 4) == [0x5000] * 4


def test_push_pull_variants():
    src = """
    pull noblock       ; TX empty: OSR = X (0)
    set x, 9
    pull noblock       ; OSR = 9
    mov isr, osr
    push
    push iffull        ; ISR count 0 < 16: no-op
    in null, 16
    push iffull        ; pushes 0
    pull ifempty       ; OSR count 0: no-op
    out null, 16
    pull ifempty       ; blocks until the host pushes
    mov isr, osr
    push
h:  jmp h
"""
    b, d, _ = setup(src)
    b.run(40)
    assert d.pop(0, 2) == [9, 0]
    assert d.read(R.addr("STAT", 0))[0] & 63 == 10
    d.push(0, [0x7777])
    b.run(10)
    assert d.pop(0) == [0x7777]


def test_push_noblock_drops_when_full():
    b, d, _ = setup(".wrap_target\n set x, 1\n mov isr, x\n push noblock\n.wrap")
    b.run(60)
    assert d.rx_level(0) == 4
    assert d.read(R.addr("FDEBUG"))[0] & 0x1000


def test_mov_ops_and_sources():
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
    b, d, _ = setup(src, pushes=[0x12C4])
    b.run(40)
    assert d.pop(0, 4) == [0xED3B, 0x2348, 0xC412, 0]


def test_mov_pins_and_in_pins():
    b = Bench()
    d = FastDriver(b)
    b.ui_pins = 0b10110
    p = assemble_one(".pins in=1 out=5 out_count=6\n.init mov osr, !null\n.init out pindirs, 6\n"
                     " mov x, pins\n mov pins, x\n in pins, 3\n push\nh: jmp h")
    d.load(p, 0)
    d.enable(1)
    b.run(20)
    # pins 1..4 = 1011b, pins 5.. read back the outputs just written
    assert (b.pads() >> 5) & 0x3F == 0b001011
    assert d.pop(0)[0] >> 13 == 0b011


def test_set_destinations():
    src = ".pins set=11 set_count=3\n set pindirs, 7\n set pins, 5\n set x, 31\n set y, 17\nh: jmp h"
    b, d, _ = setup(src)
    b.run(20)
    assert (b.out[1], b.out[2]) == (0b101, 0b111)
    assert (reg(b, d, "x"), reg(b, d, "y")) == (31, 17)


def test_out_destinations():
    src = """
.pins out=11 out_count=4
    pull
    out pindirs, 4     ; 0xF
    out pins, 4        ; 0xA
    out isr, 4         ; ISR = 3, count 4
    out null, 4
    pull
    out pc, 5          ; jump to 9
    set x, 1           ; skipped
    set x, 2           ; skipped
    in null, 12        ; count 16
    push iffull
h:  jmp h
"""
    b, d, _ = setup(src, pushes=[0x03AF, 9], in_shiftdir=0)
    b.run(40)
    assert (b.out[1], b.out[2]) == (0xA, 0xF)
    assert d.pop(0) == [0x3000]
    assert reg(b, d, "x") == 0


def test_exec_from_program_and_host():
    b, d, _ = setup("pull\n out exec, 16\n mov isr, x\n push\nh: jmp h", pushes=[instr("set x, 21")])
    b.run(20)
    assert d.pop(0) == [21]
    # Host EXEC works on a disabled engine and does not advance the PC.
    b, d, _ = setup("set x, 1\n set x, 2", enable=False)
    d.exec(0, instr("set y, 9"))
    assert d.read(R.addr("STAT", 0))[0] & 0x7F == 0
    assert reg(b, d, "y") == 9
    d.exec(0, instr("jmp 1"))
    assert d.read(R.addr("STAT", 0))[0] & 63 == 1
    # A stalled injected instruction stays pending.
    d.exec(0, instr("pull block"))
    assert d.read(R.addr("STAT", 0))[0] & 0x40
    d.push(0, [5])
    assert not d.read(R.addr("STAT", 0))[0] & 0x40
    assert reg(b, d, "osr") == 5


def test_stream_interpreter():
    """A two-instruction program executes an instruction stream from the TX FIFO."""
    b, d, _ = setup(".wrap_target\n pull\n mov exec, osr\n.wrap", enable=True)
    for text in ("set x, 3", "mov y, !x", "mov isr, y", "push"):
        d.push(0, [instr(text)])
        b.run(6)
    assert d.pop(0) == [0xFFFC]


def test_side_set():
    src = ".side_set 2\n.pins side=5\n.init set pindirs, 3\n nop side 1\n nop side 2 [1]\n nop side 3"
    b, d, _ = setup(src, set_base=5, set_count=2)
    b.run(24)
    assert "1001100110011" in wave(b, 5, 16)  # bit 0 of side values 1, 2, 2 (delay), 3
    assert "0111011101110" in wave(b, 6, 16)  # bit 1
    # Optional side-set leaves the pins alone; pindirs mode drives directions.
    src = ".side_set 1 opt pindirs\n.pins side=11\n nop side 1\n nop\n nop side 0 [2]"
    b, d, _ = setup(src)
    oe = ""
    for _ in range(16):
        b.step()
        oe += str(b.out[2] & 1)
    assert "1100011000" in oe


def test_side_set_applies_while_stalled():
    src = ".side_set 1\n.pins side=5\n.init set pindirs, 1\n pull side 1\n nop side 0"
    b, d, _ = setup(src, set_base=5, set_count=1)
    b.run(10)
    assert b.pin(5) == 1 and d.read(R.addr("STAT", 0))[0] & 63 == 0


def test_irq_set_wait_clear_and_rel():
    b = Bench()
    d = FastDriver(b)
    d.load(assemble_one("irq wait 1 rel\n set x, 7\nh: jmp h"), 1)  # flag (1+1)&3 = 2
    d.load(assemble_one("wait 1 irq 2\n set y, 3\n irq 5\n irq clear 5\n irq 6\nh: jmp h"), 0)
    d.enable(0b10)
    b.run(6)
    assert d.read(R.addr("IRQ")) == [0b100]
    assert d.read(R.addr("STAT", 1))[0] & 63 == 0  # still waiting for flag 2 to clear
    d.enable(0b01)
    b.run(10)
    assert d.read(R.addr("IRQ")) == [0b1000000]  # 2 cleared by the wait, 5 set then cleared, 6 set
    assert reg(b, d, "x", 1) == 7 and reg(b, d, "y", 0) == 3


def test_irq_host_force_clear_and_int_pin():
    b, d, _ = setup("wait 1 irq 4\n irq 0\nh: jmp h")
    b.run(5)
    assert b.host_irq == 0
    d.write(R.addr("INT_EN"), [0x0001])
    d.write(R.addr("IRQ_FORCE"), [0x10])
    b.run(4)
    assert d.read(R.addr("IRQ")) == [0x01] and b.host_irq == 1
    assert d.read(R.addr("INT_STAT"))[0] == 0x3001  # IRQ flag 0, both TX FIFOs not full
    d.write(R.addr("IRQ"), [0x01])
    assert b.host_irq == 0
    d.write(R.addr("INT_EN"), [0x0100])  # RX FIFO 0 not empty
    d.exec(0, instr("push"))
    assert b.host_irq == 1


def test_wait_pin_and_timeout():
    src = """
.timeout 9
    wait 0 pin 2             ; PIN2 idles high
    wait 1 pin 2 timeout     ; success skips the next instruction
    jmp late
    set x, 1
h:  jmp h
late:
    set x, 2
    jmp h
"""
    for rise_after, expect in ((6, 1), (40, 2)):
        b, d, _ = setup(src, in_base=0)
        b.run(5)
        b.ui_pins &= ~4
        b.run(rise_after)
        b.ui_pins |= 4
        b.run(40)
        assert reg(b, d, "x") == expect


def test_timeout_counts_ticks():
    b, d, _ = setup(".timeout 4\n.pins set=5 set_count=1\n.init set pindirs, 1\n set pins, 1\n"
                    " wait 0 pin 0 timeout\n set pins, 0\nh: jmp h", clkdiv=2)
    b.run(40)
    assert "0" + "1" * 12 + "0" in wave(b, 5, 44)  # set(1 tick) + 5 failed checks, 2 cycles each


def test_virtual_pins():
    b, d, _ = setup("wait 0 pin 19\n set x, 1\n wait 1 pin 19\n set x, 2\n wait 1 pin 28\n set x, 3\nh: jmp h")
    b.run(10)
    assert d.read(R.addr("STAT", 0))[0] & 63 == 0  # TX FIFO empty: virtual pin 19 is high
    d.push(0, [1])
    b.run(5)
    assert reg(b, d, "x") == 1
    d.exec(0, instr("pull"))
    b.run(5)
    assert reg(b, d, "x") == 2
    d.write(R.addr("IRQ_FORCE"), [0x10])  # IRQ flag 4 is virtual pin 28
    b.run(5)
    assert reg(b, d, "x") == 3


def test_timestamp():
    src = "wait 0 pin 0\n mov x, time\n wait 1 pin 0\n in time, 16\n push\n mov isr, x\n push\nh: jmp h"
    b, d, _ = setup(src)
    b.run(5)
    b.ui_pins &= ~1
    b.run(100)
    b.ui_pins |= 1
    b.run(20)
    t1, t0 = d.pop(0, 2)
    assert (t1 - t0) & 0xFFFF == 100
    d.write(R.addr("TS_PRESCALE"), [9])
    d.write(R.addr("TIME"), [0])
    b.run(100)
    assert d.read(R.addr("TIME"))[0] in (10, 11)


def test_pin_priority_between_engines_and_host():
    b = Bench()
    d = FastDriver(b)
    d.set_pins(0, 0x7FFE0)
    d.load(assemble_one(".pins set=5 set_count=2\n.wrap_target\n set pins, 1\n.wrap"), 0)
    d.load(assemble_one(".pins set=6 set_count=2\n.wrap_target\n set pins, 0\n.wrap"), 1)
    d.enable(0b11)
    b.run(4)
    assert (b.pads() >> 5) & 7 == 0b001  # pin 6: engine 1 beats engine 0
    d.write(R.addr("PIN_OUT_LO"), [0xFFE0])
    assert (b.pads() >> 5) & 0x3F == 0b111001  # engines beat the host on pins 5..7


def test_open_drain_and_filter():
    b = Bench()
    d = FastDriver(b)
    d.set_open_drain(1 << 11)
    d.set_pins(0x7FFE0, 1 << 11)
    assert (b.out[1] & 1, b.out[2] & 1) == (0, 0)  # level 1 in open-drain mode: released
    d.write(R.addr("PIN_OUT_LO"), [0])
    assert (b.out[1] & 1, b.out[2] & 1) == (0, 1)  # level 0: driven low
    # The glitch filter hides pulses shorter than three cycles.
    d.set_filter(1 << 0)
    b.ui_pins &= ~1
    b.run(2)
    b.ui_pins |= 1
    b.run(8)
    assert d.read(R.addr("PIN_IN_LO"))[0] & 1 == 1
    b.ui_pins &= ~1
    b.run(8)
    assert d.read(R.addr("PIN_IN_LO"))[0] & 1 == 0


def test_fifo_join_and_chain():
    b = Bench()
    d = FastDriver(b)
    d.load(assemble_one(".fifo join_tx\n nop"), 0)
    d.push(0, range(1, 10))
    assert d.tx_level(0) == 8 and d.read(R.addr("FSTAT"))[0] & 0x0101 == 0x0101
    d.load(assemble_one(".fifo join_rx\n.wrap_target\n set x, 3\n mov isr, x\n push\n.wrap"), 1)
    d.enable(0b10)
    b.run(60)
    assert d.rx_level(1) == 8
    # Engine 0 inverts what engine 1 relays: 1's RX FIFO feeds 0's TX FIFO.
    d.load(assemble_one(".wrap_target\n pull\n mov isr, osr\n push\n.wrap"), 1)
    d.load(assemble_one(".fifo chain=1\n.wrap_target\n pull\n mov isr, !osr\n push\n.wrap"), 0)
    d.enable(0b11)
    d.push(1, [0x00FF, 0x1234])
    b.run(30)
    assert d.pop(0, 2) == [0xFF00, 0xEDCB]


def test_restart():
    b, d, _ = setup("set x, 5\n set y, 6\nh: jmp h")
    b.run(10)
    d.write(R.addr("CTRL"), [0x11])
    b.run(1)
    d.disable(1)
    assert reg(b, d, "y") in (0, 6)
    b2, d2, _ = setup("set x, 5\nh: jmp h", enable=False)
    d2.exec(0, instr("set y, 9"))
    d2.write(R.addr("CTRL"), [0x10])
    assert reg(b2, d2, "y") == 0 and d2.read(R.addr("STAT", 0))[0] & 63 == 0


# ----------------------------------------------------------------------------- bit-level assist unit
def crc_ref(bits, poly, init=0):
    crc = init
    for bit in bits:
        fb = (crc ^ bit) & 1
        crc = (crc >> 1) ^ (poly if fb else 0)
    return crc


def test_bau_crc16_usb():
    data = [0x00, 0x01, 0x02, 0x03]  # USB spec example: CRC16 = 0xF75E (bit-reversed on the wire)
    src = f"""
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
    b, d, _ = setup(src, pushes=data)
    b.run(120)
    bits = [(byte >> i) & 1 for byte in data for i in range(8)]
    got = d.pop(0)[0]
    assert got == crc_ref(bits, 0xA001, 0xFFFF) ^ 0xFFFF
    assert int(f"{got:016b}"[::-1], 2) == 0xF75E


def test_bau_nrzi_and_stuffing_tx_rx_loopback():
    """Engine 0 sends NRZI with bit stuffing on PIN5; engine 1 decodes it from the same pin."""
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
    nop [1]                 ; skip the idle level; the first sample sees the first sent bit
.wrap_target
    in pins, bau [1]
.wrap
"""
    b = Bench(trace=True)
    d = FastDriver(b)
    d.load(assemble_one(tx), 0, set_base=5, set_count=1)
    d.load(assemble_one(rx), 1)
    words = [0xFFFF, 0x7EFE, 0x0000, 0xFFFF]
    d.push(0, words)
    d.enable(0b11)  # both engines start on the same cycle
    start = b.cycle
    for _ in range(400):
        b.step()
        if len(b.iss.sm[1].rx) == 4:
            break
    assert not b.iss.sm[1].berr
    # 16 ones in a row were sent, yet the line never holds one level for more than 7 bit times.
    line = wave(b, 5, b.cycle - start)[::2]
    assert d.pop(1, 4) == words
    assert "1" * 8 not in line and "0" * 8 not in line and "1" * 7 in line


def test_bau_stuff_any_polarity_can_style():
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
    b, d, _ = setup(src, pushes=[0x0000, 0xFFFF], set_base=5, set_count=1)
    b.run(60)
    w = wave(b, 5, 62)
    # 16 zeros then 16 ones with a complement bit after every five equal bits
    stream = w[w.index("1") - 5:]
    assert stream.startswith("00000" + "1" + "00000" + "1" + "00000" + "1" + "0" + "11111" + "0" + "11111" + "0")


def test_bau_manchester():
    src = """
.pins out=5 out_count=2
.shift autopull=8
.init mov osr, !null
.init out pindirs, 2
.init set bau, BAU_RESET | BAU_MAN
.wrap_target
    out pins, bau
.wrap
"""
    b, d, _ = setup(src, pushes=[0b10110010])
    b.run(40)
    w = wave(b, 5, 44)
    # LSB first, IEEE 802.3: 0 -> high,low ; 1 -> low,high
    assert "10" + "01" + "10" + "10" + "01" + "01" + "10" + "01" in w
    assert "01" + "10" + "01" + "01" + "10" + "10" + "01" + "10" in wave(b, 6, 44)  # complement pin


def test_bau_destuff_error_flag():
    rx = """
.bau stuff=2 ones
.pins in=0
.init set bau, BAU_RESET | BAU_STUFF
.wrap_target
    in pins, bau
.wrap
"""
    b, d, _ = setup(rx, enable=False)
    b.ui_pins = 0x1F  # constant ones: the third bit should have been a 0 stuff bit
    d.enable(1)
    b.run(10)
    assert b.iss.sm[0].berr == 1
    d.exec(0, instr("jmp pin 0"))
