# Constrained-random lock-step: Verilated RTL against the Python reference model.
#   make            build the harness
#   make run SEEDS=100 CYCLES=100000 JOBS=8
#   make cov        build with Verilator line/toggle coverage and write coverage/
VERILATOR ?= verilator
SRC := $(abspath ../../src)
SOURCES := $(addprefix $(SRC)/,project.v bw_host_spi.v bw_imem.v bw_clkdiv.v bw_fifo.v bw_bau.v bw_sm.v)
VFLAGS := --cc --exe --build -O3 --relative-includes --top-module tt_um_nnphanusf_bitweaver -Wno-fatal \
          -CFLAGS -O2 --x-assign unique --x-initial unique
SEEDS ?= 100
CYCLES ?= 100000
FIRST ?= 1
JOBS ?= 8

.PHONY: all run cov clean
all: obj/Vtt_um_nnphanusf_bitweaver

obj/Vtt_um_nnphanusf_bitweaver: harness.cpp $(SOURCES)
	$(VERILATOR) $(VFLAGS) --Mdir obj $(SOURCES) harness.cpp -o Vtt_um_nnphanusf_bitweaver

obj_cov/Vtt_um_nnphanusf_bitweaver: harness.cpp $(SOURCES)
	$(VERILATOR) $(VFLAGS) --coverage-line --coverage-toggle --Mdir obj_cov $(SOURCES) harness.cpp \
	  -o Vtt_um_nnphanusf_bitweaver

run: obj/Vtt_um_nnphanusf_bitweaver
	python3 run_lockstep.py --bin obj/Vtt_um_nnphanusf_bitweaver --first $(FIRST) --seeds $(SEEDS) \
	  --cycles $(CYCLES) --jobs $(JOBS)

cov: obj_cov/Vtt_um_nnphanusf_bitweaver
	python3 run_lockstep.py --bin obj_cov/Vtt_um_nnphanusf_bitweaver --first $(FIRST) --seeds $(SEEDS) \
	  --cycles $(CYCLES) --jobs $(JOBS) --coverage coverage

clean:
	rm -rf obj obj_cov coverage work
