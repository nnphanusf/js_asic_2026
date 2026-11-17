/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver bit-level assist unit (BAU): a programmable filter on the one-bit shift path.
 *
 *   IN  src, BAU : line bit -> [NRZI decode] -> [drop stuff bits] -> [CRC] -> ISR
 *   OUT dst, BAU : OSR bit -> [CRC] -> [insert stuff bits] -> [NRZI | Manchester encode] -> dst
 *
 * Each stage is switched by a mode bit that the program sets with SET BAU:
 *   mode[0] CRC    update the CRC register with every data bit
 *   mode[1] STUFF  after STUFF_N equal bits (ones only, or either polarity) a stuff bit follows
 *   mode[2] NRZI   0 = transition, 1 = no transition
 *   mode[3] MAN    Manchester: each data bit takes two OUT instructions (~bit, then bit)
 *   SET BAU with bit 4 set also clears the run counter, line level, phase and error flag.
 *
 * The CRC is a right-shifting LFSR with a programmable reflected polynomial; its width is implied
 * by the polynomial. This block is purely the bit-level state; the engine decides when a bit is
 * committed.
 */

`default_nettype none

module bw_bau (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        restart,
    // configuration
    input  wire [15:0] poly,
    input  wire [2:0]  stuff_n,
    input  wire        stuff_any,
    // datapath inputs
    input  wire        in_raw,      // line bit sampled by IN
    input  wire        out_bit,     // next OSR bit for OUT
    // commits from the engine (at most one per cycle)
    input  wire        in_commit,
    input  wire        out_commit,
    input  wire        set_mode,
    input  wire [4:0]  set_data,
    input  wire        crc_wr,
    input  wire [15:0] crc_wdata,
    // results
    output wire        stuff_due,   // the next bit is a stuff bit
    output wire        in_bit,      // decoded bit for the ISR
    output wire        out_enc,     // encoded level for the pins
    output wire        out_consume, // this OUT finishes a bit (always, except Manchester first half)
    output reg  [15:0] crc,
    output reg         berr
);

  reg [3:0] mode;
  reg [2:0] run_len;
  reg       run_val;
  reg       level;
  reg       phase;

  wire m_crc = mode[0], m_stuff = mode[1], m_nrzi = mode[2], m_man = mode[3];

  assign stuff_due = m_stuff & (stuff_n != 3'd0) & (run_len == stuff_n) & (stuff_any | run_val);

  assign in_bit = m_nrzi ? ~(in_raw ^ level) : in_raw;

  wire out_sym = stuff_due ? ~run_val : out_bit;
  assign out_enc = m_man ? (phase ? out_sym : ~out_sym) : m_nrzi ? (out_sym ? level : ~level) : out_sym;
  assign out_consume = ~m_man | phase;

  // One data bit through the CRC and the run-length counter.
  wire        trk     = (in_commit & ~stuff_due) | (out_commit & out_consume & ~stuff_due);
  wire        trk_bit = in_commit ? in_bit : out_sym;
  wire        is_stuf = (in_commit & stuff_due) | (out_commit & out_consume & stuff_due);
  wire [15:0] crc_nxt = {1'b0, crc[15:1]} ^ ((crc[0] ^ trk_bit) ? poly : 16'd0);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      crc     <= 16'd0;
      mode    <= 4'd0;
      run_len <= 3'd0;
      run_val <= 1'b0;
      level   <= 1'b1;
      phase   <= 1'b0;
      berr    <= 1'b0;
    end else if (restart) begin
      crc     <= 16'd0;
      mode    <= 4'd0;
      run_len <= 3'd0;
      run_val <= 1'b0;
      level   <= 1'b1;
      phase   <= 1'b0;
      berr    <= 1'b0;
    end else begin
      if (in_commit) level <= in_raw;
      if (out_commit) begin
        level <= out_enc;
        if (m_man) phase <= ~phase;
      end
      if (is_stuf) begin
        if (in_commit && in_bit == run_val) berr <= 1'b1;
        run_val <= trk_bit;
        run_len <= 3'd1;
      end
      if (trk) begin
        if (m_crc) crc <= crc_nxt;
        if (m_stuff) begin
          if (trk_bit == run_val) begin
            if (run_len != 3'd7) run_len <= run_len + 3'd1;
          end else begin
            run_val <= trk_bit;
            run_len <= 3'd1;
          end
        end
      end
      if (crc_wr) crc <= crc_wdata;
      if (set_mode) begin
        mode <= set_data[3:0];
        if (set_data[4]) begin
          run_len <= 3'd0;
          run_val <= 1'b0;
          level   <= 1'b1;
          phase   <= 1'b0;
          berr    <= 1'b0;
        end
      end
    end
  end

endmodule
