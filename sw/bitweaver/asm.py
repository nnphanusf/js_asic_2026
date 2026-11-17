"""Assembler and disassembler for Bitweaver programs.

    python3 -m bitweaver.asm prog.bw            # prints a listing
    python3 -m bitweaver.asm prog.bw -o out/    # writes out/<name>.json and out/<name>.hex

The syntax follows the familiar PIO style; docs/isa.md is the reference. A source file holds one or
more programs, each started by `.program <name>`. Programs are position independent: jump targets
are encoded relative to the program's first instruction, and the engine's ORG register relocates
them at run time.
"""

from __future__ import annotations

import argparse
import ast
import json
import operator
import os
import re
from dataclasses import dataclass, field

from . import regmap as R


class AsmError(Exception):
    def __init__(self, msg: str, line: int | None = None, text: str = ""):
        super().__init__(f"line {line}: {msg}: {text.strip()}" if line else msg)


# ------------------------------------------------------------------------------------------------
# Instruction encoders (also usable directly from Python)
# ------------------------------------------------------------------------------------------------
def _field(op: int, arg: int, ds: int = 0) -> int:
    return ((op & 7) << 13) | ((ds & 31) << 8) | (arg & 0xFF)


def enc_jmp(cond: int, addr: int, ds: int = 0) -> int:
    return _field(R.OP_JMP, (cond << 5) | (addr & 31), ds)


def enc_wait(pol: int, src_irq: bool, idx: int, timed: bool = False, ds: int = 0) -> int:
    return _field(R.OP_WAIT, (pol << 7) | (int(timed) << 6) | (int(src_irq) << 5) | (idx & 31), ds)


def enc_in(src: int, count: int = 1, bau: bool = False, ds: int = 0) -> int:
    return _field(R.OP_IN, (src << 5) | (0x10 if bau else count & 15), ds)


def enc_out(dst: int, count: int = 1, bau: bool = False, ds: int = 0) -> int:
    return _field(R.OP_OUT, (dst << 5) | (0x10 if bau else count & 15), ds)


def enc_push(iffull: bool = False, block: bool = True, ds: int = 0) -> int:
    return _field(R.OP_PUSHPULL, (int(iffull) << 6) | (int(block) << 5), ds)


def enc_pull(ifempty: bool = False, block: bool = True, ds: int = 0) -> int:
    return _field(R.OP_PUSHPULL, 0x80 | (int(ifempty) << 6) | (int(block) << 5), ds)


def enc_mov(dst: int, src: int, op: int = R.MOVOP_NONE, ds: int = 0) -> int:
    return _field(R.OP_MOV, (dst << 5) | (op << 3) | src, ds)


def enc_irq(idx: int, clear: bool = False, wait: bool = False, rel: bool = False, ds: int = 0) -> int:
    return _field(R.OP_IRQ, (int(clear) << 6) | (int(wait) << 5) | (int(rel) << 4) | (idx & 7), ds)


def enc_set(dst: int, value: int, ds: int = 0) -> int:
    return _field(R.OP_SET, (dst << 5) | (value & 31), ds)


NOP = enc_mov(R.MOV_Y, R.SRC_Y)


# ------------------------------------------------------------------------------------------------
# Program container
# ------------------------------------------------------------------------------------------------
@dataclass
class Program:
    name: str
    code: list[int] = field(default_factory=list)
    wrap_target: int = 0
    wrap: int = 0
    entry: int = 0
    side_count: int = 0
    side_opt: bool = False
    side_pindirs: bool = False
    config: dict = field(default_factory=dict)  # optional defaults from directives
    init: list[int] = field(default_factory=list)  # instructions to execute before enabling
    labels: dict = field(default_factory=dict)
    defines: dict = field(default_factory=dict)
    listing: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "name": self.name, "code": self.code, "wrap_target": self.wrap_target, "wrap": self.wrap,
            "entry": self.entry,
            "side_set": {"count": self.side_count, "opt": self.side_opt, "pindirs": self.side_pindirs},
            "config": self.config, "init": self.init, "labels": self.labels, "defines": self.defines,
        }

    @staticmethod
    def from_json(d: dict) -> "Program":
        s = d.get("side_set", {})
        return Program(d["name"], list(d["code"]), d.get("wrap_target", 0), d.get("wrap", len(d["code"]) - 1),
                       d.get("entry", 0), s.get("count", 0), s.get("opt", False), s.get("pindirs", False),
                       dict(d.get("config", {})), list(d.get("init", [])), dict(d.get("labels", {})),
                       dict(d.get("defines", {})))

    def hex(self) -> str:
        return "".join(f"{w:04x}\n" for w in self.code)


