#!/usr/bin/env python3
"""Render a timing diagram of four engines running four protocols at once, from the simulator.

    python3 scripts/wave_svg.py docs/images/four_protocols.svg
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sw"))

from bitweaver.asm import load_program  # noqa: E402
from bitweaver.loader import Bench, FastDriver  # noqa: E402
from bitweaver.proto import JtagSeq, spi_frame, ws2812_words  # noqa: E402

b = Bench(trace=True)
d = FastDriver(b)
d.load(load_program("ws2812"), 0)  # PIN5
d.load(load_program("uart_tx"), 1, clkdiv=4)  # PIN9, 1.875 Mbaud for the picture
d.load(load_program("spi_ctrl_cpha0", file="spi"), 2, clkdiv=4)  # PIN11..14
d.load(load_program("jtag"), 3, clkdiv=3, out_base=7, side_base=6, in_base=0, dirs_pins=[6, 7, 8])  # TCK 6, TDI 7, TMS 8
seq = JtagSeq()
seq.reset()
seq.shift_ir(0b1110, 4)
d.push(0, ws2812_words([(0xFF, 0x00, 0x81)]))
d.push(1, [0x55, 0xA3])
d.push(2, spi_frame(b"\x9f\xc3"))
d.push(3, seq.words()[:2])
start = len(b.trace)
d.enable(0b1111)
b.run(1800)
trace = b.trace[start:]

signals = [("WS2812  (SM0)", 5), ("UART TX (SM1)", 9), ("SPI CS  (SM2)", 11), ("SPI MOSI", 12), ("SPI SCK", 14),
           ("JTAG TCK (SM3)", 6), ("JTAG TDI", 7), ("JTAG TMS", 8)]
W, ROW, LEFT, H = 1100, 34, 150, 18
scale = (W - LEFT - 20) / len(trace)
out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{ROW * len(signals) + 50}" '
       f'font-family="monospace" font-size="12">',
       '<rect width="100%" height="100%" fill="white"/>']
for i, (name, pin) in enumerate(signals):
    y0 = 20 + i * ROW
    out.append(f'<text x="8" y="{y0 + H - 4}" fill="#222">{name}</text>')
    def ylev(v):
        return y0 + (2 if v else H)

    prev = (trace[0] >> pin) & 1
    pts = [f"{LEFT:.1f},{ylev(prev)}"]
    for k, t in enumerate(trace):
        v = (t >> pin) & 1
        if v != prev:
            x = LEFT + k * scale
            pts += [f"{x:.1f},{ylev(prev)}", f"{x:.1f},{ylev(v)}"]
            prev = v
    pts.append(f"{LEFT + len(trace) * scale:.1f},{ylev(prev)}")
    out.append(f'<polyline fill="none" stroke="#1f5fbf" stroke-width="1.4" points="{" ".join(pts)}"/>')
us = len(trace) / 60
out.append(f'<text x="{LEFT}" y="{ROW * len(signals) + 40}" fill="#555">0 us</text>')
out.append(f'<text x="{W - 90}" y="{ROW * len(signals) + 40}" fill="#555">{us:.0f} us</text>')
out.append(f'<text x="{(LEFT + W) / 2 - 170}" y="{ROW * len(signals) + 40}" fill="#555">'
           f'four engines, four protocols, one 60 MHz clock (cycle-accurate simulator)</text>')
out.append("</svg>")
path = sys.argv[1] if len(sys.argv) > 1 else "four_protocols.svg"
with open(path, "w") as fh:
    fh.write("\n".join(out))
print(path, os.path.getsize(path), "bytes")
