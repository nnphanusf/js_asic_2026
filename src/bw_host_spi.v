/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver host port: SPI mode 0 target, MSB first, sampled with the system clock.
 *
 * Frame (CS_N low throughout):  [cmd] [addr] [data word] [data word] ...
 *   cmd[7] = 1 write, 0 read;  cmd[6] = 1 increment the address after every word.
 *   Data words are 16 bits, high byte first.
 * MISO returns the ID byte during cmd, a FIFO status byte during addr, then read data.
 *
 * A read word is loaded into the shift register on the SCK falling edge before its first bit
 * (rd_load) and its side effect (rd_pop, e.g. a FIFO pop) fires on the first SCK rising edge of the
 * word, so a word the host never clocks is never consumed.
 *
 * Inputs must already be synchronized to clk. MISO changes two to three cycles after the
 * synchronized SCK falls, so the host's SCK low time must exceed three cycles plus every cycle of pad,
 * multiplexer and phase delay (SpiDriver.miso_delay in sw/bitweaver/loader.py models it). Use
 * SCK <= clk/16 on real hardware; clk/8 only works with ideal pads.
 */

`default_nettype none
`include "bw_defs.vh"

module bw_host_spi (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        cs_n,
    input  wire        sck,
    input  wire        mosi,
    output wire        miso,
    input  wire [7:0]  status,    // shifted out during the address byte
    output wire [7:0]  rd_addr,   // address being read (valid in the data phase)
    input  wire [15:0] rd_data,   // combinational read data for rd_addr
    output wire        rd_load,   // rd_data is being latched this cycle
    output reg         rd_pop,    // the host started clocking out the latched word
    output reg         wr_en,     // one-cycle write strobe
    output reg  [7:0]  wr_addr,
    output reg  [15:0] wr_data
);

  localparam [1:0] PH_CMD = 2'd0, PH_ADDR = 2'd1, PH_DATA = 2'd2;

  reg        sck_q;
  reg [3:0]  bitcnt;
  reg [1:0]  phase;
  reg        cmd_w, cmd_inc;
  reg [7:0]  addr;
  reg [14:0] sr_in;
  reg [15:0] sr_out;
  reg        ld;

  wire        rise = sck & ~sck_q;
  wire        fall = ~sck & sck_q;
  wire [15:0] word = {sr_in[14:0], mosi};

  assign rd_addr = addr;
  assign rd_load = ~cs_n & fall & ld & (phase == PH_DATA);
  assign miso    = sr_out[15] & ~cs_n;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sck_q   <= 1'b0;
      bitcnt  <= 4'd0;
      phase   <= PH_CMD;
      cmd_w   <= 1'b0;
      cmd_inc <= 1'b0;
      addr    <= 8'd0;
      sr_in   <= 15'd0;
      sr_out  <= {`BW_SPI_ID_BYTE, 8'h00};
      ld      <= 1'b0;
      rd_pop  <= 1'b0;
      wr_en   <= 1'b0;
      wr_addr <= 8'd0;
      wr_data <= 16'd0;
    end else begin
      sck_q  <= sck;
      wr_en  <= 1'b0;
      rd_pop <= 1'b0;
      if (cs_n) begin
        bitcnt  <= 4'd0;
        phase   <= PH_CMD;
        cmd_w   <= 1'b0;
        cmd_inc <= 1'b0;
        ld      <= 1'b0;
        sr_out  <= {`BW_SPI_ID_BYTE, 8'h00};
      end else if (rise) begin
        sr_in <= word[14:0];
        case (phase)
          PH_CMD: begin
            if (bitcnt == 4'd7) begin
              cmd_w   <= word[7];
              cmd_inc <= word[6];
              phase   <= PH_ADDR;
              bitcnt  <= 4'd0;
              ld      <= 1'b1;
            end else begin
              bitcnt <= bitcnt + 4'd1;
            end
          end
          PH_ADDR: begin
            if (bitcnt == 4'd7) begin
              addr   <= word[7:0];
              phase  <= PH_DATA;
              bitcnt <= 4'd0;
              ld     <= 1'b1;
            end else begin
              bitcnt <= bitcnt + 4'd1;
            end
          end
          default: begin
            if (bitcnt == 4'd0 && !cmd_w) rd_pop <= 1'b1;
            if (bitcnt == 4'd15) begin
              if (cmd_w) begin
                wr_en   <= 1'b1;
                wr_addr <= addr;
                wr_data <= word;
              end
              addr <= addr + {7'd0, cmd_inc};
              ld   <= 1'b1;
            end
            bitcnt <= bitcnt + 4'd1;
          end
        endcase
      end else if (fall) begin
        if (ld) begin
          ld     <= 1'b0;
          sr_out <= (phase == PH_ADDR) ? {status, 8'h00} : rd_data;
        end else begin
          sr_out <= {sr_out[14:0], 1'b0};
        end
      end
    end
  end

`ifdef FORMAL
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  initial assume (!rst_n);

  always @(posedge clk) begin
    if (f_past && rst_n && $past(rst_n)) begin
      // The phase register only ever holds a legal state.
      assert (phase != 2'd3);
      // The byte phases never count past eight bits.
      if (phase != PH_DATA) assert (bitcnt <= 4'd7);
      // Strobes are single-cycle pulses that only follow an SCK rising edge with CS_N low.
      if (wr_en) assert ($past(rise) && !$past(cs_n) && $past(phase) == PH_DATA && $past(cmd_w));
      if (rd_pop) assert ($past(rise) && !$past(cs_n) && $past(phase) == PH_DATA && !$past(cmd_w));
      if ($past(wr_en) && $past(rst_n)) assert (!wr_en);
      // No deadlock: one cycle with CS_N high always returns the port to its idle state.
      if ($past(cs_n) && $past(rst_n)) begin
        assert (phase == PH_CMD && bitcnt == 4'd0 && !ld && !wr_en && !rd_pop);
        assert (sr_out == {`BW_SPI_ID_BYTE, 8'h00});
      end
      // MISO is quiet while deselected.
      if (cs_n) assert (!miso);
    end
  end

  // Reachability: a complete write and a complete read.
  always @(posedge clk) begin
    cover (f_past && rst_n && wr_en);
    cover (f_past && rst_n && rd_pop);
    cover (f_past && rst_n && rd_load && addr == 8'h2B);
  end
`endif

endmodule
