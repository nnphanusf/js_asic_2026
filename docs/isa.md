# Bitweaver instruction set

Every engine executes 16-bit instructions from the shared 64-word instruction memory. This page is the
specification; `sw/bitweaver/iss.py` implements it cycle for cycle, and the RTL is checked against that model
on every clock cycle (see [verification.md](verification.md)).

## Instruction format

```
 15   13 12        8 7                    0
+-------+-----------+----------------------+
|  op   | delay /   |      arguments       |
|       | side-set  |                      |
+-------+-----------+----------------------+
```

| op | Instruction |
|---|---|
| 0 | `JMP` |
| 1 | `WAIT` |
| 2 | `IN` |
| 3 | `OUT` |
| 4 | `PUSH` / `PULL` |
| 5 | `MOV` |
| 6 | `IRQ` |
| 7 | `SET` |

### Delay and side-set field (bits 12:8)

The 5-bit field is split by the engine's `PINCTRL1` register:

```
[ enable ]  [ side-set value: SIDE_COUNT bits ]  [ delay: the remaining bits ]
  only if SIDE_EN = 1
```

* **Side-set** writes `SIDE_COUNT` (0 to 3) pins starting at `SIDE_BASE`, on every tick the instruction is
  evaluated, *including ticks on which it stalls*. With `SIDE_EN` set, the top bit enables side-set per
  instruction. With `SHIFTCTRL.SIDE_PINDIR` set, side-set drives pin directions instead of levels. Side-set
  wins over an `OUT`, `MOV` or `SET` of the same engine on the same pin.
* **Delay** idles the engine for that many extra ticks after the instruction completes. A stalled
  instruction does not start its delay until it completes. Instructions injected by the host have no delay.

The assembler syntax is `instr ... side <value> [<delay>]`, with `.side_set <count> [opt] [pindirs]`.

## Timing

An engine advances by one **tick** whenever its clock divider fires (`CLKDIV_INT + CLKDIV_FRAC / 256`
clock cycles per tick, 16.8 fixed point, 1 = every cycle). On each tick the engine either counts down a
delay or evaluates the instruction register IR:

* if the instruction **completes**, its effects apply at the end of the cycle, PC moves on, and IR is
  loaded with the next instruction in the same cycle, so the next tick executes it. There is no fetch
  bubble and no branch penalty;
* if the instruction **stalls** (a `WAIT` whose condition is false, a blocking `PULL` on an empty FIFO,
  ...), nothing changes except side-set, the documented stall side effects below, and the FIFO status
  flags. The instruction is evaluated again on the next tick.

PC advances with wrapping: after the instruction at `WRAP.TOP` comes `WRAP.BOTTOM`, otherwise PC + 1.
`WRAP` holds absolute addresses.

Input pins pass a 2-flip-flop synchronizer (and optionally a 3-cycle glitch filter), so an engine sees a
pad change 2 clock cycles later (5 with the filter). Output pins change on the clock edge at the end of the
tick that writes them.

## Pins seen by an engine

Engines read a 32-bit pin vector:

| Index | Meaning |
|---|---|
| 0–4 | PIN0–PIN4 (`ui_in[4:0]`) |
| 5–10 | PIN5–PIN10 (`uo_out[5:0]`), reading back the driven level (0 while not driven) |
| 11–18 | PIN11–PIN18 (`uio[7:0]`), the level on the pad |
| 19 | this engine's TX FIFO is empty |
| 20 | this engine's RX FIFO is full |
| 21 | BAU: the next bit is a stuff bit |
| 22 | BAU: a received stuff bit had the wrong value (sticky until `SET BAU` with bit 4) |
| 23 | always 0 |
| 24–31 | IRQ flags 0–7 |

Pin numbers in instructions are relative and wrap around at 32: `WAIT ... PIN n` tests pin
`(IN_BASE + n) mod 32`, and `IN PINS` reads pins `IN_BASE`, `IN_BASE + 1`, ... (rotating). `JMP PIN` tests
the absolute pin `ORG.JMP_PIN`. Writes to pins (`OUT`/`MOV`/`SET`/side-set) affect only PIN5–PIN18; other
indices are ignored.

When several engines write the same pin in the same cycle, the highest-numbered engine wins, and engines win
over a host write of `PIN_OUT`/`PIN_OE` in the same cycle.

## Shift registers

* **OSR** (output shift register) feeds `OUT`. It shifts right (bit 0 first) or left (bit 15 first), per
  `SHIFTCTRL.OUT_SHIFTDIR`. Its counter `osr_cnt` counts the bits shifted out since the last load, 0..16.
  The OSR is *empty* when `osr_cnt >= PULL_THRESH`. After a restart the OSR is empty (`osr_cnt = 16`).
* **ISR** (input shift register) collects `IN` data. Right shift inserts new bits at the top (bit 15),
  left shift at the bottom. `isr_cnt` counts the bits shifted in, 0..16. The ISR is *full* when
  `isr_cnt >= PUSH_THRESH`.
