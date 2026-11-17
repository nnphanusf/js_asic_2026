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
  // The join configuration only changes together with a clear (bw_sm clears both FIFOs on every
  // write to FIFOCTRL).
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(posedge clk) begin
    if (f_past && !$past(clr)) begin
      assume (join_tx == $past(join_tx));
      assume (join_rx == $past(join_rx));
    end
  end
  initial assume (!rst_n);

  // Basic invariants: counts within the configured depth and consistent with the pointers.
  always @(posedge clk) begin
    if (f_past && rst_n) begin
      assert (tx_cnt <= tx_depth);
      assert (rx_cnt <= rx_depth);
      assert (tx_cnt[2:0] == tx_wp - tx_rp);
      assert (rx_cnt[2:0] == rx_wp - rx_rp);
    end
  end

  // Order preservation, by tracking two arbitrary values a and b pushed back to back into one FIFO.
  // State 0: idle. 1: a is inside at `pos` places from the head. 2: b was the very next push.
  // 3: a has left, so b must be at the head.
  (* anyconst *) reg [15:0] f_a;
  (* anyconst *) reg [15:0] f_b;
  (* anyconst *) reg f_watch_rx;  // which FIFO of the pair to watch
  (* anyseq *) reg f_mark;
  reg [1:0] f_st;
  reg [3:0] f_pos;
  wire        w_join = f_watch_rx ? jrx : jtx;
  wire        w_push = f_watch_rx ? do_rx_push : do_tx_push;
  wire        w_pop = f_watch_rx ? do_rx_pop : do_tx_pop;
  wire [15:0] w_wdata = f_watch_rx ? rx_wdata : tx_wdata;
  wire [15:0] w_head = f_watch_rx ? rx_rdata : tx_rdata;
  wire [3:0]  w_cnt = f_watch_rx ? rx_cnt : tx_cnt;
  wire        w_empty = f_watch_rx ? rx_empty : tx_empty;
  wire [2:0]  w_rp = f_watch_rx ? rx_rp : tx_rp;
  wire [2:0]  w_base = f_watch_rx ? 3'b100 : 3'b000;  // physical half used when not joined
  wire [2:0]  w_a_lin = w_rp + f_pos[2:0];
  wire [2:0]  w_b_lin = w_rp + f_pos[2:0] + 3'd1;
  wire [2:0]  w_a_idx = w_join ? w_a_lin : (w_base | {1'b0, w_a_lin[1:0]});
  wire [2:0]  w_b_idx = w_join ? w_b_lin : (w_base | {1'b0, w_b_lin[1:0]});

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      f_st  <= 2'd0;
      f_pos <= 4'd0;
    end else if (clr) begin
      f_st <= 2'd0;
    end else begin
      case (f_st)
        2'd0: if (w_push && f_mark && w_wdata == f_a) begin
          f_st  <= 2'd1;
          f_pos <= w_cnt - {3'd0, w_pop};
        end
        2'd1: begin
          if (w_pop && f_pos == 4'd0) f_st <= 2'd0;
          else begin
            f_pos <= f_pos - {3'd0, w_pop};
            if (w_push) f_st <= (w_wdata == f_b) ? 2'd2 : 2'd0;
          end
        end
        2'd2: begin
          if (w_pop && f_pos == 4'd0) f_st <= 2'd3;
          else f_pos <= f_pos - {3'd0, w_pop};
        end
        default: if (w_pop) f_st <= 2'd0;
      endcase
    end
  end

  always @(posedge clk) begin
    if (f_past && rst_n) begin
      if (f_st == 2'd1 || f_st == 2'd2) begin
        assert (f_pos < w_cnt);
        assert (mem[w_a_idx] == f_a);
      end
      if (f_st == 2'd2) begin
        assert (f_pos + 4'd1 < w_cnt);
        assert (mem[w_b_idx] == f_b);
      end
      if (f_st == 2'd3) begin
        assert (!w_empty);
        assert (w_head == f_b);
      end
    end
  end

  always @(posedge clk) begin
    cover (rst_n && tx_full && jtx);
    cover (rst_n && rx_full && jrx);
    cover (rst_n && tx_full && rx_full && !jtx && !jrx);
    cover (rst_n && f_st == 2'd3 && !f_watch_rx && jtx);
    cover (rst_n && f_st == 2'd3 && f_watch_rx && !jrx);
  end
`endif

endmodule
