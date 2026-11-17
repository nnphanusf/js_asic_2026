// Bitweaver lock-step harness: replays a stimulus file cycle by cycle on the Verilated RTL and writes
// the outputs after every rising clock edge. run_lockstep.py replays the same stimulus on the Python
// reference model and compares the two output streams.
//
// Stimulus: 3 bytes per cycle: ui_in, uio_ext (what the outside world drives onto the bidirectional
// pads, 1 = released), flags (bit 0 rst_n, bit 1 ena). The pads are modelled as in test/tb.v:
// uio_in = (uio_oe & uio_out) | (~uio_oe & uio_ext), computed from the outputs before the edge.
// Output: 3 bytes per cycle: uo_out, uio_out, uio_oe.
#include <cstdio>
#include <cstdlib>
#include <vector>
#include "Vtt_um_nnphanusf_bitweaver.h"
#include "verilated.h"
#if VM_COVERAGE
#include "verilated_cov.h"
#endif

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    if (argc < 3) {
        fprintf(stderr, "usage: %s stimulus.bin outputs.bin [coverage.dat]\n", argv[0]);
        return 2;
    }
    FILE* in = fopen(argv[1], "rb");
    FILE* out = fopen(argv[2], "wb");
    if (!in || !out) {
        perror("open");
        return 2;
    }
    std::vector<unsigned char> stim;
    unsigned char buf[65536];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, in)) > 0) stim.insert(stim.end(), buf, buf + n);
    fclose(in);

    Vtt_um_nnphanusf_bitweaver* top = new Vtt_um_nnphanusf_bitweaver;
    top->clk = 0;
    top->rst_n = 0;
    top->ena = 1;
    top->ui_in = 0x80;
    top->uio_in = 0xFF;
    top->eval();
    std::vector<unsigned char> res;
    res.reserve(stim.size());
    for (size_t i = 0; i + 2 < stim.size(); i += 3) {
        unsigned ui = stim[i], ext = stim[i + 1], flags = stim[i + 2];
        top->ui_in = ui;
        top->rst_n = flags & 1;
        top->ena = (flags >> 1) & 1;
        top->uio_in = (top->uio_oe & top->uio_out) | (~top->uio_oe & ext);
        top->clk = 0;
        top->eval();
        top->clk = 1;
        top->eval();
        res.push_back(top->uo_out);
        res.push_back(top->uio_out);
        res.push_back(top->uio_oe);
    }
    fwrite(res.data(), 1, res.size(), out);
    fclose(out);
    top->final();
#if VM_COVERAGE
    if (argc > 3) Verilated::threadContextp()->coveragep()->write(argv[3]);
#endif
    delete top;
    return 0;
}