# ------------------------------------------------------------------------------------------------
# Expression evaluation
# ------------------------------------------------------------------------------------------------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.FloorDiv: operator.floordiv,
        ast.Div: operator.floordiv, ast.Mod: operator.mod, ast.LShift: operator.lshift,
        ast.RShift: operator.rshift, ast.BitOr: operator.or_, ast.BitAnd: operator.and_,
        ast.BitXor: operator.xor}


def _eval(expr: str, names: dict) -> int:
    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise AsmError(f"unknown symbol '{node.id}'")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            return ~ev(node.operand)
        raise AsmError(f"bad expression '{expr}'")

    try:
        return ev(ast.parse(expr.strip(), mode="eval").body)
    except SyntaxError:
        raise AsmError(f"bad expression '{expr}'") from None


# ------------------------------------------------------------------------------------------------
# Assembler
# ------------------------------------------------------------------------------------------------
_COND = {n: i for i, n in enumerate(R.JMP_CONDS)}
_SRC = {n: i for i, n in enumerate(R.SOURCES)}
_OUT = {n: i for i, n in enumerate(R.OUT_DESTS)}
_MOV = {n: i for i, n in enumerate(R.MOV_DESTS)}
_SET = {n: i for i, n in enumerate(R.SET_DESTS) if n}
_KV = re.compile(r"(\w+)\s*=\s*(\S+)")


def _strip(line: str) -> str:
    for mark in (";", "//"):
        if mark in line:
            line = line[: line.index(mark)]
    return line.strip()


