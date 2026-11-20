/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver instruction memory: 64 x 16 bits in flip-flops.
 * One write port (host) and one read port per engine. There is no host read-back port: each read port
 * is a 16-bit 64:1 mux fed by all 1024 bits, the densest wiring in the design.
 * The engine ports are write-first: an engine that fetches the address being written this cycle
 * gets the new word, so an engine's instruction register always equals memory[PC]. The ports read
 * each word's next value (its flip-flop's D input), which is the write-first value by construction,
 * so no address compare or bypass mux sits after the 64:1 read mux on the fetch path.
 */

`default_nettype none

module bw_imem #(
    parameter NUM_SM = 2
) (
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire                 we,
    input  wire [5:0]           waddr,
    input  wire [15:0]          wdata,
    input  wire [NUM_SM*6-1:0]  sm_raddr,
    output wire [NUM_SM*16-1:0] sm_rdata
);

  // nxt holds every word's next value, word a at bits [a*16 +: 16].
  wire [64*16-1:0] nxt;

  genvar a, g;
  generate
    for (a = 0; a < 64; a = a + 1) begin : g_word
      reg [15:0] q;
      assign nxt[a*16+:16] = (we && waddr == a) ? wdata : q;
      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 16'd0;
        else q <= nxt[a*16+:16];
      end
    end
    for (g = 0; g < NUM_SM; g = g + 1) begin : g_port
      wire [5:0] ra = sm_raddr[g*6+:6];
      assign sm_rdata[g*16+:16] = nxt[ra*16+:16];
    end
  endgenerate


endmodule
