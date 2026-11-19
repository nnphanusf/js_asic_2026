# Programming guide: writing a new protocol

This guide writes a protocol the chip was not designed around, Dallas/Maxim **1-Wire**, from an empty file to a
tested program, using only the tools in this repository. The finished program is
[`sw/programs/onewire.bw`](../sw/programs/onewire.bw) and its test is
[`test/test_proto_onewire.py`](../test/test_proto_onewire.py). The second half of the guide is a collection of
patterns and pitfalls from the other protocols.

You need Python 3.10+ (for the assembler and simulator) and, for the RTL tests, Icarus Verilog and cocotb
(`test/requirements.txt`). Run Python commands from `sw/` or put `sw/` on `PYTHONPATH`.

## 1. Read the protocol as timing

1-Wire is one open-drain line with a pull-up. The master starts every action by pulling the line low:

| Action | Master does | Device does |
|---|---|---|
| Reset | low for 480 us, then release | waits 15–60 us, pulls low for 60–240 us ("presence") |
| Write 1 | low for 1–15 us, release; slot is 60–120 us | samples 15–60 us after the falling edge |
| Write 0 | low for 60–120 us | samples as above |
| Read | low for ≥1 us, release, sample before 15 us | holds the line low to send a 0 |

Two observations make the program small: a **read slot is a write-1 slot**, and everything is slow, so a
**1 µs tick** is plenty. At 60 MHz that is `clkdiv 60`.

## 2. Choose pins, tick and host interface

* One pin that can be open drain: PIN17 (`uio[6]`). Open-drain mode turns "drive high" into "release", so the
  program can simply write 0 and 1.
* Host interface: one TX word per operation, one RX word back.
  * `0x0000`: reset; reply 0 if a device answered.
  * `0x0100 | byte`: exchange a byte, LSB first; reply holds the byte read. Sending `0xFF` reads a byte.

The configuration lines at the top of the program say all of this:

```
.program onewire
.pins out=17 out_count=1 set=17 set_count=1 in=17 dirs=17 high=17 od=17
.shift out=right in=right
.clkdiv 60
.timeout 240
```

`dirs=17 high=17` makes the loader enable the pin as an output with level 1 (released) before the program runs;
`od=17` turns on open-drain mode for it. `out=right` sends the OSR's bit 0 first (1-Wire is LSB first);
`in=right` collects bits from the top, so after eight bits the byte sits in bits 15:8 of the ISR.

## 3. Decode a command

```
cmd:
    pull                      ; wait for a command word
    out x, 8                  ; the byte
    out y, 1                  ; 1 = byte, 0 = reset
    jmp !y reset
    mov osr, x                ; the byte again, LSB first
    set y, 7
```

`PULL` blocks while the TX FIFO is empty, so an idle engine simply waits here.

## 4. One time slot with delays

Every instruction can carry a delay of up to 31 extra ticks (`[n]`), so a whole 70 µs slot is six instructions.
The comments give the time in µs at which each instruction runs:

```
slot:
    set pins, 0 [5]           ; 0..5: drive low for 6 us
    out pins, 1 [8]           ; 6..14: release for a 1 (or a read), keep low for a 0
    in pins, 1 [31]           ; 15: sample; a device sending 0 holds the line low here
    nop [12]                  ; 47..59
    set pins, 1 [8]           ; 60..68: release (ends a 0 slot), recovery time
    jmp y-- slot              ; 69
    push                      ; the byte read, in bits 15:8
```

Count ticks, not instructions: an instruction with `[n]` takes `n + 1` ticks. Here every slot is exactly 70
ticks whatever the data, which is what makes bit-banging on this chip exact.

## 5. Reset, presence and a timeout

The presence pulse may never come (no device on the bus). A plain `WAIT` would hang. A **timed WAIT** gives up
after `TMO + 1` ticks; on success it skips the next instruction, so the next instruction is the timeout path:

```
reset:
    set pins, 0               ; reset pulse: 480 us low
    set x, 14
rlow:
    jmp x-- rlow [31]         ; 15 x 32 us
    set pins, 1 [14]          ; release, wait 15 us
    wait 0 pin 0 timeout      ; presence pulse? success skips the next instruction
    jmp absent
    set x, 0                  ; present
    wait 1 pin 0              ; end of the presence pulse
    jmp rest
absent:
    set x, 1
rest:
    mov isr, x
    push
    set x, 12
rwait:
    jmp x-- rwait [31]        ; let the reset window finish
    jmp cmd
```

Long delays are loops of delayed jumps (`jmp x-- label [31]` is 32 ticks per pass). `wait 0 pin 0` is relative
to `IN_BASE`, so it watches PIN17.

## 6. Assemble and look at it

```bash
cd sw
python3 -m bitweaver.asm programs/onewire.bw
```

The listing shows each instruction's address and encoding. The assembler checks delays, side-set widths, jump
reach (targets must be in the first 32 instructions of a program) and operand ranges.

Run it on the cycle-accurate simulator before touching the RTL. `Bench` models the pads, `FastDriver` performs
host register accesses without simulating the SPI bit clock:

