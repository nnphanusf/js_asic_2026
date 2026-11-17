# Results

Area and timing numbers, newest first. Cell counts from Yosys generic synthesis unless noted; GDS numbers come from
the `gds` workflow's LibreLane reports.

| Date | Design state | Cells | Notes |
|---|---|---|---|
| 2026-11-17 | Full cluster RTL: 4 engines with BAU, 64x16 IMEM, host SPI, pin mux (commit before first full GDS) | 27412 IHP cells (Yosys + ABC, typ corner) | Area 385815 um2, about 39 % of the 8x4 core. Hierarchy before flattening (generic cells): IMEM 12134 (1024 flops + four 64:1 read ports), one engine 4061 (FIFO 1056, BAU 220, divider 197), host port 230, top level 2150. ABC critical-path estimate 9.07 ns at the slow 1.08 V / 125 C corner, without wire load (target 16.67 ns). |
| 2026-11-17 | Same, after GDS (commit 3627c90) | 300 IHP cells | 8x4 tiles at 60 MHz: utilisation 0.408 %, wire length 6576 um, 54 flip-flops, no setup or hold violations. Precheck all green (SG13CMOS5L DRC, pin, boundary, layer, cell name checks). GDS 54 min, precheck 22 min on GitHub runners. |
| 2026-11-16 | Phase 0 skeleton + SPI bring-up probe | 138 (generic) | Every input pin now drives logic. The first GDS run failed with "19 critical disconnected pins" because the all-idle skeleton had no logic. |