* **Autopull** (`SHIFTCTRL.AUTOPULL`): an `OUT` that finds the OSR empty refills it from the TX FIFO and
  stalls for that tick (the `OUT` itself runs on the next tick). If the FIFO is empty it stalls without
  refilling and sets `FDEBUG.TXSTALL`. In addition, an `OUT` that empties the OSR refills it in the same
  tick if the FIFO holds data.
* **Autopush** (`SHIFTCTRL.AUTOPUSH`): an `IN` that fills the ISR pushes it to the RX FIFO and clears it.
  If the RX FIFO is full the `IN` stalls (`FDEBUG.RXSTALL`).

Thresholds of 0 mean 16.

## Instructions

In the encodings below, `d` is the delay/side-set field.

### JMP — jump

```
000 ddddd ccc aaaaa      jmp [cond] <target>
```

Jumps to `ORG + a` (mod 64) if the condition holds, otherwise continues with the next instruction.

| ccc | Condition | Notes |
|---|---|---|
| 0 | always | |
| 1 | `!x` | X is zero |
| 2 | `x--` | X is non-zero; X is decremented in any case |
| 3 | `!y` | Y is zero |
| 4 | `y--` | Y is non-zero; Y is decremented in any case |
| 5 | `x!=y` | |
| 6 | `pin` | pin `JMP_PIN` (0..31, so also the virtual pins) is high |
| 7 | `!osre` | the OSR is not empty |

Jump targets are 5 bits, relative to the program origin, so they reach the first 32 instructions of a
program. Programs may be up to 64 instructions long; later instructions are reached by falling through.

### WAIT — wait for a pin or IRQ flag, with optional timeout

```
001 ddddd p t s iiiii    wait <p> pin <n> [timeout]
                         wait <p> irq <n> [rel] [timeout]
```

Stalls until the selected source equals `p`.

* `s = 0`: pin `(IN_BASE + i) mod 32`.
* `s = 1`: IRQ flag `i[2:0]`, or with `i[4]` set (`rel`) flag `(i[2] << 2) | ((i[1:0] + engine) mod 4)`.
  Waiting for a flag to be **1** clears it when the wait completes.
* `t = 1` (**timed**, a Bitweaver extension): the wait gives up after `TMO + 1` failed checks (one per
  tick). On success the next instruction is **skipped**; on timeout execution continues with the next
  instruction. So the instruction after a timed `WAIT` is its timeout handler:

  ```
      wait 1 pin 0 timeout    ; success: continue at "ok"
      jmp no_response         ; timeout
  ok: ...
  ```

  With `TMO = 0` a timed `WAIT` checks once and never stalls: a conditional skip on any pin or flag.

### IN — shift data into the ISR

```
010 ddddd sss b nnnn     in <source>, <count>     (b = 0, count n, 0 = 16)
                         in <source>, bau         (b = 1)
```

Shifts `count` bits of the source into the ISR. Sources are shared with `MOV`:

| sss | Source |
|---|---|
| 0 | `pins` — the pin vector rotated by `IN_BASE` |
| 1 | `x` |
| 2 | `y` |
| 3 | `null` — zeros |
| 4 | `time` — the 16-bit timestamp counter (an extension) |
| 5 | `crc` — this engine's BAU CRC register (an extension) |
| 6 | `isr` |
| 7 | `osr` |

With `bau`, one bit (bit 0 of the source) goes through the bit-level assist unit; see below.

### OUT — shift data out of the OSR

```
011 ddddd sss b nnnn     out <destination>, <count>
                         out <destination>, bau
```

| sss | Destination |
|---|---|
| 0 | `pins` — `OUT_COUNT` pins from `OUT_BASE` |
| 1 | `x` |
| 2 | `y` |
| 3 | `null` |
| 4 | `pindirs` — directions of `OUT_COUNT` pins from `OUT_BASE` |
| 5 | `pc` — jump to `ORG + value[4:0]` |
| 6 | `isr` — also sets `isr_cnt` to the count |
| 7 | `exec` — execute the value as the next instruction (PC does not advance past it) |

With `bau`, one bit goes through the BAU and the destination receives the 2-bit pair `{~level, level}`
(so with `OUT_COUNT = 2`, a differential pair).

### PUSH / PULL

```
100 ddddd 0 f b 00000    push [iffull] [block|noblock]
100 ddddd 1 f b 00000    pull [ifempty] [block|noblock]
```

* `PUSH` moves the ISR to the RX FIFO and clears it (`isr_cnt = 0`). `iffull`: do nothing unless the ISR
  is full. If the RX FIFO is full: block (stall, `FDEBUG.RXSTALL`), or with `noblock` drop the data, clear
  the ISR and continue (also `RXSTALL`).
* `PULL` loads the OSR from the TX FIFO (`osr_cnt = 0`). `ifempty`: do nothing unless the OSR is empty. If
  the TX FIFO is empty: block, or with `noblock` copy X into the OSR (`FDEBUG.TXSTALL` in both cases).

### MOV — copy with an operation

```
101 ddddd ddd oo sss     mov <destination>, [!|::|<>]<source>
```

