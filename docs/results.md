# Results

Area and timing numbers, newest first. Cell counts from Yosys generic synthesis unless noted; GDS numbers come from
the `gds` workflow's LibreLane reports.

| Date | Design state | Cells | Notes |
|---|---|---|---|
| 2026-11-17 | Same, after GDS (commit 3627c90) | 300 IHP cells | 8x4 tiles at 60 MHz: utilisation 0.408 %, wire length 6576 um, 54 flip-flops, no setup or hold violations. Precheck all green (SG13CMOS5L DRC, pin, boundary, layer, cell name checks). GDS 54 min, precheck 22 min on GitHub runners. |
| 2026-11-16 | Phase 0 skeleton + SPI bring-up probe | 138 (generic) | Every input pin now drives logic. The first GDS run failed with "19 critical disconnected pins" because the all-idle skeleton had no logic. |
