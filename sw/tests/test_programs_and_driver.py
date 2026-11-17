"""Every shipped program assembles; the MicroPython board driver works against the simulator."""

import glob
import importlib
import os
import sys
import types

import pytest

from bitweaver.asm import assemble_file
from bitweaver.loader import Bench, build_image

HERE = os.path.dirname(__file__)
PROGRAMS = sorted(glob.glob(os.path.join(HERE, "..", "programs", "*.bw")))


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: os.path.basename(p))
def test_program_assembles(path):
    progs = assemble_file(path)
    assert progs
    for p in progs.values():
        assert 1 <= len(p.code) <= 64
        assert 0 <= p.wrap_target <= p.wrap < len(p.code)


def test_total_size_of_concurrency_demo_fits():
    names = {"uart_tx": "uart", "uart_rx": "uart", "spi_ctrl_cpha0": "spi", "i2c_ctrl": "i2c"}
    total = 0
    for name, f in names.items():
        total += len(assemble_file(os.path.join(HERE, "..", "programs", f + ".bw"))[name].code)
    assert total <= 64


@pytest.fixture
def driver():
    """The MicroPython driver with ttboard.util.platform backed by the cycle-accurate simulator."""
    bench = Bench(trace=True)
    state = {"ui": 0x80}

    def write_ui_in_byte(v):
        state["ui"] = v & 0xFF
        for _ in range(4):  # the RP2 is much slower than the design clock
            ui = state["ui"]
            bench.step((ui >> 7) & 1, (ui >> 6) & 1, (ui >> 5) & 1)

    def read_uo_out_byte():
        return bench.out[0]

    platform = types.ModuleType("ttboard.util.platform")
    platform.write_ui_in_byte = write_ui_in_byte
    platform.read_uo_out_byte = read_uo_out_byte
    pkg = types.ModuleType("ttboard")
    util = types.ModuleType("ttboard.util")
    pkg.util, util.platform = util, platform
    sys.modules.update({"ttboard": pkg, "ttboard.util": util, "ttboard.util.platform": platform})
    # sw/host/micropython/bitweaver.py has the package's name; load it by path.
    import importlib.util
    spec = importlib.util.spec_from_file_location("bw_board", os.path.join(HERE, "..", "host", "micropython",
                                                                          "bitweaver.py"))
    board = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(board)
    yield board.Bitweaver(), bench
    for k in ("ttboard", "ttboard.util", "ttboard.util.platform"):
        sys.modules.pop(k, None)


def test_board_driver_uart(driver):
    bw, bench = driver
    assert bw.read(0x00) == [0xB717]
    assert bw.first_bytes[0] == 0xB7
    image = build_image(os.path.join(HERE, "..", "programs", "uart.bw"), "uart_tx", sm=0, clkdiv=2)
    bw.load_image(image)
    bw.enable(1)
    start = len(bench.trace)
    bw.push(0, b"Hi")
    bench.run(400)
    line = [(t >> 9) & 1 for t in bench.trace[start:]]  # PIN9 = uo_out[4]
    # Decode 8N1 at 16 cycles per bit (8 ticks of 2 cycles).
    got, i = [], 0
    while i < len(line) - 160 and len(got) < 2:
        if line[i] == 0 and line[i - 1] == 1:
            got.append(sum(line[i + 8 + 16 * (k + 1)] << k for k in range(8)))
            i += 150
        i += 1
    assert bytes(got) == b"Hi"
