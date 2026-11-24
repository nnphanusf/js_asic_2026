"""The host loaders never put the pins in an unsafe or unexpected state.

* Open-drain pins: the loader sets PAD_OD before it sets any pin, so a pin that a program sets high
  (released) is never driven high, not even for one clock cycle while the program is being loaded.
* read_imem: reading instruction memory through an engine moves no pin and keeps its configuration.
"""

import pytest

from bitweaver import regmap as R
from bitweaver.asm import load_program
from bitweaver.loader import Bench, FastDriver, SpiDriver, pad_masks

OPEN_DRAIN_PROGRAMS = [("i2c_ctrl", "i2c"), ("i2c_target", "i2c"), ("onewire", "onewire")]
DRIVERS = {"fast": FastDriver, "spi": lambda b: SpiDriver(b, half=8)}


def watch_open_drain(bench: Bench, od: int) -> list[int]:
    """Record every cycle on which a pin in `od` is driven high (uio_out and uio_oe both 1)."""
    uio_od = (od >> 11) & 0xFF
    bad: list[int] = []

    def hook(b):
        _, uio_out, uio_oe = b.out
        if uio_out & uio_oe & uio_od:
            bad.append(b.cycle)

    bench.hooks.append(hook)
    return bad


@pytest.mark.parametrize("driver", DRIVERS)
@pytest.mark.parametrize("name, file", OPEN_DRAIN_PROGRAMS)
def test_open_drain_pins_never_driven_high_while_loading(name, file, driver):
    prog = load_program(name, file=file)
    od, _ = pad_masks(prog)
    bench = Bench()
    bad = watch_open_drain(bench, od)
    d = DRIVERS[driver](bench)
    d.load(prog, 1, origin=0)
    d.enable(2)
    bench.run(2000)
    assert not bad, f"{name}: open-drain pins driven high on {len(bad)} cycles, first at {bad[0]}"
    # The pins really are set up: released (high through the pull-up) and owned by the engine.
    for pin in prog.config["od_pins"]:
        assert bench.pin(pin) == 1


@pytest.mark.parametrize("driver", DRIVERS)
def test_read_imem_moves_no_pin(driver):
    """spi_ctrl_cpha0 with CPOL = 1 idles SCK (PIN14, a non-optional side-set pin) high."""
    prog = load_program("spi_ctrl_cpha0", file="spi", CPOL=1)
    bench = Bench(trace=True)
    d = DRIVERS[driver](bench)
    d.load(prog, 0, origin=0)
    bench.run(20)
    org, pinctrl1 = d.read(R.addr("ORG", 0))[0], d.read(R.addr("PINCTRL1", 0))[0]
    assert bench.pin(14) == 1
    start = len(bench.trace)
    words = [d.read_imem(a, sm=0) for a in range(len(prog.code))]
    assert words == list(prog.code)
    assert all((p >> 14) & 1 for p in bench.trace[start:]), "SCK moved during read_imem"
    assert d.read(R.addr("ORG", 0))[0] == org
    assert d.read(R.addr("PINCTRL1", 0))[0] == pinctrl1
