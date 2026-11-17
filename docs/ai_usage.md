# How AI was used

This design was built with heavy use of an AI coding agent, Claude (Anthropic), running in Claude Code with direct
access to the repository and a local toolchain. This page says what the agent did, what the human did, and how the
work was checked by tools rather than taken on trust.

## Division of work

**Human (project owner):**
- Chose the competition, the architecture direction (a multi-engine, PIO-style bit-banging core), the language
  (Verilog with cocotb) and the 8x4 tile budget, and wrote the brief (`PLAN.md`) and project rules (`CLAUDE.md`).
- Created the GitHub repository and accounts, enabled Actions and GitHub Pages, and owns the submission.
- Reviews the progress log (`PROGRESS.md`) at phase boundaries.

**AI agent:**
- Wrote the register map, instruction-set simulator, assembler and loader (`sw/bitweaver`), the RTL (`src/`), the
  testbenches (`test/`), the formal properties (`formal/`), the example programs (`sw/programs/`) and the
  documentation, working from the brief.
- Ran the local tools (Icarus Verilog, Verilator, Yosys, SymbiYosys, cocotb) and read the results of the GitHub
  Actions runs, and fixed what they reported.
- Kept `PROGRESS.md` as a running log of decisions and their reasons.

Models used: Claude Fable 5.2 for the early sessions (toolchain, simulator, first RTL) and Claude Opus 5.5 from the
lock-step testbench onwards.

## What was checked by tools instead of trusted

The agent's output was treated as unverified until a tool agreed with it:

- **Two independent descriptions of the design.** The Python simulator and the RTL were written separately, each
  from the specification. The lock-step testbench runs both on the same inputs and requires identical outputs on
  every pin on every clock cycle. Disagreements are investigated; each one is either a model bug or an RTL bug, and
  the fix is recorded in `docs/verification.md`.
- **Independent protocol models.** Protocol programs are judged by third-party models (`cocotbext-uart`,
  `cocotbext-spi`) or by models written separately from the firmware, never only by the simulator.
- **Formal proofs** (SymbiYosys) for the FIFO, the host port and open-drain safety.
- **The Tiny Tapeout flow** (LibreLane hardening, precheck, gate-level simulation of the routed netlist) and static
  timing analysis decide whether the chip is buildable; the agent's area and timing estimates are only estimates.

## Limits

The agent can still make mistakes that every check above shares, for example misreading the specification the same
way in both the simulator and the RTL. Independent protocol models catch most of these for the protocols tested. No
silicon has been tested yet.
