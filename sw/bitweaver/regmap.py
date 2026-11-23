"""Bitweaver register map and instruction-set constants: the single source of truth.

Everything that must agree between the RTL, the instruction-set simulator, the assembler and the
documentation is defined here. `src/bw_defs.vh` and `docs/regmap.md` are generated from this file:

    python3 -m bitweaver.regmap --vh src/bw_defs.vh --md docs/regmap.md

Do not edit the generated files by hand.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------------
# Chip-level constants
# --------------------------------------------------------------------------------------------
CHIP_ID = 0xB717
CHIP_VERSION = 0x0100
SPI_ID_BYTE = 0xB7  # first byte shifted out on MISO after CS_N falls

# The chip's rated clock (info.yaml clock_hz). Programs and protocol helpers compute their dividers
# from it by default; the cocotb testbench runs at 60 MHz and passes its own clock explicitly.
F_CLK = 50_000_000

NUM_SM = 2  # four engines did not route in an 8x4 tile; see PROGRESS.md
_SMB = max(1, (NUM_SM - 1).bit_length())  # bits needed to name an engine
IMEM_DEPTH = 64
NUM_PINS = 19  # PIN0..PIN18
NUM_IRQ = 8
FIFO_DEPTH = 4  # per direction; 8 when joined

# Pin groups inside the 19-pin virtual space.
PIN_UI_LO, PIN_UI_HI = 0, 4  # ui_in[4:0], input only
PIN_UO_LO, PIN_UO_HI = 5, 10  # uo_out[5:0], output only (reads back the driven value)
PIN_UIO_LO, PIN_UIO_HI = 11, 18  # uio[7:0], bidirectional
PIN_OUT_MASK = 0x7FFE0  # pins that have an output register (PIN5..PIN18)
PIN_OD_MASK = 0x7F800  # pins that support open-drain mode (PIN11..PIN18)
PIN_FILT_MASK = 0x7F81F  # pins that have an input glitch filter (PIN0..4, PIN11..18)

# Virtual (status) pins, visible to each engine at pin indices 19..31.
VPIN_TXE = 19  # own TX FIFO empty
VPIN_RXF = 20  # own RX FIFO full
VPIN_STUFF = 21  # BAU: the next bit is a stuff bit
VPIN_BERR = 22  # BAU: stuff error (sticky)
VPIN_ZERO = 23  # reads 0
VPIN_IRQ0 = 24  # IRQ flags 0..7 at pins 24..31

# Host SPI command byte.
CMD_WRITE = 0x80
CMD_INC = 0x40

# Address map (8-bit address, 16-bit registers).
SM_BASE = 0x20
SM_STRIDE = 0x10
IMEM_BASE = 0x80

# --------------------------------------------------------------------------------------------
# Instruction set
# --------------------------------------------------------------------------------------------
OP_JMP, OP_WAIT, OP_IN, OP_OUT, OP_PUSHPULL, OP_MOV, OP_IRQ, OP_SET = range(8)
OPCODE_NAMES = ["JMP", "WAIT", "IN", "OUT", "PUSH/PULL", "MOV", "IRQ", "SET"]

JMP_CONDS = ["always", "!x", "x--", "!y", "y--", "x!=y", "pin", "!osre"]
(COND_ALWAYS, COND_NOT_X, COND_X_DEC, COND_NOT_Y, COND_Y_DEC, COND_X_NE_Y, COND_PIN,
 COND_NOT_OSRE) = range(8)

# IN and MOV share one source encoding.
SOURCES = ["pins", "x", "y", "null", "time", "crc", "isr", "osr"]
SRC_PINS, SRC_X, SRC_Y, SRC_NULL, SRC_TIME, SRC_CRC, SRC_ISR, SRC_OSR = range(8)

OUT_DESTS = ["pins", "x", "y", "null", "pindirs", "pc", "isr", "exec"]
OUT_PINS, OUT_X, OUT_Y, OUT_NULL, OUT_PINDIRS, OUT_PC, OUT_ISR, OUT_EXEC = range(8)

MOV_DESTS = ["pins", "x", "y", "crc", "exec", "pc", "isr", "osr"]
MOV_PINS, MOV_X, MOV_Y, MOV_CRC, MOV_EXEC, MOV_PC, MOV_ISR, MOV_OSR = range(8)

MOV_OPS = ["none", "invert", "reverse", "swap"]
MOVOP_NONE, MOVOP_INVERT, MOVOP_REVERSE, MOVOP_SWAP = range(4)

SET_DESTS = ["pins", "x", "y", "bau", "pindirs", None, None, None]
SET_PINS, SET_X, SET_Y, SET_BAU, SET_PINDIRS = range(5)

# BAU mode bits (the operand of SET BAU).
BAU_CRC, BAU_STUFF, BAU_NRZI, BAU_MAN, BAU_RESET = 1, 2, 4, 8, 16


# --------------------------------------------------------------------------------------------
# Registers
# --------------------------------------------------------------------------------------------
@dataclass
class Field:
    name: str
    lsb: int
    width: int
    desc: str
    reset: int = 0

    @property
    def msb(self) -> int:
        return self.lsb + self.width - 1

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lsb


@dataclass
class Reg:
    name: str
    addr: int  # absolute address for global registers, offset for per-engine registers
    access: str  # RO, RW, W1C, WO, or a short description for mixed registers
    desc: str
    fields: list[Field] = field(default_factory=list)

    @property
    def reset(self) -> int:
        value = 0
        for f in self.fields:
            value |= (f.reset << f.lsb) & f.mask
        return value

    @property
    def mask(self) -> int:
        value = 0
        for f in self.fields:
            value |= f.mask
        return value


def _pinvec(lo: bool, what: str) -> list[Field]:
    if lo:
        return [Field("PINS", 0, 16, f"{what} for PIN15..PIN0, one bit per pin")]
    return [Field("PINS", 0, 3, f"{what} for PIN18..PIN16")]


GLOBAL_REGS = [
    Reg("ID", 0x00, "RO", "Chip identifier.", [Field("ID", 0, 16, "Reads 0xB717.", CHIP_ID)]),
    Reg("VERSION", 0x01, "RO", "Design version.",
        [Field("VERSION", 0, 16, "Major in the high byte, minor in the low byte.", CHIP_VERSION)]),
    Reg("CTRL", 0x02, "RW", "Engine enables and restart strobes.", [
        Field("EN", 0, NUM_SM, "Engine enable, one bit per engine. All engines enabled by one write start on the "
                          "same clock cycle."),
        Field("RESTART", 4, NUM_SM, "Write 1 to restart an engine: clears its scratch and shift registers, "
                               "counters, delay, BAU state and clock-divider phase, and sets PC to ORG. "
                               "Reads 0."),
    ]),
    Reg("INT_STAT", 0x03, "RO", "Interrupt sources. HOST_IRQ is high while (INT_STAT & INT_EN) != 0.", [
        Field("IRQ", 0, 8, "The eight IRQ flags."),
        Field("RXNE", 8, NUM_SM, "RX FIFO not empty, one bit per engine."),
        Field("TXNF", 12, NUM_SM, "TX FIFO not full, one bit per engine."),
    ]),
    Reg("INT_EN", 0x04, "RW", "Interrupt enable mask, same layout as INT_STAT.",
        [Field("EN", 0, 16, "1 = the source drives HOST_IRQ.")]),
    Reg("IRQ", 0x05, "W1C", "IRQ flags shared by the engines.",
        [Field("FLAGS", 0, 8, "Read the flags; write 1 to clear a flag.")]),
    Reg("IRQ_FORCE", 0x06, "WO", "Set IRQ flags from the host.",
        [Field("FLAGS", 0, 8, "Write 1 to set a flag. Reads 0.")]),
    Reg("FSTAT", 0x07, "RO", "FIFO status, one bit per engine in each field.", [
        Field("TXFULL", 0, NUM_SM, "TX FIFO full."),
        Field("TXEMPTY", 4, NUM_SM, "TX FIFO empty."),
        Field("RXFULL", 8, NUM_SM, "RX FIFO full."),
        Field("RXEMPTY", 12, NUM_SM, "RX FIFO empty."),
    ]),
    Reg("FDEBUG", 0x08, "W1C", "Sticky FIFO error flags, one bit per engine in each field.", [
        Field("TXOVER", 0, NUM_SM, "The host pushed to a full TX FIFO; the word was dropped."),
        Field("RXUNDER", 4, NUM_SM, "The host read an empty RX FIFO; it read 0."),
        Field("TXSTALL", 8, NUM_SM, "The engine stalled on (or a non-blocking PULL found) an empty TX FIFO."),
        Field("RXSTALL", 12, NUM_SM, "The engine stalled on (or a non-blocking PUSH dropped data at) a full RX "
                                "FIFO."),
    ]),
    Reg("TS_PRESCALE", 0x09, "RW", "Timestamp prescaler.",
        [Field("DIV", 0, 8, "The timestamp counter advances every DIV+1 clock cycles.")]),
    Reg("TIME", 0x0A, "RW", "Free-running 16-bit timestamp counter.",
        [Field("TIME", 0, 16, "Read the counter; write to set it (also clears the prescaler phase).")]),
    Reg("PIN_IN_LO", 0x10, "RO", "Pin inputs after the synchronizer and glitch filter.",
        _pinvec(True, "Input level")),
    Reg("PIN_IN_HI", 0x11, "RO", "Pin inputs, upper pins.", _pinvec(False, "Input level")),
    Reg("PIN_OUT_LO", 0x12, "RW", "Pin output register. Engines writing a pin in the same cycle win. "
                                  "Bits for PIN0..PIN4 read 0.", _pinvec(True, "Output level")),
    Reg("PIN_OUT_HI", 0x13, "RW", "Pin output register, upper pins.", _pinvec(False, "Output level")),
    Reg("PIN_OE_LO", 0x14, "RW", "Pin output enable (direction) register. Bits for PIN0..PIN4 read 0.",
        _pinvec(True, "1 = output")),
    Reg("PIN_OE_HI", 0x15, "RW", "Pin output enable register, upper pins.", _pinvec(False, "1 = output")),
    Reg("PAD_OD_LO", 0x16, "RW", "Open-drain mode. Implemented for PIN11..PIN18 only; other bits read 0.",
        _pinvec(True, "1 = open drain (drive low or float, never drive high)")),
    Reg("PAD_OD_HI", 0x17, "RW", "Open-drain mode, upper pins.", _pinvec(False, "1 = open drain")),
    Reg("PAD_FILT_LO", 0x18, "RW", "Input glitch filter. Implemented for PIN0..PIN4 and PIN11..PIN18.",
        _pinvec(True, "1 = input must hold for 3 clock cycles before it is seen")),
    Reg("PAD_FILT_HI", 0x19, "RW", "Input glitch filter, upper pins.", _pinvec(False, "1 = filter on")),
]

# Per-engine registers. `addr` is the offset from SM_BASE + n * SM_STRIDE.
SM_REGS = [
    Reg("CLKDIV_INT", 0x0, "RW", "Clock divider, integer part.",
        [Field("INT", 0, 16, "Clock cycles per engine tick (0 means 65536, with FRAC 0 only).", 1)]),
    Reg("CLKDIV_FRAC", 0x1, "RW", "Clock divider, fractional part.",
        [Field("FRAC", 0, 8, "Extra 1/256ths of a clock cycle per tick.")]),
    Reg("WRAP", 0x2, "RW", "Program wrap addresses (absolute instruction-memory addresses).", [
        Field("BOTTOM", 0, 6, "Address executed after the instruction at TOP."),
        Field("TOP", 8, 6, "Last address of the wrap range.", 63),
    ]),
    Reg("ORG", 0x3, "RW", "Program origin and jump pin.", [
        Field("ORG", 0, 6, "Instruction-memory address of the program's first instruction. Jump targets "
                           "are relative to it."),
        Field("JMP_PIN", 8, 5, "Pin tested by JMP PIN (0..31, including the virtual pins)."),
    ]),
    Reg("PINCTRL0", 0x4, "RW", "Pin bases.", [
        Field("IN_BASE", 0, 5, "First pin read by IN PINS, MOV x, PINS and WAIT PIN."),
        Field("OUT_BASE", 5, 5, "First pin written by OUT PINS, OUT PINDIRS and MOV PINS."),
        Field("SET_BASE", 10, 5, "First pin written by SET PINS and SET PINDIRS."),
    ]),
    Reg("PINCTRL1", 0x5, "RW", "Side-set base and pin counts.", [
        Field("SIDE_BASE", 0, 5, "First side-set pin."),
        Field("OUT_COUNT", 5, 5, "Number of pins written by OUT and MOV (0..16)."),
        Field("SET_COUNT", 10, 3, "Number of pins written by SET (0..5)."),
        Field("SIDE_COUNT", 13, 2, "Number of side-set data bits (0..3)."),
        Field("SIDE_EN", 15, 1, "1 = the top bit of the delay/side-set field is a per-instruction "
                                "side-set enable."),
    ]),
    Reg("SHIFTCTRL", 0x6, "RW", "Shift register control.", [
        Field("PUSH_THRESH", 0, 4, "ISR bit count that triggers autopush and PUSH IFFULL (0 means 16)."),
        Field("PULL_THRESH", 4, 4, "OSR bit count that triggers autopull, PULL IFEMPTY and !OSRE "
                                   "(0 means 16)."),
        Field("AUTOPUSH", 8, 1, "1 = push the ISR automatically when it reaches PUSH_THRESH."),
        Field("AUTOPULL", 9, 1, "1 = refill the OSR automatically when it reaches PULL_THRESH."),
        Field("IN_SHIFTDIR", 10, 1, "ISR shift direction: 1 = right (new bits enter at bit 15), "
                                    "0 = left.", 1),
        Field("OUT_SHIFTDIR", 11, 1, "OSR shift direction: 1 = right (bit 0 leaves first), 0 = left.", 1),
        Field("SIDE_PINDIR", 12, 1, "1 = side-set drives pin directions instead of pin levels."),
    ]),
    Reg("FIFOCTRL", 0x7, "RW", "FIFO configuration. Any write clears both FIFOs of this engine.", [
        Field("JOIN_TX", 0, 1, "1 = TX FIFO is 8 deep, RX FIFO disabled."),
        Field("JOIN_RX", 1, 1, "1 = RX FIFO is 8 deep, TX FIFO disabled (ignored if JOIN_TX is set)."),
        Field("CHAIN_EN", 2, 1, "1 = this engine's TX FIFO is fed from another engine's RX FIFO."),
        Field("CHAIN_SRC", 3, _SMB, "Engine whose RX FIFO feeds this TX FIFO."),
    ]),
    Reg("TMO", 0x8, "RW", "WAIT timeout.",
        [Field("TICKS", 0, 16, "A timed WAIT gives up after TICKS+1 failed checks.")]),
    Reg("BAU_POLY", 0x9, "RW", "BAU CRC polynomial.",
        [Field("POLY", 0, 16, "Reflected (right-shifting) polynomial; the width is implied by its top "
                              "set bit.")]),
    Reg("BAU_CFG", 0xA, "RW", "BAU bit-stuffing configuration.", [
        Field("STUFF_N", 0, 3, "A stuff bit follows every run of STUFF_N equal bits (0 disables)."),
        Field("STUFF_ANY", 3, 1, "1 = runs of zeros and ones both count (CAN); 0 = only runs of ones "
                                 "(USB)."),
    ]),
    Reg("FIFO", 0xB, "RW", "FIFO access.",
        [Field("DATA", 0, 16, "Write: push to the TX FIFO. Read: pop from the RX FIFO (0 if empty).")]),
    Reg("EXEC", 0xC, "RW", "Immediate execution.",
        [Field("INSTR", 0, 16, "Write: execute this instruction on the next clock cycle, even if the "
                               "engine is disabled. Read: the instruction register.")]),
    Reg("STAT", 0xD, "RO", "Engine status.", [
        Field("PC", 0, 6, "Program counter (absolute)."),
        Field("EXEC_PENDING", 6, 1, "An injected instruction has not completed yet."),
        Field("TXLEVEL", 8, 4, "Words in the TX FIFO."),
        Field("RXLEVEL", 12, 4, "Words in the RX FIFO."),
    ]),
]

GLOBAL_BY_NAME = {r.name: r for r in GLOBAL_REGS}
SM_BY_NAME = {r.name: r for r in SM_REGS}


def addr(name: str, sm: int | None = None) -> int:
    """Address of a global register, or of a per-engine register when `sm` is given."""
    if sm is None:
        return GLOBAL_BY_NAME[name].addr
    return SM_BASE + sm * SM_STRIDE + SM_BY_NAME[name].addr


def fld(reg: str, name: str) -> Field:
    r = GLOBAL_BY_NAME.get(reg) or SM_BY_NAME[reg]
    for f in r.fields:
        if f.name == name:
            return f
    raise KeyError(f"{reg}.{name}")


def pack(reg: str, **values: int) -> int:
    """Build a register value from field values; unnamed fields keep their reset value."""
    r = GLOBAL_BY_NAME.get(reg) or SM_BY_NAME[reg]
    out = r.reset
    for key, value in values.items():
        f = fld(reg, key.upper())
        if value < 0 or value >= (1 << f.width):
            raise ValueError(f"{reg}.{f.name}: {value} does not fit in {f.width} bits")
        out = (out & ~f.mask) | (value << f.lsb)
    return out


# --------------------------------------------------------------------------------------------
# Generators
# --------------------------------------------------------------------------------------------
HEADER = "Generated by sw/bitweaver/regmap.py. Do not edit; run `make -C sw gen` instead."


def gen_vh() -> str:
    out = [f"// {HEADER}", "`ifndef BW_DEFS_VH", "`define BW_DEFS_VH", ""]
    out += [f"`define BW_CHIP_ID      16'h{CHIP_ID:04X}",
            f"`define BW_CHIP_VERSION 16'h{CHIP_VERSION:04X}",
            f"`define BW_SPI_ID_BYTE  8'h{SPI_ID_BYTE:02X}",
            f"`define BW_SM_BASE      8'h{SM_BASE:02X}",
            f"`define BW_IMEM_BASE    8'h{IMEM_BASE:02X}",
            f"`define BW_NUM_SM       {NUM_SM}",
            f"`define BW_SMB          {_SMB}", ""]
    out.append("// Global register addresses")
    for r in GLOBAL_REGS:
        out.append(f"`define BW_A_{r.name:<12} 8'h{r.addr:02X}")
    out += ["", "// Per-engine register offsets"]
    for r in SM_REGS:
        out.append(f"`define BW_O_{r.name:<12} 4'h{r.addr:X}")
    out += ["", "// Fields as msb:lsb ranges"]
    for r in GLOBAL_REGS + SM_REGS:
        for f in r.fields:
            if f.name in ("PINS",) or len(r.fields) == 1:
                continue
            out.append(f"`define BW_F_{r.name + '_' + f.name:<22} {f.msb}:{f.lsb}")
    out += ["", "// Reset values of the per-engine configuration registers"]
    for r in SM_REGS:
        if r.access == "RW" and r.name not in ("FIFO", "EXEC"):
            out.append(f"`define BW_R_{r.name:<12} 16'h{r.reset:04X}")
    out += ["", "// Opcodes"]
    for i, n in enumerate(["JMP", "WAIT", "IN", "OUT", "PUSHPULL", "MOV", "IRQ", "SET"]):
        out.append(f"`define BW_OP_{n:<9} 3'd{i}")
    out += ["", "// JMP conditions"]
    for i, n in enumerate(["ALWAYS", "NOT_X", "X_DEC", "NOT_Y", "Y_DEC", "X_NE_Y", "PIN", "NOT_OSRE"]):
        out.append(f"`define BW_COND_{n:<9} 3'd{i}")
    out += ["", "// IN and MOV sources"]
    for i, n in enumerate(SOURCES):
        out.append(f"`define BW_SRC_{n.upper():<8} 3'd{i}")
    out += ["", "// OUT destinations"]
    for i, n in enumerate(OUT_DESTS):
        out.append(f"`define BW_OUT_{n.upper():<8} 3'd{i}")
    out += ["", "// MOV destinations"]
    for i, n in enumerate(MOV_DESTS):
        out.append(f"`define BW_MOV_{n.upper():<8} 3'd{i}")
    out += ["", "// SET destinations"]
    for i, n in enumerate(SET_DESTS):
        if n:
            out.append(f"`define BW_SET_{n.upper():<8} 3'd{i}")
    out += ["", "// Virtual pins"]
    for n, v in [("TXE", VPIN_TXE), ("RXF", VPIN_RXF), ("STUFF", VPIN_STUFF), ("BERR", VPIN_BERR),
                 ("ZERO", VPIN_ZERO), ("IRQ0", VPIN_IRQ0)]:
        out.append(f"`define BW_VPIN_{n:<6} {v}")
    out += ["", "`endif", ""]
    return "\n".join(out)


def _reg_table(regs: list[Reg], addr_fmt) -> list[str]:
    out = ["| Address | Name | Access | Bits | Field | Reset | Description |", "|---|---|---|---|---|---|---|"]
    for r in regs:
        first = True
        for f in r.fields:
            bits = f"{f.msb}:{f.lsb}" if f.width > 1 else f"{f.lsb}"
            head = f"| {addr_fmt(r)} | `{r.name}` | {r.access} |" if first else "| | | |"
            reset = f"0x{f.reset:X}" if f.reset > 9 else str(f.reset)
            desc = f.desc if not first or len(r.fields) > 1 else f"{r.desc} {f.desc}"
            if first and len(r.fields) > 1:
                desc = f"**{r.desc}** {f.desc}"
            out.append(f"{head} {bits} | `{f.name}` | {reset} | {desc} |")
            first = False
    return out


def gen_md() -> str:
    out = [f"<!-- {HEADER} -->", "# Register map", "",
           "All registers are 16 bits wide and are reached through the host SPI port at an 8-bit address. "
           "See [architecture.md](architecture.md) for the SPI framing.", "",
           "Access types: RO read only, RW read/write, W1C write 1 to clear, WO write only.", "",
           "| Address range | Contents |", "|---|---|",
           "| `0x00`–`0x1F` | Global registers |"]
    for n in range(NUM_SM):
        base = SM_BASE + n * SM_STRIDE
        out.append(f"| `0x{base:02X}`–`0x{base + SM_STRIDE - 1:02X}` | Engine SM{n} registers |")
    out += [f"| `0x{IMEM_BASE:02X}`–`0x{IMEM_BASE + IMEM_DEPTH - 1:02X}` | Instruction memory, "
            f"{IMEM_DEPTH} words (write only; reads return 0) |", "",
            "Unlisted addresses read 0 and ignore writes.", "", "## Global registers", ""]
    out += _reg_table(GLOBAL_REGS, lambda r: f"`0x{r.addr:02X}`")
    out += ["", "## Per-engine registers", "",
            f"Engine *n* has its registers at `0x{SM_BASE:02X} + 0x{SM_STRIDE:02X} * n + offset`.", ""]
    out += _reg_table(SM_REGS, lambda r: f"`+0x{r.addr:X}`")
    out += ["", "## Pin numbering", "",
            "| Pins | Pads | Direction |", "|---|---|---|",
            "| PIN0..PIN4 | `ui_in[0]`..`ui_in[4]` | input only |",
            "| PIN5..PIN10 | `uo_out[0]`..`uo_out[5]` | output only; reads back the driven level |",
            "| PIN11..PIN18 | `uio[0]`..`uio[7]` | bidirectional, optional open drain |",
            "| 19 | virtual | this engine's TX FIFO is empty |",
            "| 20 | virtual | this engine's RX FIFO is full |",
            "| 21 | virtual | BAU: the next bit is a stuff bit |",
            "| 22 | virtual | BAU: stuff error (sticky until the BAU is reset) |",
            "| 23 | virtual | always 0 |",
            "| 24..31 | virtual | IRQ flags 0..7 |", ""]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vh", help="write the Verilog header here")
    ap.add_argument("--md", help="write the Markdown register table here")
    args = ap.parse_args()
    if args.vh:
        with open(args.vh, "w") as fh:
            fh.write(gen_vh())
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(gen_md())


if __name__ == "__main__":
    main()
