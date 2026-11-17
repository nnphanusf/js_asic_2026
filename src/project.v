/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver: a programmable protocol emulator.
 *
 * Phase 0 skeleton. This top level proves the Tiny Tapeout flow
 * (RTL -> GDS -> precheck) at 8x4 tiles and 60 MHz. It holds every protocol
 * pin in a safe idle state (outputs low, bidirectional pins Hi-Z) and contains
 * one small piece of real logic, the "probe": a read-only SPI target on the
 * host pins. The probe gives the flow a clocked design whose logic depends on
 * every input pin, so no pad is left disconnected, and it gives the demo board
 * a first bring-up test (read the ID, read back the protocol pins).
 * The engine cluster, full host port and pin mux replace it in Phase 1.
 *
 * Pin plan (fixed; see docs/info.md):
 *   ui_in[7]  HOST_CS_N    ui_in[6] HOST_SCK    ui_in[5] HOST_MOSI
 *   uo_out[7] HOST_MISO    uo_out[6] HOST_IRQ
 *   ui_in[4:0], uo_out[5:0], uio[7:0]  -> 19 protocol pins PIN0..PIN18
 *
 * Probe protocol (SPI mode 0, MSB first, SCK at most clk/8):
 *   While CS_N is low, every byte shifted out on MISO is selected by the
 *   previous byte shifted in on MOSI. The first byte after CS_N falls is the ID.
 *     cmd[1:0] = 0 : ID (8'hB7)
 *     cmd[1:0] = 1 : {3'b000, PIN4..PIN0}   (ui_in[4:0])
 *     cmd[1:0] = 2 : PIN18..PIN11           (uio_in[7:0])
 *     cmd[1:0] = 3 : free-running 8-bit cycle counter (counts while ena)
 *   MISO is driven low while CS_N is high.
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

  localparam [7:0] PROBE_ID = 8'hB7;

  // 2-FF synchronizers on every input (the real design keeps these).
  reg [15:0] sync1, sync2;
  always @(posedge clk) begin
    if (!rst_n) begin
      sync1 <= 16'h0080;  // CS_N idles high
      sync2 <= 16'h0080;
    end else begin
      sync1 <= {uio_in, ui_in};
      sync2 <= sync1;
    end
  end

  wire [4:0] pins_ui  = sync2[4:0];
  wire       mosi     = sync2[5];
  wire       sck      = sync2[6];
  wire       cs_n     = sync2[7];
  wire [7:0] pins_uio = sync2[15:8];

  // Free-running cycle counter.
  reg [7:0] cycles;
  always @(posedge clk) begin
    if (!rst_n) cycles <= 8'd0;
    else if (ena) cycles <= cycles + 8'd1;
  end

  // SPI mode 0 target: sample MOSI on SCK rise, update MISO on SCK fall.
  reg       sck_q;
  reg [2:0] bit_cnt;
  reg       prev_mosi;  // MOSI bit from the previous SCK rise (cmd bit 1 at a byte end)
  reg [7:0] tx;
  reg       loaded;  // tx was just reloaded at a byte boundary; skip the next shift

  wire       sck_rise = sck & ~sck_q;
  wire       sck_fall = ~sck & sck_q;
  wire [1:0] cmd      = {prev_mosi, mosi};  // only the low two command bits are decoded

  reg [7:0] reply;
  always @(*) begin
    case (cmd)
      2'd0:    reply = PROBE_ID;
      2'd1:    reply = {3'b000, pins_ui};
      2'd2:    reply = pins_uio;
      default: reply = cycles;
    endcase
  end

  always @(posedge clk) begin
    if (!rst_n || cs_n) begin
      sck_q   <= 1'b0;
      bit_cnt <= 3'd0;
      prev_mosi <= 1'b0;
      tx      <= PROBE_ID;
      loaded  <= 1'b0;
    end else begin
      sck_q <= sck;
      if (sck_rise) begin
        prev_mosi <= mosi;
        bit_cnt <= bit_cnt + 3'd1;
        if (bit_cnt == 3'd7) begin
          tx     <= reply;
          loaded <= 1'b1;
        end
      end else if (sck_fall) begin
        if (loaded) loaded <= 1'b0;
        else tx <= {tx[6:0], 1'b0};
      end
    end
  end

  wire miso = ~cs_n & tx[7];

  // Protocol pins stay idle until the engines arrive.
  assign uo_out  = {miso, 7'b0000000};  // uo_out[6] HOST_IRQ and PIN5..PIN10 low
  assign uio_out = 8'h00;
  assign uio_oe  = 8'h00;               // all bidirectional pins are inputs (Hi-Z)

endmodule
