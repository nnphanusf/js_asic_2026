# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v bw_host_spi.v bw_imem.v bw_clkdiv.v bw_fifo.v bw_bau.v bw_sm.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
# Cell models for the PDK in $(PDK) (ihp-sg13cmos5l or ihp-sg13g2), e.g. sg13cmos5l_stdcell.v
PDK ?= ihp-sg13cmos5l
PDK_CELL_PREFIX = $(patsubst ihp-%,%,$(PDK))
PDK_LIBS_REF = $(PDK_ROOT)/$(PDK)/libs.ref
VERILOG_SOURCES += $(PDK_LIBS_REF)/$(PDK_CELL_PREFIX)_io/verilog/$(PDK_CELL_PREFIX)_io.v
# Newer PDK versions ship the UDP (user-defined primitive) models in a separate file
VERILOG_SOURCES += $(wildcard $(PDK_LIBS_REF)/$(PDK_CELL_PREFIX)_stdcell/verilog/$(PDK_CELL_PREFIX)_udp.v)
VERILOG_SOURCES += $(PDK_LIBS_REF)/$(PDK_CELL_PREFIX)_stdcell/verilog/$(PDK_CELL_PREFIX)_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Waveforms are off by default (the full suite runs millions of cycles); use `make WAVES=1` to dump tb.fst.
ifneq ($(WAVES),1)
COMPILE_ARGS    += -DNO_DUMP
endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# Python path: the shared testbench library and the Bitweaver toolchain.
export PYTHONPATH := $(PWD)/lib:$(PWD)/../sw:$(PYTHONPATH)

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES ?= test_smoke,test_isa,test_proto_uart,test_proto_spi,test_proto_i2c,test_proto_concurrent,test_proto_ws2812_jtag,test_proto_swd_ps2,test_proto_can,test_proto_usb,test_proto_onewire

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
