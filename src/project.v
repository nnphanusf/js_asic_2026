/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver: a programmable protocol emulator.
 *
 * Phase 0 skeleton. This top level only proves the Tiny Tapeout flow
 * (RTL -> GDS -> precheck) at 8x4 tiles and 60 MHz. Every pin is held in a
 * safe idle state: outputs low, bidirectional pins as inputs (Hi-Z).
 * The engine cluster, host SPI port and pin mux arrive in Phase 1.
 *
 * Pin plan (fixed; see docs/info.md):
 *   ui_in[7]  HOST_CS_N    ui_in[6] HOST_SCK    ui_in[5] HOST_MOSI
 *   uo_out[7] HOST_MISO    uo_out[6] HOST_IRQ
 *   ui_in[4:0], uo_out[5:0], uio[7:0]  -> 19 protocol pins PIN0..PIN18
 */

`default_nettype none

module tt_um_nnphanusf_bitweaver (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // Safe idle state until the real design lands.
  assign uo_out  = 8'h00;
  assign uio_out = 8'h00;
  assign uio_oe  = 8'h00;  // all bidirectional pins are inputs (Hi-Z)

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, clk, rst_n, ui_in, uio_in, 1'b0};

endmodule
