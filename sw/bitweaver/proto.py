"""Host-side helpers for the example protocol programs in sw/programs.

They only build and parse FIFO words; any transport (simulator, cocotb, MicroPython) can send them.
"""

from __future__ import annotations


# ---------------------------------------------------------------- UART (uart.bw)
def uart_clkdiv(baud: float, f_clk: float = 60e6) -> float:
    """Clock divider for uart_tx / uart_rx: 8 ticks per bit."""
    return f_clk / (8 * baud)


def uart_parity_word(byte: int, odd: bool = False) -> int:
    """9-bit word for the DATA_BITS = 9 variants: data plus an even (or odd) parity bit."""
    p = (bin(byte & 0xFF).count("1") & 1) ^ int(odd)
    return (byte & 0xFF) | (p << 8)


# ---------------------------------------------------------------- SPI (spi.bw)
def spi_frame(data) -> list[int]:
    """TX FIFO words for one spi_ctrl_cpha0/1 frame: the length minus one, then the bytes."""
    data = list(data)
    if not data:
        raise ValueError("an SPI frame needs at least one byte")
    return [len(data) - 1] + [b & 0xFF for b in data]


def spi_clkdiv(f_sck: float, f_clk: float = 60e6) -> float:
    """Clock divider for the SPI controller: 4 ticks per bit."""
    return f_clk / (4 * f_sck)


# ---------------------------------------------------------------- I2C (i2c.bw)
def i2c_cmd(byte: int, ack: int = 1, start: bool = False, stop: bool = False) -> int:
    """One i2c_ctrl command word."""
    return (int(start) << 15) | ((byte & 0xFF) << 7) | ((ack & 1) << 6) | (int(stop) << 5)


def i2c_write_cmds(address: int, data=(), stop: bool = True) -> list[int]:
    """START, address with W, data bytes, optional STOP."""
    data = list(data)
    words = [i2c_cmd(address << 1, start=True, stop=stop and not data)]
    words += [i2c_cmd(b, stop=stop and i == len(data) - 1) for i, b in enumerate(data)]
    return words


def i2c_read_cmds(address: int, count: int, stop: bool = True) -> list[int]:
    """(Repeated) START, address with R, `count` reads (ACK all but the last, NACK the last)."""
    words = [i2c_cmd((address << 1) | 1, start=True)]
    words += [i2c_cmd(0xFF, ack=int(i == count - 1), stop=stop and i == count - 1) for i in range(count)]
    return words


def i2c_result(word: int) -> tuple[int, bool]:
    """(byte seen on SDA, acknowledged) for one i2c_ctrl RX word."""
    return (word >> 1) & 0xFF, (word & 1) == 0


def i2c_clkdiv(f_scl: float, f_clk: float = 60e6) -> int:
    """Clock divider for i2c_ctrl (10 ticks per bit, SCL low for 5) at or below `f_scl`.

    Rounds up so the bus never runs faster than asked, and keeps tLOW (5 ticks) at or above the
    I2C minimum: 4.7 us up to 100 kHz, 1.3 us above. 100 kHz -> 60, 400 kHz -> 16 (375 kHz).
    """
    import math
    t_low = 4.7e-6 if f_scl <= 100e3 else 1.3e-6
    return max(math.ceil(f_clk / (10 * f_scl) - 1e-9), math.ceil(t_low * f_clk / 5 - 1e-9))
