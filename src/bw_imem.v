/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver instruction memory: 64 x 16 bits in flip-flops.
 * One write port (host) and one read port per engine. There is no host read-back port: each read port
 * is a 16-bit 64:1 mux fed by all 1024 bits, the densest wiring in the design.
 * The engine ports are write-first: an engine that fetches the address being written this cycle
 * gets the new word, so an engine's instruction register always equals memory[PC].
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

  reg [15:0] mem[0:63];
  integer i;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (i = 0; i < 64; i = i + 1) mem[i] <= 16'd0;
    end else if (we) begin
      mem[waddr] <= wdata;
    end
  end

  genvar g;
  generate
    for (g = 0; g < NUM_SM; g = g + 1) begin : g_port
      wire [5:0] ra = sm_raddr[g*6+:6];
      assign sm_rdata[g*16+:16] = (we && waddr == ra) ? wdata : mem[ra];
    end
  endgenerate


endmodule