```python
from bitweaver.asm import load_program
from bitweaver.loader import Bench, FastDriver

b = Bench(trace=True)
d = FastDriver(b)
d.load(load_program("onewire"), sm=0)
d.enable(1)
start = len(b.trace)
d.push(0, [0x0000])                 # reset
b.run(1200 * 60)                    # 1.2 ms
line = "".join(str((t >> 17) & 1) for t in b.trace[start::60])  # PIN17, one sample per microsecond
print(line.count("0"))              # 482: the reset pulse (the 480 us loop plus two instructions)
print(d.pop(0))                     # [1]: no presence pulse, the timeout path ran
```

`b.hooks` takes callables that run before every cycle and can play a device: change `b.uio_ext` (what the outside
world drives onto PIN11..PIN18) and `b.ui_pins`.

## 7. Test it against an independent model

The RTL test ([`test/test_proto_onewire.py`](../test/test_proto_onewire.py)) loads the program over the real SPI
host port and talks to a 1-Wire device model written from the Maxim datasheet
([`test/lib/onewire_model.py`](../test/lib/onewire_model.py)), not from the program. While it runs, the lock-step
checker compares every output pin of the RTL with the simulator on every clock cycle.

```bash
cd test
make COCOTB_TEST_MODULES=test_proto_onewire
python3 -m cocotb_tools.check_results results.xml
```

Write the model from the protocol's specification, not from your program. A model that only replays what your
program does will agree with your bugs.

## 8. Run it on the board

On a PC, turn the program into a load image (the exact register writes), copy it and the driver to the demo
board, and replay it:

```bash
python3 -m bitweaver.loader sw/programs/onewire.bw onewire --sm 0 -o onewire.json
mpremote cp onewire.json sw/host/micropython/bitweaver.py :
```

```python
import bitweaver
bw = bitweaver.Bitweaver()
bw.load_image("onewire.json")
bw.enable(1)
bw.push(0, [0x0000, 0x133] + [0x1FF] * 8)   # reset, READ ROM, read eight bytes
print(bw.pop(0))
```

## Patterns

* **Clock with side-set.** Put a clock pin on side-set and every instruction can toggle it for free while it does
  something else (`out pins, 1 side 0`). SPI, JTAG, SWD and I2C all do this.
* **Safe idle level.** A stalled instruction still applies its side-set, so put the idle clock level on the
  instruction that waits for data (`pull side 0`). The same trick holds a line low on purpose: the I2C target's
  `pull block side 0` stretches SCL until the host supplies a byte.
* **Conditional skip.** `wait <level> pin <n> timeout` with `TMO = 0` checks once and skips the next instruction if
  the pin matches. Together with `jmp pin` that gives two independent branch conditions.
* **Constant-time branches.** When two paths must take the same time (CAN and USB keep every bit at exactly 10
  ticks whether or not a stuff bit is inserted), give both paths the same number of ticks with `nop [n]`, and
  remember that a delay also applies when a jump is not taken.
* **Stuff-aware loops.** Virtual pin 21 says "the next bit is a stuff bit": `jmp pin` on it before counting a bit.
* **Several engines, one protocol.** Split work that must happen at the same time. The I2C target uses one engine
  to watch for START/STOP and another to shift bits; they talk through IRQ flags, which the second engine tests
  with `jmp pin` on virtual pins 24..31.
* **Host-chosen behaviour.** `out exec, 16` runs an instruction taken from the FIFO. The USB transmitter lets the
  host decide per packet which segment runs through the CRC unit.
* **FIFO depth.** A program that only transmits or only receives should join its FIFOs (`.fifo join_tx` /
  `join_rx`) for 8 words of buffering. A receiver on someone else's clock should never stall: use
  `push noblock` (data is dropped and `FDEBUG.RXSTALL` set) rather than blocking.
* **Packing several programs.** Programs are position independent; `Driver.load` places them one after another,
  and both engines run their programs out of the 64 words.

## Pitfalls

* **Input latency.** Inputs reach the engines two clock cycles after the pad (five with the glitch filter). At
  `clkdiv 1`, an engine reacting to an edge sees it about three cycles late. Leave a tick of margin where a protocol
  samples right after an edge.
* **Read pins in one instruction when they must agree.** Testing two pins in consecutive instructions can see a
  transition between the tests. The USB end-of-packet detector originally did this and saw K-to-J transitions as
  SE0; `in pins, 2` samples both in the same cycle.
* **SCL is still high after START.** A receiver that waits for "clock high" right after a START condition samples
  a bogus bit. Wait for the clock to fall first.
* **Autopull looks ahead.** An `OUT` that empties the OSR refills it in the same tick if the TX FIFO holds data. A
  program that later overwrites the OSR (for example with a CRC) would lose that word: use explicit
  `pull ifempty` instead of autopull in such programs (see `can.bw`).
* **Jump reach.** Jump targets are 5 bits, relative to the program start: labels you jump to must be among the
  first 32 instructions. Put long straight-line code at the end.
* **Rewriting a running program.** Writing instruction memory under a running engine works (the engine sees the
  new word at once) but is rarely what you want; disable the engine first.
