/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver engine clock divider: 16.8 fixed point.
 * `tick` is high for one clock cycle per engine step. While the engine is disabled the divider is
 * held at its start state, so the first tick comes on the first enabled cycle and engines enabled
 * together stay in phase.
 *
 * `zero` is a registered copy of (cnt == 0), updated with cnt in every branch, so `tick` comes
 * straight from a flop instead of from a 16-bit zero detect at the head of the engine's longest
 * path (tick -> execute -> pc_next -> instruction-memory read -> IR).
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
  reg        zero;  // == (cnt == 0)

  wire [8:0] sum = {1'b0, acc} + {1'b0, div_frac};

  assign tick = en & zero;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cnt  <= 16'd0;
      acc  <= 8'd0;
      zero <= 1'b1;
    end else if (!en || restart) begin
      cnt  <= 16'd0;
      acc  <= 8'd0;
      zero <= 1'b1;
    end else if (zero) begin
      acc  <= sum[7:0];
      cnt  <= div_int - 16'd1 + {15'd0, sum[8]};
      // div_int - 1 + carry wraps to 0 exactly when div_int + carry == 1 (mod 2^16).
      zero <= sum[8] ? (div_int == 16'd0) : (div_int == 16'd1);
    end else begin
      cnt  <= cnt - 16'd1;
      zero <= (cnt == 16'd1);
    end
  end

`ifdef FORMAL
  // The registered flag always matches the counter it stands for.
  initial assume (!rst_n);
  always @(*) if (rst_n) assert (zero == (cnt == 16'd0));
`endif

endmodule
