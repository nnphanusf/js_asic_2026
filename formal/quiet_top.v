/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Whole chip, seen only through its pins: from reset until the host first pulls CS_N low, no pad is
 * driven and every output is low. This covers the reset itself, the two-cycle reset synchronizer and
 * any length of idle time after it, for every value of the other inputs (ena, the protocol pins and
 * SCK/MOSI), with no assumption about the chip's internal state beyond the reset.
 */

`default_nettype none

module quiet_top (
    input wire       clk,
    input wire       rst_n,
    input wire       ena,
    input wire [7:0] ui_in,
    input wire [7:0] uio_in
);

  wire [7:0] uo_out, uio_out, uio_oe;

  tt_um_nnphanusf_bitweaver dut (
      .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in), .uio_out(uio_out), .uio_oe(uio_oe),
      .ena(ena), .clk(clk), .rst_n(rst_n)
  );

  // quiet: the host has not selected the chip since the last reset.
  reg quiet = 1'b1;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) quiet <= 1'b1;
    else if (!ui_in[7]) quiet <= 1'b0;
  end

  initial assume (!rst_n);

  always @(*) begin
    if (!rst_n || quiet) begin
      assert (uio_oe == 8'd0);
      assert (uio_out == 8'd0);
      assert (uo_out == 8'd0);
    end
  end

  // Reachability: quiet cycles well past the two-cycle reset synchronizer (the property is not
  // vacuous), and the host's first CS_N low ends the quiet period.
  reg [2:0] since = 3'd0;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) since <= 3'd0;
    else if (since != 3'd7) since <= since + 3'd1;
  end
  always @(posedge clk) begin
    cover (rst_n && quiet && since == 3'd4);
    cover (rst_n && !quiet && since == 3'd5);
  end

endmodule