class _Asm:
    def __init__(self, name: str, globals_: dict):
        self.p = Program(name)
        self.p.defines = dict(globals_)
        self.pending: list[tuple[int, str, str]] = []  # (line number, raw text, instruction text)
        self.inits: list[tuple[int, str, str]] = []
        self.wrap_set = False

    # ---- directives
    def directive(self, n: int, raw: str, text: str) -> None:
        parts = text.split()
        d, args = parts[0].lower(), parts[1:]
        p = self.p
        names = p.defines
        kv = {k.lower(): v for k, v in _KV.findall(text)}
        flags = [a.lower() for a in args if "=" not in a]
        try:
            if d == ".define":
                p.defines[args[0]] = _eval(" ".join(args[1:]), names)
            elif d == ".side_set":
                p.side_count = _eval(args[0], names)
                p.side_opt = "opt" in flags
                p.side_pindirs = "pindirs" in flags
                if not 0 <= p.side_count <= 3:
                    raise AsmError("side-set count must be 0..3")
            elif d == ".wrap_target":
                p.wrap_target = len(self.pending)
            elif d == ".wrap":
                p.wrap = len(self.pending) - 1
                self.wrap_set = True
            elif d == ".entry":
                p.config["_entry"] = args[0]
            elif d == ".pins":
                for k in ("in", "out", "set", "side", "jmp", "out_count", "set_count"):
                    if k in kv:
                        p.config[{"in": "in_base", "out": "out_base", "set": "set_base", "side": "side_base",
                                  "jmp": "jmp_pin"}.get(k, k)] = _eval(kv.pop(k), names)
                if "od" in kv:
                    p.config["od_pins"] = [_eval(x, names) for x in kv.pop("od").split(",")]
                if "filter" in kv:
                    p.config["filter_pins"] = [_eval(x, names) for x in kv.pop("filter").split(",")]
                if kv:
                    raise AsmError(f"unknown .pins option '{next(iter(kv))}'")
            elif d == ".shift":
                for k, v in kv.items():
                    if k in ("in", "out"):
                        if v.lower() not in ("left", "right"):
                            raise AsmError("shift direction must be left or right")
                        p.config[f"{k}_shiftdir"] = 1 if v.lower() == "right" else 0
                    elif k in ("autopush", "autopull"):
                        p.config[k] = 1
                        p.config["push_thresh" if k == "autopush" else "pull_thresh"] = _eval(v, names)
                    elif k in ("push_thresh", "pull_thresh"):
                        p.config[k] = _eval(v, names)
                    else:
                        raise AsmError(f"unknown .shift option '{k}'")
            elif d == ".clkdiv":
                p.config["clkdiv"] = float(args[0])
            elif d == ".fifo":
                for f in flags:
                    if f not in ("join_tx", "join_rx"):
                        raise AsmError(f"unknown .fifo option '{f}'")
                    p.config[f] = 1
                if "chain" in kv:
                    p.config["chain_src"] = _eval(kv["chain"], names)
            elif d == ".timeout":
                p.config["tmo"] = _eval(" ".join(args), names)
            elif d == ".bau":
                if "poly" in kv:
                    p.config["bau_poly"] = _eval(kv["poly"], names)
                if "stuff" in kv:
                    p.config["stuff_n"] = _eval(kv["stuff"], names)
                if "any" in flags:
                    p.config["stuff_any"] = 1
                if "ones" in flags:
                    p.config["stuff_any"] = 0
            elif d == ".init":
                self.inits.append((n, raw, text[len(".init"):].strip()))
            else:
                raise AsmError("unknown directive")
        except (IndexError, ValueError):
            raise AsmError("bad directive", n, raw) from None
        except AsmError as e:
            raise AsmError(str(e), n, raw) from None

    # ---- instructions
    def _ds(self, side: str | None, delay: str | None, names: dict, strict: bool = True) -> int:
        p = self.p
        dbits = 5 - p.side_count - (1 if p.side_opt else 0)
        d = _eval(delay, names) if delay else 0
        if not 0 <= d < (1 << dbits):
            raise AsmError(f"delay {d} does not fit in {dbits} bits")
        v = d
        if side is not None:
            if p.side_count == 0:
                raise AsmError("side-set used without .side_set")
            s = _eval(side, names)
            if not 0 <= s < (1 << p.side_count):
                raise AsmError(f"side-set value {s} does not fit in {p.side_count} bits")
            v |= s << dbits
            if p.side_opt:
                v |= 0x10
        elif p.side_count and not p.side_opt and strict:
            raise AsmError("this program's side-set is not optional; every instruction needs 'side'")
        return v

    def encode(self, text: str, names: dict, strict: bool = True) -> int:
        delay = None
        m = re.search(r"\[([^\]]+)\]", text)
        if m:
            delay = m.group(1)
            text = text[: m.start()] + text[m.end():]
        side = None
        m = re.search(r"\bside\s+(\S+)", text, re.I)
        if m:
            side = m.group(1)
            text = text[: m.start()] + text[m.end():]
        ds = self._ds(side, delay, names, strict)
        toks = text.replace(",", " ").split()
        if not toks:
            raise AsmError("empty instruction")
        op = toks[0].lower()
        a = [t for t in toks[1:]]
        if op in ("set", "in", "out", "mov") and "," in text:
            # Two comma-separated operands; the second may be an expression with spaces.
            a = [x.strip() for x in text.split(None, 1)[1].split(",", 1)]
        low = [t.lower() for t in a]

        if op == "nop":
            return NOP | (ds << 8)
        if op == "jmp":
            if len(a) == 1:
                cond, target = "always", a[0]
            elif len(a) == 2:
                cond, target = low[0], a[1]
            else:
                raise AsmError("jmp takes [condition] target")
            if cond not in _COND:
                raise AsmError(f"unknown jmp condition '{cond}'")
            t = _eval(target, names)
            if not 0 <= t < 32:
                raise AsmError(f"jump target {t} is outside the 32-instruction window")
            return enc_jmp(_COND[cond], t, ds)
        if op == "wait":
            timed = "timeout" in low
            rel = "rel" in low
            rest = [t for t in low if t not in ("timeout", "rel")]
            if len(rest) != 3 or rest[1] not in ("pin", "irq"):
                raise AsmError("wait takes <0|1> <pin|irq> <index> [rel] [timeout]")
            pol = _eval(rest[0], names)
            idx = _eval(a[low.index(rest[2])], names)
            if pol not in (0, 1):
                raise AsmError("wait polarity must be 0 or 1")
            if rest[1] == "irq":
                if not 0 <= idx < 8:
                    raise AsmError("irq index must be 0..7")
                return enc_wait(pol, True, idx | (0x10 if rel else 0), timed, ds)
            if not 0 <= idx < 32:
                raise AsmError("pin index must be 0..31")
            return enc_wait(pol, False, idx, timed, ds)
        if op in ("in", "out"):
            if len(a) != 2:
                raise AsmError(f"{op} takes <{'source' if op == 'in' else 'destination'}>, <count|bau>")
            table = _SRC if op == "in" else _OUT
            if low[0] not in table:
                raise AsmError(f"unknown {op} operand '{a[0]}'")
            enc = enc_in if op == "in" else enc_out
            if low[1] == "bau":
                return enc(table[low[0]], bau=True, ds=ds)
            c = _eval(a[1], names)
            if not 1 <= c <= 16:
                raise AsmError("bit count must be 1..16")
            return enc(table[low[0]], c, ds=ds)
        if op in ("push", "pull"):
            cond = "iffull" if op == "push" else "ifempty"
            for t in low:
                if t not in (cond, "block", "noblock"):
                    raise AsmError(f"unknown {op} option '{t}'")
            enc = enc_push if op == "push" else enc_pull
            return enc(cond in low, "noblock" not in low, ds)
        if op == "mov":
            if len(a) != 2:
                raise AsmError("mov takes <destination>, [!|~|::|<>]<source>")
            src = low[1]
            mop = R.MOVOP_NONE
            for prefix, code in (("!", R.MOVOP_INVERT), ("~", R.MOVOP_INVERT), ("::", R.MOVOP_REVERSE),
                                 ("<>", R.MOVOP_SWAP)):
                if src.startswith(prefix):
                    mop, src = code, src[len(prefix):]
                    break
            if low[0] not in _MOV or src not in _SRC:
                raise AsmError("unknown mov operand")
            return enc_mov(_MOV[low[0]], _SRC[src], mop, ds)
        if op == "irq":
            rel = "rel" in low
            rest = [t for t in low if t != "rel"]
            mode = "set"
            if len(rest) == 2:
                mode = rest[0]
            elif len(rest) != 1:
                raise AsmError("irq takes [set|nowait|wait|clear] <index> [rel]")
            if mode not in ("set", "nowait", "wait", "clear"):
                raise AsmError(f"unknown irq mode '{mode}'")
            idx = _eval(a[low.index(rest[-1])], names)
            if not 0 <= idx < 8:
                raise AsmError("irq index must be 0..7")
            return enc_irq(idx, mode == "clear", mode == "wait", rel, ds)
        if op == "set":
            if len(a) != 2 or low[0] not in _SET:
                raise AsmError("set takes <pins|x|y|bau|pindirs>, <value>")
            v = _eval(a[1], names)
            if not 0 <= v < 32:
                raise AsmError("set value must be 0..31")
            return enc_set(_SET[low[0]], v, ds)
        raise AsmError(f"unknown instruction '{toks[0]}'")

    def finish(self) -> Program:
        p = self.p
        if len(self.pending) > 32:
            raise AsmError(f"program '{p.name}' has {len(self.pending)} instructions; the limit is 32")
        if not self.wrap_set:
            p.wrap = len(self.pending) - 1
        names = dict(p.defines)
        names.update(p.labels)
        for n, raw, text in self.pending:
            try:
                word = self.encode(text, names)
            except AsmError as e:
                raise AsmError(str(e), n, raw) from None
            p.listing.append(f"{len(p.code):2d}  {word:04x}  {raw.strip()}")
            p.code.append(word)
        for n, raw, text in self.inits:
            try:
                # An init instruction without 'side' encodes side-set value 0.
                p.init.append(self.encode(text, names, strict=False))
            except AsmError as e:
                raise AsmError(str(e), n, raw) from None
        entry = p.config.pop("_entry", None)
        if entry is not None:
            p.entry = _eval(entry, names)
        return p