| oo | Operation |
|---|---|
| 0 | none |
| 1 | `!` invert |
| 2 | `::` bit reverse |
| 3 | `<>` byte swap (an extension) |

| ddd | Destination |
|---|---|
| 0 | `pins` (as `OUT pins`) |
| 1 | `x` |
| 2 | `y` |
| 3 | `crc` — load the BAU CRC register |
| 4 | `exec` |
| 5 | `pc` |
| 6 | `isr` (sets `isr_cnt = 0`) |
| 7 | `osr` (sets `osr_cnt = 0`, i.e. full) |

`nop` is `mov y, y`.

### IRQ — shared flags

```
110 ddddd 0 c w r 0 iii   irq [set|wait|clear] <n> [rel]
```

There are eight IRQ flags shared by all engines and the host (`IRQ` register, `HOST_IRQ` pin via
`INT_EN`). `rel` adds the engine number to the low two bits of the index, as for `WAIT`.

* `irq <n>` / `irq set <n>`: set the flag.
* `irq clear <n>`: clear it.
* `irq wait <n>`: set the flag, then stall until something (another engine with `WAIT`, or the host)
  clears it.

If several sources touch a flag in one cycle: engine sets win over engine clears, and a host `IRQ_FORCE`
wins over everything; a host write-1-to-clear loses to an engine set.

### SET — immediate

```
111 ddddd ddd vvvvv      set <destination>, <value>
```

| ddd | Destination |
|---|---|
| 0 | `pins` — `SET_COUNT` pins from `SET_BASE` |
| 1 | `x` (zero-extended) |
| 2 | `y` |
| 3 | `bau` — the BAU mode, see below |
| 4 | `pindirs` |
| 5–7 | reserved, no effect |

## Bit-level assist unit (BAU)

Each engine has a BAU on its one-bit shift path, used by `IN ..., bau` and `OUT ..., bau`. `SET BAU, m`
selects the mode:

| m bit | Name | Effect |
|---|---|---|
| 0 | `BAU_CRC` | every data bit updates the CRC register |
| 1 | `BAU_STUFF` | bit stuffing / destuffing |
| 2 | `BAU_NRZI` | NRZI: a 0 bit is a level change, a 1 bit keeps the level |
| 3 | `BAU_MAN` | Manchester (IEEE 802.3): each data bit takes two `OUT`s, `~bit` then `bit` |
| 4 | `BAU_RESET` | also clear the run counter, line level (to 1), Manchester phase and the stuff error flag |

**CRC.** A right-shifting LFSR: `crc = (crc >> 1) ^ (POLY if (crc ^ bit) & 1 else 0)`, with `BAU_POLY` the
bit-reversed polynomial (the width is implied by its top bit). Load an initial value with `MOV CRC, ...`
(e.g. `mov crc, !null` for all ones) and read it with `IN CRC` or `MOV ..., CRC`. `BAU_POLY` is the
polynomial with its bits reversed, so the register holds the CRC bit-reversed. Examples: USB CRC16
(polynomial `0x8005`) `0xA001`, USB CRC5 (`0x05`) `0x0014`, CAN CRC15 (`0x4599`) `0x4CD1`.

**Bit stuffing.** `BAU_CFG.STUFF_N` (1..7) and `STUFF_ANY`: after `STUFF_N` equal data bits (ones only, or
either value with `STUFF_ANY`), the next bit is a stuff bit, the complement of the run. Virtual pin 21
shows that a stuff bit is due.
* On `OUT ..., bau` the stuff bit is sent instead of an OSR bit and the OSR does not shift.
* On `IN ..., bau` the stuff bit is checked (a wrong value sets the sticky error flag, virtual pin 22) and
  dropped: the ISR does not change and the CRC is not updated.

**NRZI** (receive): a bit equal to the previous line level decodes as 1, a change as 0. (Transmit): a 0
toggles the line, a 1 keeps it. NRZI applies after stuffing on transmit and before destuffing on receive,
as USB requires.

**Order on transmit:** OSR bit → CRC → stuffing → NRZI or Manchester → pins. On receive: pins → NRZI →
destuffing → CRC → ISR.

## Instruction injection

* The host writes an instruction to an engine's `EXEC` register: it executes on the next clock cycle, even
  if the engine is disabled, without waiting for a tick, with no delay. PC does not advance past it (a
  jump still jumps). If it stalls it keeps being retried every cycle (`STAT.EXEC_PENDING`).
* `OUT EXEC` and `MOV EXEC` execute a value from inside a program on the next tick.

Writing the instruction memory at the address an engine is currently stalled on replaces the stalled
instruction (IR always follows memory unless an instruction was injected); the replacement inherits the
stalled instruction's wait state (`WAIT ... timeout` countdown, `IRQ WAIT` phase). Avoid it, or disable
the engine first.

## Restart and enable

`CTRL.RESTART` (write 1) puts an engine at `ORG` with X, Y, ISR, OSR, counters, delay, BAU state and the
clock-divider phase cleared, without touching its FIFOs or configuration. `CTRL.EN` starts and stops
engines; engines enabled by the same write start on the same clock cycle with their dividers in phase.
