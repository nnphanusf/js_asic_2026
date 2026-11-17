# Results

Area and timing numbers, newest first. Cell counts from Yosys generic synthesis unless noted; GDS numbers come from
the `gds` workflow's LibreLane reports.

| Date | Design state | Cells | Notes |
|---|---|---|---|
| 2026-11-16 | Phase 0 skeleton + SPI bring-up probe | 138 (generic) | Every input pin now drives logic. The first GDS run failed with "19 critical disconnected pins" because the all-idle skeleton had no logic. |