def assemble(source: str, defines: dict | None = None) -> dict[str, Program]:
    """Assemble a source text. Returns the programs by name, in source order.

    `defines` override (or add) `.define` values at file scope, e.g. {"DATA_BITS": 9}.
    """
    programs: dict[str, Program] = {}
    overrides = dict(defines or {})
    globals_: dict = {"BAU_CRC": R.BAU_CRC, "BAU_STUFF": R.BAU_STUFF, "BAU_NRZI": R.BAU_NRZI,
                      "BAU_MAN": R.BAU_MAN, "BAU_RESET": R.BAU_RESET,
                      "TXE": R.VPIN_TXE, "RXF": R.VPIN_RXF, "STUFF": R.VPIN_STUFF, "BERR": R.VPIN_BERR}
    cur: _Asm | None = None
    for n, raw in enumerate(source.splitlines(), 1):
        text = _strip(raw)
        if not text:
            continue
        if text.lower().startswith(".program"):
            if cur:
                programs[cur.p.name] = cur.finish()
            parts = text.split()
            if len(parts) != 2:
                raise AsmError("'.program' needs a name", n, raw)
            globals_.update(overrides)
            cur = _Asm(parts[1], globals_)
            continue
        if cur is None:
            if text.lower().startswith(".define"):
                parts = text.split()
                globals_[parts[1]] = overrides.get(parts[1], _eval(" ".join(parts[2:]), globals_))
                continue
            raise AsmError("code before '.program'", n, raw)
        while True:
            m = re.match(r"(?:public\s+)?(\w+)\s*:\s*", text)
            if not m:
                break
            if m.group(1) in cur.p.labels:
                raise AsmError(f"duplicate label '{m.group(1)}'", n, raw)
            cur.p.labels[m.group(1)] = len(cur.pending)
            text = text[m.end():]
        if not text:
            continue
        if text.startswith("."):
            cur.directive(n, raw, text)
        else:
            cur.pending.append((n, raw, text))
    if cur:
        programs[cur.p.name] = cur.finish()
    if not programs:
        raise AsmError("no '.program' found")
    return programs


