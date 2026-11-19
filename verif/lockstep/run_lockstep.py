#!/usr/bin/env python3
"""Constrained-random lock-step between the Verilated RTL and the Python reference model.

For every seed this script
  1. builds a random stimulus: reset, random programs in the whole instruction memory, random engine
     and pad configuration, then random host SPI traffic (FIFO pushes and pops, register reads and
     writes, instruction injection, restarts, instruction-memory rewrites while running), random
     activity on all input pins, and occasional resets;
  2. runs it on the RTL through the Verilator harness and on the reference model;
  3. requires identical uo_out / uio_out / uio_oe on every cycle.

The stimulus is a pure function of the seed, so any failure is reproduced with `--first N --seeds 1`.
The reference model also records functional coverage (opcode x option x outcome), which is merged
over all seeds and written to coverage/functional.txt.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import random
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "sw"))

from bitweaver import regmap as R  # noqa: E402
from bitweaver.iss import Bitweaver  # noqa: E402
from bitweaver.loader import frame_bytes  # noqa: E402


# ------------------------------------------------------------------------------------------ stimulus
class Stim:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.data = bytearray()
        self.pins = 0x1F  # PIN0..PIN4 levels
        self.ext = 0xFF  # outside drive on the bidirectional pads
        self.noise = 0.02  # chance per cycle that a pin changes
        self.ena = 1

    def cycle(self, cs_n=1, sck=0, mosi=0, rst_n=1):
        rng = self.rng
        if rng.random() < self.noise:
            self.pins ^= 1 << rng.randrange(5)
        if rng.random() < self.noise:
            self.ext ^= 1 << rng.randrange(8)
        if rng.random() < 0.0005:
            self.ena ^= 1
        ui = (self.pins & 0x1F) | (mosi << 5) | (sck << 6) | (cs_n << 7)
        self.data += bytes([ui, self.ext, rst_n | (self.ena << 1)])

    def idle(self, n):
        for _ in range(n):
            self.cycle()

    def frame(self, data: bytes, half: int):
        for _ in range(half):
            self.cycle(cs_n=0)
        for byte in data:
            for bit in range(7, -1, -1):
                m = (byte >> bit) & 1
                for _ in range(half):
                    self.cycle(0, 0, m)
                for _ in range(half):
                    self.cycle(0, 1, m)
        for _ in range(half):
            self.cycle(cs_n=0)
        for _ in range(half + self.rng.randrange(4)):
            self.cycle()

    def write(self, addr, words, inc=False, half=4):
        self.frame(frame_bytes(addr, list(words), write=True, inc=inc), half)

    def read(self, addr, count=1, inc=False, half=4):
        self.frame(frame_bytes(addr, count=count, write=False, inc=inc), half)

    def reset(self, n):
        for _ in range(n):
            self.cycle(rst_n=0)


def rand_instr(rng: random.Random) -> int:
    """A random instruction: fully random words, or a random opcode with gentler operands."""
    if rng.random() < 0.3:
        return rng.randrange(1 << 16)
    op = rng.choice([0, 0, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 7])
    ds = rng.choice([0, 0, 0, 1, 2, 3, rng.randrange(32)])
    arg = rng.randrange(256)
    if op == 1 and rng.random() < 0.7:  # WAIT: mostly on pins, often timed
        arg = (arg & 0xDF) | (0x40 if rng.random() < 0.5 else 0)
    if op == 6 and rng.random() < 0.6:  # IRQ: mostly set / clear rather than wait
        arg &= ~0x20
    return (op << 13) | (ds << 8) | arg


def rand_config(rng: random.Random, sm: int) -> list[int]:
    div = rng.choice([1, 1, 1, 2, 3, 4, 7, rng.randrange(1, 300)])
    bot, top = rng.randrange(64), rng.randrange(64)
    org = rng.randrange(64)
    if rng.random() < 0.6:  # a sensible program window
        org = rng.randrange(0, 56)
        bot = org + rng.randrange(0, 4)
        top = min(63, bot + rng.randrange(0, 16))
    return [
        div,
        rng.randrange(256) if rng.random() < 0.3 else 0,
        R.pack("WRAP", bottom=bot, top=top),
        R.pack("ORG", org=org, jmp_pin=rng.randrange(32)),
        rng.randrange(1 << 15),
        rng.randrange(1 << 16),
        rng.randrange(1 << 13),
        rng.randrange(32) if rng.random() < 0.4 else 0,
        rng.choice([0, 1, 3, 10, rng.randrange(1 << 16)]),
        rng.choice([0xA001, 0x8408, 0x0014, 0x4599, rng.randrange(1 << 16)]),
        rng.randrange(16),
    ]


def make_stimulus(seed: int, cycles: int) -> bytes:
    rng = random.Random(seed)
    s = Stim(rng)
    s.noise = rng.choice([0.002, 0.01, 0.05, 0.2])
    s.reset(5)
    s.idle(3)
    while len(s.data) < 3 * cycles:
        # (Re)load: programs, configuration, pads, interrupt enables.
        s.write(R.IMEM_BASE, [rand_instr(rng) for _ in range(64)], inc=True)
        for k in range(R.NUM_SM):
            s.write(R.addr("CLKDIV_INT", k), rand_config(rng, k), inc=True)
        s.write(R.addr("PAD_OD_LO"), [rng.randrange(1 << 16), rng.randrange(8), rng.randrange(1 << 16),
                                      rng.randrange(8)], inc=True)
        s.write(R.addr("INT_EN"), [rng.randrange(1 << 16)])
        s.write(R.addr("TS_PRESCALE"), [rng.choice([0, 0, 1, 7, rng.randrange(256)])])
        s.write(R.addr("PIN_OUT_LO"), [rng.randrange(1 << 16), rng.randrange(8), rng.randrange(1 << 16),
                                       rng.randrange(8)], inc=True)
        s.write(R.addr("CTRL"), [0xF0 | rng.randrange(16)])
        # Run with random host traffic until the next reload or reset.
        epoch_end = len(s.data) + 3 * rng.randrange(5000, 60000)
        while len(s.data) < min(epoch_end, 3 * cycles):
            half = rng.choice([4, 4, 5, 6, 8])
            k = rng.randrange(R.NUM_SM)
            r = rng.random()
            if r < 0.25:
                s.write(R.addr("FIFO", k), [rng.randrange(1 << 16) for _ in range(rng.randint(1, 5))], half=half)
            elif r < 0.45:
                s.read(R.addr("FIFO", k), rng.randint(1, 4), half=half)
            elif r < 0.55:
                s.read(rng.randrange(256), rng.randint(1, 3), inc=rng.random() < 0.5, half=half)
            elif r < 0.62:
                s.write(R.addr("EXEC", k), [rand_instr(rng)], half=half)
            elif r < 0.66:
                s.write(R.IMEM_BASE + rng.randrange(64), [rand_instr(rng)], half=half)
            elif r < 0.70:
                s.write(R.addr("CTRL"), [rng.randrange(256)], half=half)
            elif r < 0.73:
                s.write(R.addr("IRQ_FORCE"), [rng.randrange(256)], half=half)
                s.write(R.addr("IRQ"), [rng.randrange(256)], half=half)
            elif r < 0.76:
                reg = rng.choice(R.SM_REGS)
                s.write(R.addr(reg.name, k), [rng.randrange(1 << 16)], half=half)
            elif r < 0.79:
                reg = rng.choice(R.GLOBAL_REGS)
                s.write(reg.addr, [rng.randrange(1 << 16)], half=half)
            elif r < 0.795:
                s.write(R.addr("FDEBUG"), [0xFFFF], half=half)
            elif r < 0.80:  # any address, including empty windows, which must ignore the write
                s.write(rng.randrange(256), [rng.randrange(1 << 16) for _ in range(rng.randint(1, 3))],
                        inc=rng.random() < 0.5, half=half)
            elif r < 0.8005:
                s.reset(rng.randrange(1, 10))
                s.idle(3)
                break
            else:
                s.idle(rng.randrange(1, 2000))
    return bytes(s.data[: 3 * cycles])


# ------------------------------------------------------------------------------------------ compare
def run_model(stim: bytes, cov: dict) -> bytearray:
    iss = Bitweaver(coverage=cov)
    out = bytearray()
    uo = uio_out = uio_oe = 0
    for i in range(0, len(stim), 3):
        ui, ext, flags = stim[i], stim[i + 1], stim[i + 2]
        uio_in = (uio_oe & uio_out) | (~uio_oe & ext & 0xFF)
        uo, uio_out, uio_oe = iss.step(ui, uio_in, rst_n=flags & 1, ena=(flags >> 1) & 1)
        out += bytes([uo, uio_out, uio_oe])
    return out


def one_seed(args) -> dict:
    binary, seed, cycles, workdir, covdir = args
    t0 = time.time()
    stim = make_stimulus(seed, cycles)
    sp = os.path.join(workdir, f"stim_{seed}.bin")
    op = os.path.join(workdir, f"rtl_{seed}.bin")
    with open(sp, "wb") as fh:
        fh.write(stim)
    cmd = [binary, sp, op]
    if covdir:
        cmd.append(os.path.join(covdir, f"cov_{seed}.dat"))
    subprocess.run(cmd, check=True)
    with open(op, "rb") as fh:
        rtl = fh.read()
    cov: dict = {}
    model = run_model(stim, cov)
    result = {"seed": seed, "cycles": len(stim) // 3, "ok": True, "seconds": round(time.time() - t0, 1),
              "coverage": {"|".join(map(str, k)): v for k, v in cov.items()}}
    if rtl != model:
        for i in range(0, min(len(rtl), len(model)), 3):
            if rtl[i:i + 3] != model[i:i + 3]:
                result.update(ok=False, cycle=i // 3, rtl=rtl[i:i + 3].hex(), model=model[i:i + 3].hex())
                break
        else:
            result.update(ok=False, cycle=min(len(rtl), len(model)) // 3, rtl="length", model="length")
    else:
        os.unlink(sp)
    os.unlink(op)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bin", required=True)
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--cycles", type=int, default=100000)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--coverage", help="directory for Verilator coverage and functional coverage")
    ap.add_argument("--log", default=os.path.join(HERE, "lockstep_log.jsonl"))
    args = ap.parse_args()
    workdir = os.path.join(HERE, "work")
    os.makedirs(workdir, exist_ok=True)
    covdir = os.path.abspath(args.coverage) if args.coverage else None
    if covdir:
        os.makedirs(covdir, exist_ok=True)
    jobs = [(os.path.abspath(args.bin), s, args.cycles, workdir, covdir)
            for s in range(args.first, args.first + args.seeds)]
    t0 = time.time()
    merged: dict = {}
    total = failed = 0
    with multiprocessing.Pool(args.jobs) as pool, open(args.log, "a") as log:
        for res in pool.imap_unordered(one_seed, jobs):
            for k, v in res.pop("coverage").items():
                merged[k] = merged.get(k, 0) + v
            log.write(json.dumps(res) + "\n")
            total += res["cycles"]
            if not res["ok"]:
                failed += 1
                print(f"FAIL seed {res['seed']} at cycle {res['cycle']}: rtl {res['rtl']} model {res['model']}")
            else:
                print(f"ok   seed {res['seed']}: {res['cycles']} cycles in {res['seconds']} s", flush=True)
    print(f"{args.seeds - failed}/{args.seeds} seeds passed, {total} cycles compared, "
          f"{time.time() - t0:.0f} s wall")
    if covdir:
        with open(os.path.join(covdir, "functional.json"), "w") as fh:
            json.dump(merged, fh, indent=0, sort_keys=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
