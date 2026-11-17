/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver engine clock divider: 16.8 fixed point.
 * `tick` is high for one clock cycle per engine step. While the engine is disabled the divider is
 * held at its start state, so the first tick comes on the first enabled cycle and engines enabled
 * together stay in phase.
 */

`default_nettype none

module bw_clkdiv (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        en,
    input  wire        restart,
    input  wire [15:0] div_int,
    input  wire [7:0]  div_frac,
    output wire        tick
);

  reg [15:0] cnt;
  reg [7:0]  acc;

  wire [8:0] sum = {1'b0, acc} + {1'b0, div_frac};

  assign tick = en & (cnt == 16'd0);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt <= 16'd0;
      acc <= 8'd0;
    end else if (!en || restart) begin
      cnt <= 16'd0;
      acc <= 8'd0;
    end else if (cnt == 16'd0) begin
      acc <= sum[7:0];
      cnt <= div_int - 16'd1 + {15'd0, sum[8]};
    end else begin
      cnt <= cnt - 16'd1;
    end
  end

endmodule
