"""The host loaders never put the pins in an unsafe or unexpected state.

* Open-drain pins: the loader sets PAD_OD before it sets any pin, so a pin that a program sets high
  (released) is never driven high, not even for one clock cycle while the program is being loaded.
* read_imem: reading instruction memory through an engine moves no pin and keeps its configuration.
* Hand-over: loading a program onto an engine releases the pins of the program it replaces and
  clears the pad modes only that program needed; other engines keep theirs.
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


def _pad_od(d) -> int:
    lo, hi = d.read(R.addr("PAD_OD_LO"), 2, inc=True)
    return lo | (hi << 16)


def _driven(bench: Bench, pin: int) -> bool:
    _, _, uio_oe = bench.out
    return bool((uio_oe >> (pin - 11)) & 1)


@pytest.mark.parametrize("driver", DRIVERS)
def test_switching_protocols_hands_the_pins_over(driver):
    """I2C, then SPI controller, then SPI target on one engine: each load releases the pins the
    previous program drove and clears the open-drain bits it alone needed."""
    bench = Bench()
    d = DRIVERS[driver](bench)
    d.load(load_program("i2c_ctrl", file="i2c"), 0, origin=0)
    d.enable(1)
    bench.run(200)
    assert _pad_od(d) == (1 << 13) | (1 << 14)
    ctrl = load_program("spi_ctrl_cpha0", file="spi", CPOL=1)
    d.load(ctrl, 0, origin=0)
    d.enable(1)
    bench.run(200)
    assert _pad_od(d) == 0  # SCK (PIN14) is push-pull again
    assert _driven(bench, 14) and bench.pin(14) == 1  # SCK idles high (CPOL = 1), actively driven
    assert not _driven(bench, 13)  # MISO, the old SDA, is an input now
    d.load(load_program("spi_target", file="spi"), 0, origin=0)
    d.enable(1)
    bench.run(200)
    assert not any(_driven(bench, p) for p in (11, 12, 13, 14))  # CS, MOSI, SCK in; MISO released


@pytest.mark.parametrize("driver", DRIVERS)
def test_release_keeps_the_other_engine(driver):
    """Freeing one engine leaves the other engine's pins and pad modes alone."""
    bench = Bench()
    d = DRIVERS[driver](bench)
    d.load(load_program("i2c_ctrl", file="i2c"), 1, origin=32)
    d.load(load_program("ps2_rx", file="ps2"), 0, origin=0)
    d.enable(0b11)
    bench.run(200)
    assert _pad_od(d) == 0xF << 13
    d.release(0)
    bench.run(50)
    assert _pad_od(d) == (1 << 13) | (1 << 14)
    lo, hi = d.read(R.addr("PAD_FILT_LO"), 2, inc=True)
    assert lo | (hi << 16) == 0
    assert d.read(R.addr("CTRL"))[0] & 3 == 0b10  # SM1 still running
    assert bench.pin(13) == 1 and bench.pin(14) == 1


UART_ON_PIN11 = dict(out_base=11, set_base=11, side_base=11)


@pytest.mark.parametrize("driver", DRIVERS)
@pytest.mark.parametrize("name, file, pins, moved", [("uart_tx", "uart", (9,), {}),
                                                     ("uart_tx", "uart", (11,), UART_ON_PIN11),
                                                     ("spi_ctrl_cpha0", "spi", (11, 12, 14), {})])
def test_reloading_an_engine_moves_no_pin(name, file, pins, moved, driver):
    """Reloading an engine (another divider, say) leaves the pins the program sets up at their level:
    no release in between, which would put a start bit on a UART line or an edge on SPI's SCK. This
    holds for pins set up by `.init set pindirs` too (uart_tx), also moved onto PIN11..PIN18. The
    bidirectional pads are pulled low here, so a released pin would show."""
    bench = Bench(trace=True)
    bench.uio_ext = 0x00
    d = DRIVERS[driver](bench)
    prog = load_program(name, file=file)
    d.load(prog, 0, origin=0, clkdiv=2, **moved)
    d.enable(1)
    bench.run(100)
    d.disable(1)
    start = len(bench.trace)
    d.load(prog, 0, origin=0, clkdiv=15, **moved)
    d.enable(1)
    bench.run(50)
    for pin in pins:
        levels = {(p >> pin) & 1 for p in bench.trace[start:]}
        assert len(levels) == 1, f"PIN{pin} moved while the engine was reloaded"