def assemble_file(path: str, defines: dict | None = None) -> dict[str, Program]:
    with open(path) as fh:
        return assemble(fh.read(), defines)


PROGRAM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "programs")


def load_program(name: str, file: str | None = None, **defines) -> Program:
    """Assemble program `name` from sw/programs/<file>.bw (file defaults to the name's first word)."""
    file = file or name.split("_")[0]
    return assemble_file(os.path.join(PROGRAM_DIR, file + ".bw"), defines)[name]


def assemble_one(source: str, defines: dict | None = None) -> Program:
    """Assemble a source that holds exactly one program (a '.program' line is optional)."""
    if ".program" not in source:
        source = ".program anon\n" + source
    progs = assemble(source, defines)
    if len(progs) != 1:
        raise AsmError("expected exactly one program")
    return next(iter(progs.values()))


def instr(text: str, side_count: int = 0, side_opt: bool = False) -> int:
    """Encode a single instruction that has no label operands, e.g. for the EXEC register."""
    a = _Asm("instr", {})
    a.p.side_count, a.p.side_opt = side_count, side_opt
    return a.encode(_strip(text), {})


# ------------------------------------------------------------------------------------------------
# Disassembler
# ------------------------------------------------------------------------------------------------
def disasm(word: int, side_count: int = 0, side_opt: bool = False) -> str:
    op, ds, arg = word >> 13, (word >> 8) & 31, word & 0xFF
    dbits = 5 - side_count - (1 if side_opt else 0)
    delay = ds & ((1 << dbits) - 1)
    side = None
    if side_count and (not side_opt or ds & 0x10):
        side = (ds >> dbits) & ((1 << side_count) - 1)
    if op == R.OP_JMP:
        cond = R.JMP_CONDS[arg >> 5]
        s = f"jmp {'' if cond == 'always' else cond + ' '}{arg & 31}"
    elif op == R.OP_WAIT:
        idx = arg & 31
        if arg & 0x20:
            s = f"wait {arg >> 7} irq {idx & 7}{' rel' if idx & 0x10 else ''}"
        else:
            s = f"wait {arg >> 7} pin {idx}"
        if arg & 0x40:
            s += " timeout"
    elif op in (R.OP_IN, R.OP_OUT):
        names = R.SOURCES if op == R.OP_IN else R.OUT_DESTS
        cnt = "bau" if arg & 0x10 else str((arg & 15) or 16)
        s = f"{'in' if op == R.OP_IN else 'out'} {names[arg >> 5]}, {cnt}"
    elif op == R.OP_PUSHPULL:
        if arg & 0x80:
            s = "pull" + (" ifempty" if arg & 0x40 else "") + (" block" if arg & 0x20 else " noblock")
        else:
            s = "push" + (" iffull" if arg & 0x40 else "") + (" block" if arg & 0x20 else " noblock")
    elif op == R.OP_MOV:
        if word & 0xE0FF == NOP:
            s = "nop"
        else:
            s = f"mov {R.MOV_DESTS[arg >> 5]}, {['', '!', '::', '<>'][(arg >> 3) & 3]}{R.SOURCES[arg & 7]}"
    elif op == R.OP_IRQ:
        mode = "clear" if arg & 0x40 else ("wait" if arg & 0x20 else "set")
        s = f"irq {mode} {arg & 7}{' rel' if arg & 0x10 else ''}"
    else:
        s = f"set {R.SET_DESTS[arg >> 5] or 'reserved'}, {arg & 31}"
    if side is not None:
        s += f" side {side}"
    if delay:
        s += f" [{delay}]"
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description="Bitweaver assembler")
    ap.add_argument("source")
    ap.add_argument("-o", "--outdir", help="write <name>.json and <name>.hex here")
    args = ap.parse_args()
    progs = assemble_file(args.source)
    for p in progs.values():
        if args.outdir:
            os.makedirs(args.outdir, exist_ok=True)
            with open(os.path.join(args.outdir, p.name + ".json"), "w") as fh:
                json.dump(p.to_json(), fh, indent=1)
            with open(os.path.join(args.outdir, p.name + ".hex"), "w") as fh:
                fh.write(p.hex())
        else:
            print(f"; {p.name}: {len(p.code)} instructions, wrap {p.wrap_target}..{p.wrap}, entry {p.entry}")
            print("\n".join(p.listing))


if __name__ == "__main__":
    main()
