# AI use

The competition asks entrants to describe any use of AI. This project used an AI coding assistant, with access to
the repository and a local toolchain, to produce the implementation from a written brief. This page describes the
roles and how the results were verified.

## Roles

**Project owner:**
- Set the direction: the competition entry, the multi-engine PIO-style architecture, Verilog with cocotb, the 8x4
  tile budget, and the fixed facts (top module, pin plan, deadline).
- Wrote the brief and the project rules, and reviewed the progress log (`PROGRESS.md`) at each phase boundary.
- Owns the repository, the accounts and the submission.

**AI coding assistant:**
- Produced the register map, instruction-set simulator, assembler and loader (`sw/bitweaver`), the RTL (`src/`),
  the testbenches (`test/`), the formal properties (`formal/`), the example programs (`sw/programs/`) and the
  documentation.
- Ran the local tools (Icarus Verilog, Verilator, Yosys, SymbiYosys, cocotb), read the GitHub Actions results and
  fixed what they reported.
- Chose fixes for the hardening runs within the limits set by the brief: two engines (the brief's cut order), the
  timing changes in the RTL and flow settings, and the 50 MHz clock (the brief's fallback).
- Reviewed the finished design for edge cases on real hardware, which led to the host SPI speed limit and the
  dividers for the 50 MHz clock.
- Reproduced and fixed the findings of the two independent reviews below.

Decisions and their reasons are logged in `PROGRESS.md`.

## Verification

Generated code and text were treated as unverified until a tool agreed with them:

- **Two descriptions of the design, compared on every cycle.** The Python simulator was written first, as the
  executable specification, and the RTL afterwards, following the simulator's structure. The lock-step testbench
  runs both on the same inputs and requires identical outputs on every pin on every clock cycle. This proves the
  RTL implements the model exactly; it cannot show that the model itself is right, which is why the next check
  exists.
- **Independent protocol models.** Protocol programs are judged by third-party models (`cocotbext-uart`,
  `cocotbext-spi`) or by models written separately from the firmware, never only by the simulator.
- **Formal proofs** (SymbiYosys) for the FIFO, the host port, engine invariants, the clock divider and open-drain
  safety, and a Yosys equivalence proof for the instruction-memory timing change.
- **The Tiny Tapeout flow** (LibreLane hardening, precheck, gate-level simulation of the routed netlist) and static
  timing analysis decide whether the chip is buildable; earlier area and timing estimates are only estimates.
- **Independent reviews.** The project owner had a second AI assistant, with no part in writing the code, audit
  the finished repository from a written brief (2026-11-24), and then had another assistant audit the result
  (same day, commit 68e38d0). Neither found an RTL defect. Their findings in the protocol programs, the host
  software, the testbench and the documentation were each reproduced first, then fixed with a test that fails
  without the fix (`PROGRESS.md`). Where the second review quoted numbers from build logs that could not be
  downloaded here (post-route slack, fanout warnings, the routed critical path), the documentation says so.

## Limits

A mistake shared by every check above can still get through, for example the same misreading of the
specification in both the simulator and the RTL. Independent protocol models catch most of these for the protocols
tested. No silicon has been tested yet.
