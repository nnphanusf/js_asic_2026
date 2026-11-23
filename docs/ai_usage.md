# How AI was used

The competition asks for a description of how AI was used. This design was built with heavy use of an AI coding
agent that had direct access to the repository and a local toolchain. This page says what the agent did, what the
human did, and how the work was checked by tools rather than taken on trust.

## Division of work

**Human (project owner):**
- Chose the competition, the architecture direction (a multi-engine, PIO-style bit-banging core), the language
  (Verilog with cocotb) and the 8x4 tile budget, and wrote the brief and the project rules the agent worked from.
- Created the GitHub repository and accounts, enabled Actions and GitHub Pages, and owns the submission.
- Reviews the progress log (`PROGRESS.md`) at phase boundaries.

**AI agent:**
- Wrote the register map, instruction-set simulator, assembler and loader (`sw/bitweaver`), the RTL (`src/`), the
  testbenches (`test/`), the formal properties (`formal/`), the example programs (`sw/programs/`) and the
  documentation, working from the brief.
- Ran the local tools (Icarus Verilog, Verilator, Yosys, SymbiYosys, cocotb) and read the results of the GitHub
  Actions runs, and fixed what they reported.
- Diagnosed the failed hardening runs from the LibreLane logs and chose the fixes: cutting to two engines (the
  brief's cut order), the timing changes in the RTL and flow settings, and the 50 MHz clock (the brief's fallback).
- Reviewed the finished design for edge cases on real hardware at the owner's request, and fixed what it found in
  the software and documentation (host SPI speed, dividers for the 50 MHz clock).
- Kept `PROGRESS.md` as a running log of decisions and their reasons.

The agent worked from the brief with one instruction from the human: run all phases in one go and stop only for
account or payment matters or changes to the fixed facts (top module, tiles, pins, deadline). Decisions it made on
its own are logged with their reasons in `PROGRESS.md`.

## What was checked by tools instead of trusted

The agent's output was treated as unverified until a tool agreed with it:

- **Two descriptions of the design, compared on every cycle.** The Python simulator was written first, as the
  executable specification; the RTL was written afterwards by the same agent, deliberately following the
  simulator's structure. The lock-step testbench runs both on the same inputs and requires identical outputs on
  every pin on every clock cycle. This proves the RTL implements the model exactly; it cannot show that the model
  itself is right, which is why the next check exists.
- **Independent protocol models.** Protocol programs are judged by third-party models (`cocotbext-uart`,
  `cocotbext-spi`) or by models written separately from the firmware, never only by the simulator.
- **Formal proofs** (SymbiYosys) for the FIFO, the host port, engine invariants, the clock divider and open-drain
  safety, and a Yosys equivalence proof for the instruction-memory timing change.
- **The Tiny Tapeout flow** (LibreLane hardening, precheck, gate-level simulation of the routed netlist) and static
  timing analysis decide whether the chip is buildable; the agent's area and timing estimates are only estimates.

## Limits

The agent can still make mistakes that every check above shares, for example misreading the specification the same
way in both the simulator and the RTL. Independent protocol models catch most of these for the protocols tested. No
silicon has been tested yet.
