`default_nettype none
`timescale 1ns / 1ps

/* Testbench wrapper for the cocotb tests.
 *
 * It models the pads around the design so tests can attach independent protocol models:
 *   - host_cs_n / host_sck / host_mosi drive the host port (ui_in[7:5]),
 *   - in0..in4 drive PIN0..PIN4 (ui_in[4:0]),
 *   - ext11..ext18 are what the outside world puts on the bidirectional pads PIN11..PIN18
 *     (1 = released / pulled up, 0 = pulled low). A pad the design drives takes the design's level.
 *   - pad0..pad18 show the level on every protocol pin.
 * ui_q / uio_q / rst_q / ena_q are the inputs sampled at each rising clock edge; the lock-step
 * checker feeds exactly these values to the reference model.
 */
module tb ();

  initial begin
`ifndef NO_DUMP
    $dumpfile("tb.fst");
    $dumpvars(0, tb);
`endif
    #1;
  end

  reg clk = 1'b0;
  reg rst_n = 1'b0;
  reg ena = 1'b1;
  reg host_cs_n = 1'b1, host_sck = 1'b0, host_mosi = 1'b0;
  reg in0 = 1'b1, in1 = 1'b1, in2 = 1'b1, in3 = 1'b1, in4 = 1'b1;
  reg ext11 = 1'b1, ext12 = 1'b1, ext13 = 1'b1, ext14 = 1'b1;
  reg ext15 = 1'b1, ext16 = 1'b1, ext17 = 1'b1, ext18 = 1'b1;

  wire [7:0] ui_in = {host_cs_n, host_sck, host_mosi, in4, in3, in2, in1, in0};
  wire [7:0] uio_ext = {ext18, ext17, ext16, ext15, ext14, ext13, ext12, ext11};
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;
  wire [7:0] uio_in = (uio_oe & uio_out) | (~uio_oe & uio_ext);

  wire [18:0] pads = {uio_in, uo_out[5:0], ui_in[4:0]};
  wire pad0 = pads[0], pad1 = pads[1], pad2 = pads[2], pad3 = pads[3], pad4 = pads[4];
  wire pad5 = pads[5], pad6 = pads[6], pad7 = pads[7], pad8 = pads[8], pad9 = pads[9];
  wire pad10 = pads[10], pad11 = pads[11], pad12 = pads[12], pad13 = pads[13], pad14 = pads[14];
  wire pad15 = pads[15], pad16 = pads[16], pad17 = pads[17], pad18 = pads[18];
  wire host_miso = uo_out[7];
  wire host_irq = uo_out[6];

  reg [7:0] ui_q, uio_q;
  reg rst_q, ena_q;
  always @(posedge clk) begin
    ui_q  <= ui_in;
    uio_q <= uio_in;
    rst_q <= rst_n;
    ena_q <= ena;
  end

  tt_um_nnphanusf_bitweaver user_project (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

endmodule
