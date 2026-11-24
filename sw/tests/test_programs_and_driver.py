"""Every shipped program assembles; the MicroPython board driver works against the simulator."""

import glob
import importlib
import os
import sys
import types

import pytest

from bitweaver import regmap as R
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


@pytest.mark.parametrize("pair", [("spi_ctrl_cpha0", "spi", "i2c_ctrl", "i2c"),
                                  ("i2c_cond", "i2c", "i2c_target", "i2c"),
                                  ("usb_se0", "usb", "usb_rx", "usb"),
                                  ("can_tx", "can", "can_rx", "can")])
def test_two_engine_pairs_fit_in_memory(pair):
    """Every pair of programs the tests run side by side fits in the 64-word instruction memory."""
    a, fa, b, fb = pair
    total = sum(len(assemble_file(os.path.join(HERE, "..", "programs", f + ".bw"))[n].code)
                for n, f in ((a, fa), (b, fb)))
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


def _image(file, program, sm, origin=0, **kw):
    return build_image(os.path.join(HERE, "..", "programs", file + ".bw"), program, sm=sm, origin=origin, **kw)


def test_board_driver_open_drain_set_before_pins(driver):
    """The image's open-drain mask is applied before any pin is set: no cycle drives SCL/SDA high."""
    bw, bench = driver
    image = _image("i2c", "i2c_ctrl", sm=0)
    assert image["od"] == (1 << 13) | (1 << 14)
    bad = []
    bench.hooks.append(lambda b: bad.append(b.cycle) if b.out[1] & b.out[2] & 0x0C else None)
    bw.load_image(image)
    bw.enable(1)
    bench.run(500)
    assert not bad
    assert bench.pin(13) == 1 and bench.pin(14) == 1


def test_board_driver_second_engine_keeps_the_first(driver):
    """Loading SM1 leaves a running SM0 enabled and keeps its open-drain and filter pins."""
    bw, bench = driver
    bw.load_image(_image("ps2", "ps2_rx", sm=0))
    bw.enable(0b01)
    bw.load_image(_image("i2c", "i2c_ctrl", sm=1, origin=32))
    assert bench.iss.peek(R.addr("CTRL")) == 0b01
    assert bw.en == 0b01
    od = bench.iss.peek(R.addr("PAD_OD_LO")) | (bench.iss.peek(R.addr("PAD_OD_HI")) << 16)
    filt = bench.iss.peek(R.addr("PAD_FILT_LO")) | (bench.iss.peek(R.addr("PAD_FILT_HI")) << 16)
    assert od == (1 << 13) | (1 << 14) | (1 << 15) | (1 << 16)
    assert filt == (1 << 15) | (1 << 16)
    bw.enable(0b10)
    assert bench.iss.peek(R.addr("CTRL")) == 0b11


def test_board_driver_read_imem_moves_no_pin(driver):
    bw, bench = driver
    image = _image("spi", "spi_ctrl_cpha0", sm=0, defines={"CPOL": 1})
    bw.load_image(image)
    bench.run(20)
    assert bench.pin(14) == 1
    start = len(bench.trace)
    code = assemble_file(os.path.join(HERE, "..", "programs", "spi.bw"), {"CPOL": 1})["spi_ctrl_cpha0"].code
    assert [bw.read_imem(a) for a in range(4)] == list(code[:4])
    assert all((p >> 14) & 1 for p in bench.trace[start:])


def test_board_driver_switches_protocols_cleanly(driver):
    """I2C then SPI on one engine: the old SDA is released and SCK is push-pull, not open drain."""
    bw, bench = driver
    bw.load_image(_image("i2c", "i2c_ctrl", sm=0))
    bw.enable(1)
    bench.run(200)
    bw.load_image(_image("spi", "spi_ctrl_cpha0", sm=0, defines={"CPOL": 1}))
    bw.enable(1)
    bench.run(200)
    assert bench.iss.peek(R.addr("PAD_OD_LO")) == 0
    uio_oe = bench.out[2]
    assert (uio_oe >> 3) & 1 and bench.pin(14) == 1  # SCK driven high
    assert not (uio_oe >> 2) & 1  # PIN13 (MISO) is an input
    bw.release(0)
    bench.run(20)
    assert bench.out[2] == 0 and bw.images == {}


def test_board_driver_rejects_old_images(driver):
    """An image from an older loader is refused before anything is written to the chip."""
    bw, bench = driver
    image = _image("i2c", "i2c_ctrl", sm=0)
    for legacy in ({k: v for k, v in image.items() if k != "format"}, dict(image, format=1)):
        cycle = bench.cycle
        with pytest.raises(ValueError, match="rebuild"):
            bw.load_image(legacy)
        assert bench.cycle == cycle
