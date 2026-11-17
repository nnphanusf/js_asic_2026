"""Assembler and disassembler unit tests."""

import pytest

from bitweaver import regmap as R
from bitweaver.asm import AsmError, NOP, assemble, assemble_one, disasm, instr


def test_encodings():
    assert instr("jmp 5") == 0x0005
    assert instr("jmp !x 3") == 0x0023
    assert instr("jmp x-- 31") == 0x005F
    assert instr("jmp !osre 0") == 0x00E0
    assert instr("wait 1 pin 3") == 0x2083
    assert instr("wait 0 pin 31 timeout") == 0x205F
    assert instr("wait 1 irq 2 rel") == 0x20B2
    assert instr("in pins, 8") == 0x4008
    assert instr("in pins, 16") == 0x4000
    assert instr("in time, 16") == 0x4080
    assert instr("in pins, bau") == 0x4010
    assert instr("out pins, 1") == 0x6001
    assert instr("out exec, 16") == 0x60E0
    assert instr("out pins, bau") == 0x6010
    assert instr("push") == 0x8020
    assert instr("push iffull noblock") == 0x8040
    assert instr("pull") == 0x80A0
    assert instr("pull ifempty noblock") == 0x80C0
    assert instr("mov x, y") == 0xA022
    assert instr("mov x, !y") == 0xA02A
    assert instr("mov isr, ::osr") == 0xA0D7
    assert instr("mov y, <>x") == 0xA059
    assert instr("mov crc, !null") == 0xA06B
    assert instr("irq 3") == 0xC003
    assert instr("irq wait 1 rel") == 0xC031
    assert instr("irq clear 7") == 0xC047
    assert instr("set pins, 31") == 0xE01F
    assert instr("set bau, 22") == 0xE076
    assert instr("set pindirs, 1") == 0xE081
    assert instr("nop") == NOP == 0xA042
    assert instr("set x, 1 [31]") == 0xFF21


def test_side_set_and_delay():
    p = assemble_one(".side_set 2\n nop side 3 [7]\n jmp 0 side 1")
    assert p.code == [NOP | (0b11111 << 8), (0b01000 << 8)]
    p = assemble_one(".side_set 1 opt\n nop side 1 [7]\n nop [5]")
    assert p.code == [NOP | (0b11111 << 8), NOP | (0b00101 << 8)]
    with pytest.raises(AsmError):
        assemble_one(".side_set 2\n nop side 0 [8]")
    with pytest.raises(AsmError):
        assemble_one(".side_set 1\n nop")  # non-optional side-set needs a value
    with pytest.raises(AsmError):
        assemble_one("nop side 1")


def test_labels_wrap_defines_entry():
    progs = assemble("""
.define GLOBAL 3
.program a
.define N GLOBAL + 4
start:
    set x, N          ; comment
.wrap_target
loop: jmp x-- loop    // another comment
    jmp start
.wrap
public done:
    nop
.entry loop
.program b
    jmp GLOBAL
""")
    a = progs["a"]
    assert a.code[0] == instr("set x, 7")
    assert a.code[1] == instr("jmp x-- 1")
    assert (a.wrap_target, a.wrap, a.entry) == (1, 2, 1)
    assert a.labels == {"start": 0, "loop": 1, "done": 3}
    assert progs["b"].code == [instr("jmp 3")]
    assert progs["b"].wrap == 0


def test_directives():
    p = assemble_one("""
.pins in=11 out=12 set=13 side=14 jmp=15 out_count=2 set_count=3 od=13,14 filter=11
.shift in=left out=right autopush=8 pull_thresh=12
.clkdiv 32.5
.fifo join_tx chain=2
.timeout 1000
.bau poly=0xA001 stuff=6 ones
.init set pindirs, 1
    nop
""")
    assert p.config == {
        "in_base": 11, "out_base": 12, "set_base": 13, "side_base": 14, "jmp_pin": 15, "out_count": 2,
        "set_count": 3, "od_pins": [13, 14], "filter_pins": [11], "in_shiftdir": 0, "out_shiftdir": 1,
        "autopush": 1, "push_thresh": 8, "pull_thresh": 12, "clkdiv": 32.5, "join_tx": 1, "chain_src": 2,
        "tmo": 1000, "bau_poly": 0xA001, "stuff_n": 6, "stuff_any": 0,
    }
    assert p.init == [instr("set pindirs, 1")]


@pytest.mark.parametrize("bad", [
    "bogus x", "jmp 32", "jmp maybe 1", "in pins, 0", "in pins, 17", "out nowhere, 1", "set x, 32",
    "set isr, 1", "wait 2 pin 0", "wait 1 gpio 0", "irq 8", "mov x", "push ifempty", ".unknown 1",
    "nop [32]", "jmp nolabel",
])
def test_errors(bad):
    with pytest.raises(AsmError):
        assemble_one(bad)


def test_program_length_and_jump_reach():
    """Programs may be up to 64 instructions long, but jump targets must be in the first 32."""
    with pytest.raises(AsmError):
        assemble_one("\n".join(["nop"] * 65))
    assert len(assemble_one("\n".join(["nop"] * 64)).code) == 64
    assert assemble_one("\n".join(["nop"] * 40) + "\n jmp 31").code[-1] == instr("jmp 31")
    with pytest.raises(AsmError):
        assemble_one("\n".join(["nop"] * 40) + "\nfar: nop\n jmp far")


def test_disasm_roundtrip():
    """Every 16-bit word disassembles to text that assembles back to an equivalent word."""
    for side_count, opt in ((0, False), (2, False), (1, True)):
        for word in range(0, 0x10000, 7):
            text = disasm(word, side_count, opt)
            if "reserved" in text:
                continue
            again = instr(text, side_count, opt)
            assert disasm(again, side_count, opt) == text, (hex(word), text)


def test_json_roundtrip():
    from bitweaver.asm import Program
    p = assemble_one(".side_set 1 opt pindirs\n.pins out=5\n.init set x, 1\n a: nop side 1\n jmp a")
    q = Program.from_json(p.to_json())
    assert (q.code, q.wrap, q.side_count, q.side_opt, q.side_pindirs, q.config, q.init) == \
           (p.code, p.wrap, p.side_count, p.side_opt, p.side_pindirs, p.config, p.init)
    assert p.hex() == "b842\n0000\n"


def test_regmap_generated_files_are_current():
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    with open(os.path.join(root, "src", "bw_defs.vh")) as fh:
        assert fh.read() == R.gen_vh(), "run `make -C sw gen`"
    with open(os.path.join(root, "docs", "regmap.md")) as fh:
        assert fh.read() == R.gen_md(), "run `make -C sw gen`"


def test_regmap_consistency():
    seen = set()
    for r in R.GLOBAL_REGS:
        assert r.addr < R.SM_BASE and r.addr not in seen
        seen.add(r.addr)
    for r in R.SM_REGS:
        assert r.addr < R.SM_STRIDE
    for r in R.GLOBAL_REGS + R.SM_REGS:
        used = 0
        for f in r.fields:
            assert f.msb < 16 and not used & f.mask, (r.name, f.name)
            used |= f.mask
    assert R.SM_BASE + R.NUM_SM * R.SM_STRIDE <= R.IMEM_BASE
    assert R.pack("WRAP", bottom=3, top=9) == 0x0903
