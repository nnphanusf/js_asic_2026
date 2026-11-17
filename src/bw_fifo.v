/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver FIFO pair: one engine's TX and RX FIFOs in a shared 8 x 16-bit store.
 * Normally each FIFO is 4 deep (TX in entries 0..3, RX in 4..7). join_tx gives all eight entries to
 * TX and disables RX; join_rx does the opposite. The configuration may only change together with
 * `clr`. Pushes to a full FIFO and pops from an empty one are ignored.
 */

`default_nettype none

module bw_fifo (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        clr,
    input  wire        join_tx,
    input  wire        join_rx,
    input  wire        tx_push,
    input  wire [15:0] tx_wdata,
    input  wire        tx_pop,
    output wire [15:0] tx_rdata,
    output wire        tx_full,
    output wire        tx_empty,
    output wire [3:0]  tx_level,
    input  wire        rx_push,
    input  wire [15:0] rx_wdata,
    input  wire        rx_pop,
    output wire [15:0] rx_rdata,
    output wire        rx_full,
    output wire        rx_empty,
    output wire [3:0]  rx_level
);

  reg [15:0] mem[0:7];
  reg [2:0] tx_wp, tx_rp, rx_wp, rx_rp;
  reg [3:0] tx_cnt, rx_cnt;

  wire jtx = join_tx;
  wire jrx = join_rx & ~join_tx;
  wire [3:0] tx_depth = jtx ? 4'd8 : (jrx ? 4'd0 : 4'd4);
  wire [3:0] rx_depth = jtx ? 4'd0 : (jrx ? 4'd8 : 4'd4);

  assign tx_level = tx_cnt;
  assign rx_level = rx_cnt;
  assign tx_full  = (tx_cnt == tx_depth);
  assign rx_full  = (rx_cnt == rx_depth);
  assign tx_empty = (tx_cnt == 4'd0);
  assign rx_empty = (rx_cnt == 4'd0);

  wire do_tx_push = tx_push & ~tx_full;
  wire do_tx_pop  = tx_pop & ~tx_empty;
  wire do_rx_push = rx_push & ~rx_full;
  wire do_rx_pop  = rx_pop & ~rx_empty;

  wire [2:0] tx_wi = {jtx & tx_wp[2], tx_wp[1:0]};
  wire [2:0] tx_ri = {jtx & tx_rp[2], tx_rp[1:0]};
  wire [2:0] rx_wi = {~jrx | rx_wp[2], rx_wp[1:0]};
  wire [2:0] rx_ri = {~jrx | rx_rp[2], rx_rp[1:0]};

  assign tx_rdata = mem[tx_ri];
  assign rx_rdata = mem[rx_ri];

  integer i;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (i = 0; i < 8; i = i + 1) mem[i] <= 16'd0;
      tx_wp  <= 3'd0;
      tx_rp  <= 3'd0;
      rx_wp  <= 3'd0;
      rx_rp  <= 3'd0;
      tx_cnt <= 4'd0;
      rx_cnt <= 4'd0;
    end else begin
      if (do_tx_push) mem[tx_wi] <= tx_wdata;
      if (do_rx_push) mem[rx_wi] <= rx_wdata;
      if (clr) begin
        tx_wp  <= 3'd0;
        tx_rp  <= 3'd0;
        rx_wp  <= 3'd0;
        rx_rp  <= 3'd0;
        tx_cnt <= 4'd0;
        rx_cnt <= 4'd0;
      end else begin
        if (do_tx_push) tx_wp <= tx_wp + 3'd1;
        if (do_tx_pop) tx_rp <= tx_rp + 3'd1;
        if (do_rx_push) rx_wp <= rx_wp + 3'd1;
        if (do_rx_pop) rx_rp <= rx_rp + 3'd1;
        tx_cnt <= tx_cnt + {3'd0, do_tx_push} - {3'd0, do_tx_pop};
        rx_cnt <= rx_cnt + {3'd0, do_rx_push} - {3'd0, do_rx_pop};
      end
    end
  end

`ifdef FORMAL
  // The join configuration is static between clears (the engine guarantees this by clearing the
  // FIFOs on every write to FIFOCTRL).
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(posedge clk) begin
    if (f_past && !$past(clr)) begin
      assume (join_tx == $past(join_tx));
      assume (join_rx == $past(join_rx));
    end
  end
  initial assume (!rst_n);

  // Reference model: track two arbitrary words pushed into each FIFO and check that they come out
  // in order, exactly once (the classic two-token ordering proof).
  (* anyconst *) reg [15:0] f_a;
  (* anyconst *) reg [15:0] f_b;
  reg [1:0] f_tx_state, f_rx_state;  // 0 idle, 1 a is in, 2 a and b are in, 3 a is out
  reg [3:0] f_tx_a_pos, f_rx_a_pos;  // number of pops until `a` is at the head
  (* anyseq *) reg f_tx_mark, f_rx_mark;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      f_tx_state <= 2'd0;
      f_rx_state <= 2'd0;
      f_tx_a_pos <= 4'd0;
      f_rx_a_pos <= 4'd0;
    end else if (clr) begin
      f_tx_state <= 2'd0;
      f_rx_state <= 2'd0;
    end else begin
      case (f_tx_state)
        2'd0: if (do_tx_push && f_tx_mark && tx_wdata == f_a) begin
          f_tx_state <= 2'd1;
          f_tx_a_pos <= tx_cnt - {3'd0, do_tx_pop};
        end
        2'd1: begin
          if (do_tx_pop) f_tx_a_pos <= f_tx_a_pos - 4'd1;
          if (do_tx_push && tx_wdata == f_b) f_tx_state <= 2'd2;
          else if (do_tx_push) f_tx_state <= 2'd0;  // something else followed: stop tracking
          else if (do_tx_pop && f_tx_a_pos == 4'd0) f_tx_state <= 2'd0;
        end
        2'd2: begin
          if (do_tx_pop) begin
            f_tx_a_pos <= f_tx_a_pos - 4'd1;
            if (f_tx_a_pos == 4'd0) f_tx_state <= 2'd3;
          end
        end
        default: if (do_tx_pop) f_tx_state <= 2'd0;
      endcase
      case (f_rx_state)
        2'd0: if (do_rx_push && f_rx_mark && rx_wdata == f_a) begin
          f_rx_state <= 2'd1;
          f_rx_a_pos <= rx_cnt - {3'd0, do_rx_pop};
        end
        2'd1: begin
          if (do_rx_pop) f_rx_a_pos <= f_rx_a_pos - 4'd1;
          if (do_rx_push && rx_wdata == f_b) f_rx_state <= 2'd2;
          else if (do_rx_push) f_rx_state <= 2'd0;
          else if (do_rx_pop && f_rx_a_pos == 4'd0) f_rx_state <= 2'd0;
        end
        2'd2: begin
          if (do_rx_pop) begin
            f_rx_a_pos <= f_rx_a_pos - 4'd1;
            if (f_rx_a_pos == 4'd0) f_rx_state <= 2'd3;
          end
        end
        default: if (do_rx_pop) f_rx_state <= 2'd0;
      endcase
    end
  end

  wire [2:0] f_tx_a_idx = {jtx & (tx_rp[2] ^ (f_tx_a_pos[2] ^ ((tx_rp[1:0] + f_tx_a_pos[1:0]) < tx_rp[1:0]))),
                           tx_rp[1:0] + f_tx_a_pos[1:0]};
  wire [2:0] f_rx_a_idx = {~jrx | (rx_rp[2] ^ (f_rx_a_pos[2] ^ ((rx_rp[1:0] + f_rx_a_pos[1:0]) < rx_rp[1:0]))),
                           rx_rp[1:0] + f_rx_a_pos[1:0]};

  always @(posedge clk) begin
    if (rst_n) begin
      // No overflow, no underflow, consistent pointers.
      assert (tx_cnt <= tx_depth || tx_cnt <= 4'd8);
      assert (tx_cnt <= 4'd8 && rx_cnt <= 4'd8);
      if (!jtx) assert (tx_cnt <= 4'd4 || jrx);
      assert (tx_cnt[2:0] == (tx_wp - tx_rp) || tx_cnt == 4'd8 || (!jtx && tx_cnt[1:0] == (tx_wp[1:0] - tx_rp[1:0])));
      assert (rx_cnt[2:0] == (rx_wp - rx_rp) || rx_cnt == 4'd8 || (!jrx && rx_cnt[1:0] == (rx_wp[1:0] - rx_rp[1:0])));
      // While `a` is tracked it is still stored where the model says.
      if (f_tx_state == 2'd1 || f_tx_state == 2'd2) begin
        assert (f_tx_a_pos < tx_cnt);
        assert (mem[f_tx_a_idx] == f_a);
      end
      if (f_rx_state == 2'd1 || f_rx_state == 2'd2) begin
        assert (f_rx_a_pos < rx_cnt);
        assert (mem[f_rx_a_idx] == f_a);
      end
      // Order: when `a` has just left, the next word out is `b`.
      if (f_tx_state == 2'd3) begin
        assert (!tx_empty);
        assert (tx_rdata == f_b);
      end
      if (f_rx_state == 2'd3) begin
        assert (!rx_empty);
        assert (rx_rdata == f_b);
      end
    end
  end

  always @(posedge clk) begin
    cover (rst_n && tx_full && jtx);
    cover (rst_n && rx_full && jrx);
    cover (rst_n && tx_full && rx_full && !jtx && !jrx);
    cover (rst_n && f_tx_state == 2'd3);
    cover (rst_n && f_rx_state == 2'd3);
  end
`endif

endmodule
