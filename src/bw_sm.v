/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver engine (state machine): one of four identical programmable bit-banging cores.
 *
 * An engine executes one 16-bit instruction per tick of its clock divider. It owns
 *   - its configuration registers (a 16-address window of the host register map),
 *   - PC and instruction register IR (IR always holds memory[PC] unless an instruction was injected),
 *   - scratch registers X and Y, input and output shift registers ISR and OSR with bit counters,
 *   - a TX/RX FIFO pair, a clock divider, a WAIT timeout counter and a bit-level assist unit.
 * It reads the shared pins, IRQ flags and timestamp, and returns pin, IRQ and FIFO effects that the
 * top level merges across engines.
 *
 * The behaviour is specified in docs/isa.md and modelled cycle for cycle by sw/bitweaver/iss.py;
 * this file is written in the same order as Bitweaver._exec() there.
 */

`default_nettype none
`include "bw_defs.vh"

module bw_sm (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [1:0]  sm_id,
    input  wire        en,
    input  wire        restart,
    // host register window
    input  wire        cfg_we,
    input  wire [3:0]  cfg_waddr,
    input  wire [15:0] cfg_wdata,
    input  wire [3:0]  cfg_raddr,
    output reg  [15:0] cfg_rdata,
    // instruction memory port (write-first read of pc_next)
    output wire [5:0]  pc_next,
    input  wire [15:0] imem_rdata,
    // shared inputs
    input  wire [18:0] pins_in,
    input  wire [7:0]  irq_flags,
    input  wire [15:0] ts,
    // pin effects: masks and values for the output-level and direction registers
    output wire [18:0] po_mask,
    output wire [18:0] po_val,
    output wire [18:0] pd_mask,
    output wire [18:0] pd_val,
    // IRQ flag effects
    output wire [7:0]  irq_set,
    output wire [7:0]  irq_clr,
    // FIFO status and chaining
    output wire        tx_full,
    output wire        tx_empty,
    output wire        rx_full,
    output wire        rx_empty,
    output wire [15:0] rx_head,
    output wire        host_push,   // the host is pushing to the TX FIFO this cycle
    input  wire        rx_pop,      // pop the RX FIFO (host read or chain)
    input  wire        chain_push,  // push chain_wdata to the TX FIFO
    input  wire [15:0] chain_wdata,
    output wire        chain_en,
    output wire [1:0]  chain_src,
    // sticky-flag events
    output wire        ev_txover,
    output wire        ev_txstall,
    output wire        ev_rxstall
);

  // ------------------------------------------------------------------ configuration registers
  reg [15:0] r_div_int;
  reg [7:0]  r_div_frac;
  reg [5:0]  r_wrap_bot, r_wrap_top, r_org;
  reg [4:0]  r_jmp_pin, r_in_base, r_out_base, r_set_base, r_side_base, r_out_count;
  reg [2:0]  r_set_count;
  reg [1:0]  r_side_count;
  reg        r_side_en;
  reg [3:0]  r_push_thresh, r_pull_thresh;
  reg        r_autopush, r_autopull, r_in_right, r_out_right, r_side_pindir;
  reg        r_join_tx, r_join_rx, r_chain_en;
  reg [1:0]  r_chain_src;
  reg [15:0] r_tmo, r_poly;
  reg [2:0]  r_stuff_n;
  reg        r_stuff_any;

  wire we_fifoctrl = cfg_we && cfg_waddr == `BW_O_FIFOCTRL;
  wire host_exec   = cfg_we && cfg_waddr == `BW_O_EXEC;
  assign host_push = cfg_we && cfg_waddr == `BW_O_FIFO;
  assign chain_en  = r_chain_en;
  assign chain_src = r_chain_src;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      r_div_int     <= `BW_R_CLKDIV_INT;
      r_div_frac    <= 8'd0;
      r_wrap_bot    <= 6'd0;
      r_wrap_top    <= 6'd63;
      r_org         <= 6'd0;
      r_jmp_pin     <= 5'd0;
      r_in_base     <= 5'd0;
      r_out_base    <= 5'd0;
      r_set_base    <= 5'd0;
      r_side_base   <= 5'd0;
      r_out_count   <= 5'd0;
      r_set_count   <= 3'd0;
      r_side_count  <= 2'd0;
      r_side_en     <= 1'b0;
      r_push_thresh <= 4'd0;
      r_pull_thresh <= 4'd0;
      r_autopush    <= 1'b0;
      r_autopull    <= 1'b0;
      r_in_right    <= 1'b1;
      r_out_right   <= 1'b1;
      r_side_pindir <= 1'b0;
      r_join_tx     <= 1'b0;
      r_join_rx     <= 1'b0;
      r_chain_en    <= 1'b0;
      r_chain_src   <= 2'd0;
      r_tmo         <= 16'd0;
      r_poly        <= 16'd0;
      r_stuff_n     <= 3'd0;
      r_stuff_any   <= 1'b0;
    end else if (cfg_we) begin
      case (cfg_waddr)
        `BW_O_CLKDIV_INT:  r_div_int <= cfg_wdata;
        `BW_O_CLKDIV_FRAC: r_div_frac <= cfg_wdata[7:0];
        `BW_O_WRAP: begin
          r_wrap_bot <= cfg_wdata[`BW_F_WRAP_BOTTOM];
          r_wrap_top <= cfg_wdata[`BW_F_WRAP_TOP];
        end
        `BW_O_ORG: begin
          r_org     <= cfg_wdata[`BW_F_ORG_ORG];
          r_jmp_pin <= cfg_wdata[`BW_F_ORG_JMP_PIN];
        end
        `BW_O_PINCTRL0: begin
          r_in_base  <= cfg_wdata[`BW_F_PINCTRL0_IN_BASE];
          r_out_base <= cfg_wdata[`BW_F_PINCTRL0_OUT_BASE];
          r_set_base <= cfg_wdata[`BW_F_PINCTRL0_SET_BASE];
        end
        `BW_O_PINCTRL1: begin
          r_side_base  <= cfg_wdata[`BW_F_PINCTRL1_SIDE_BASE];
          r_out_count  <= cfg_wdata[`BW_F_PINCTRL1_OUT_COUNT];
          r_set_count  <= cfg_wdata[`BW_F_PINCTRL1_SET_COUNT];
          r_side_count <= cfg_wdata[`BW_F_PINCTRL1_SIDE_COUNT];
          r_side_en    <= cfg_wdata[`BW_F_PINCTRL1_SIDE_EN];
        end
        `BW_O_SHIFTCTRL: begin
          r_push_thresh <= cfg_wdata[`BW_F_SHIFTCTRL_PUSH_THRESH];
          r_pull_thresh <= cfg_wdata[`BW_F_SHIFTCTRL_PULL_THRESH];
          r_autopush    <= cfg_wdata[`BW_F_SHIFTCTRL_AUTOPUSH];
          r_autopull    <= cfg_wdata[`BW_F_SHIFTCTRL_AUTOPULL];
          r_in_right    <= cfg_wdata[`BW_F_SHIFTCTRL_IN_SHIFTDIR];
          r_out_right   <= cfg_wdata[`BW_F_SHIFTCTRL_OUT_SHIFTDIR];
          r_side_pindir <= cfg_wdata[`BW_F_SHIFTCTRL_SIDE_PINDIR];
        end
        `BW_O_FIFOCTRL: begin
          r_join_tx   <= cfg_wdata[`BW_F_FIFOCTRL_JOIN_TX];
          r_join_rx   <= cfg_wdata[`BW_F_FIFOCTRL_JOIN_RX];
          r_chain_en  <= cfg_wdata[`BW_F_FIFOCTRL_CHAIN_EN];
          r_chain_src <= cfg_wdata[`BW_F_FIFOCTRL_CHAIN_SRC];
        end
        `BW_O_TMO:      r_tmo <= cfg_wdata;
        `BW_O_BAU_POLY: r_poly <= cfg_wdata;
        `BW_O_BAU_CFG: begin
          r_stuff_n   <= cfg_wdata[`BW_F_BAU_CFG_STUFF_N];
          r_stuff_any <= cfg_wdata[`BW_F_BAU_CFG_STUFF_ANY];
        end
        default: ;
      endcase
    end
  end

  // ------------------------------------------------------------------ engine state
  reg [5:0]  pc;
  reg [15:0] ir, x, y, isr, osr;
  reg [4:0]  isr_cnt, osr_cnt, delay;
  reg        xq;       // IR holds an injected instruction; PC does not advance when it completes
  reg        xh;       // the injected instruction came from the host: run it without waiting for a tick
  reg        iw;       // IRQ WAIT has raised its flag and is waiting for it to clear
  reg        tw;       // a timed WAIT is in progress and tmo_cnt is valid
  reg [15:0] tmo_cnt;

  wire tick;
  bw_clkdiv u_div (
      .clk(clk), .rst_n(rst_n), .en(en), .restart(restart),
      .div_int(r_div_int), .div_frac(r_div_frac), .tick(tick)
  );

  wire        tx_pop;
  wire        rx_push;
  wire [15:0] rx_wdata;
  wire [15:0] tx_head;
  wire [3:0]  tx_level, rx_level;
  bw_fifo u_fifo (
      .clk(clk), .rst_n(rst_n), .clr(we_fifoctrl),
      .join_tx(r_join_tx), .join_rx(r_join_rx),
      .tx_push(host_push | chain_push), .tx_wdata(host_push ? cfg_wdata : chain_wdata),
      .tx_pop(tx_pop), .tx_rdata(tx_head), .tx_full(tx_full), .tx_empty(tx_empty), .tx_level(tx_level),
      .rx_push(rx_push), .rx_wdata(rx_wdata),
      .rx_pop(rx_pop), .rx_rdata(rx_head), .rx_full(rx_full), .rx_empty(rx_empty), .rx_level(rx_level)
  );
  assign ev_txover = host_push & tx_full;

  // ------------------------------------------------------------------ decode
  wire [2:0] op  = ir[15:13];
  wire [4:0] ds  = ir[12:8];
  wire [7:0] arg = ir[7:0];

  // Delay / side-set field: [enable if SIDE_EN][SIDE_COUNT side bits][delay bits].
  reg [4:0] dly_field;
  reg [2:0] side_val;
  reg       side_on;
  always @* begin
    side_on = 1'b0;
    side_val = 3'd0;
    dly_field = ds;
    case ({r_side_en, r_side_count})
      3'b000: ;
      3'b001: begin side_on = 1'b1; side_val = {2'b00, ds[4]}; dly_field = {1'b0, ds[3:0]}; end
      3'b010: begin side_on = 1'b1; side_val = {1'b0, ds[4:3]}; dly_field = {2'b00, ds[2:0]}; end
      3'b011: begin side_on = 1'b1; side_val = ds[4:2]; dly_field = {3'b000, ds[1:0]}; end
      3'b100: dly_field = {1'b0, ds[3:0]};
      3'b101: begin side_on = ds[4]; side_val = {2'b00, ds[3]}; dly_field = {2'b00, ds[2:0]}; end
      3'b110: begin side_on = ds[4]; side_val = {1'b0, ds[3:2]}; dly_field = {3'b000, ds[1:0]}; end
      default: begin side_on = ds[4]; side_val = ds[3:1]; dly_field = {4'b0000, ds[0]}; end
    endcase
  end

  wire [4:0] push_thresh = (r_push_thresh == 4'd0) ? 5'd16 : {1'b0, r_push_thresh};
  wire [4:0] pull_thresh = (r_pull_thresh == 4'd0) ? 5'd16 : {1'b0, r_pull_thresh};

  // ------------------------------------------------------------------ bit-level assist unit
  wire        stuff_due, bau_in_bit, bau_enc, bau_consume, berr;
  wire [15:0] crc;
  reg         bau_in_commit, bau_out_commit, bau_set, crc_wr;
  reg  [15:0] wr_val;  // value written by MOV (also the CRC write data)

  // ------------------------------------------------------------------ pins as this engine sees them
  wire [31:0] pins32 = {irq_flags, 1'b0, berr, stuff_due, rx_full, tx_empty, pins_in};
  wire        jmp_pin_val = pins32[r_jmp_pin];
  wire [4:0]  wait_idx = r_in_base + arg[4:0];
  wire        wait_pin_val = pins32[wait_idx];
  /* verilator lint_off UNUSEDSIGNAL */
  wire [63:0] pins_dbl = {pins32, pins32} >> r_in_base;
  /* verilator lint_on UNUSEDSIGNAL */
  wire [15:0] pins_rot = pins_dbl[15:0];

  // IRQ flag index, optionally relative to the engine number.
  wire [1:0] irq_rel = arg[1:0] + sm_id;
  wire [2:0] irq_idx = arg[4] ? {arg[2], irq_rel} : arg[2:0];
  wire       irq_val = irq_flags[irq_idx];
  wire [7:0] irq_bit = 8'd1 << irq_idx;

  // Source operand of IN and MOV.
  wire [2:0] src_sel = (op == `BW_OP_IN) ? arg[7:5] : arg[2:0];
  reg [15:0] src_val;
  always @* begin
    case (src_sel)
      `BW_SRC_PINS: src_val = pins_rot;
      `BW_SRC_X:    src_val = x;
      `BW_SRC_Y:    src_val = y;
      `BW_SRC_NULL: src_val = 16'd0;
      `BW_SRC_TIME: src_val = ts;
      `BW_SRC_CRC:  src_val = crc;
      `BW_SRC_ISR:  src_val = isr;
      default:      src_val = osr;
    endcase
  end

  bw_bau u_bau (
      .clk(clk), .rst_n(rst_n), .restart(restart),
      .poly(r_poly), .stuff_n(r_stuff_n), .stuff_any(r_stuff_any),
      .in_raw(src_val[0]), .out_bit(r_out_right ? osr[0] : osr[15]),
      .in_commit(bau_in_commit), .out_commit(bau_out_commit),
      .set_mode(bau_set), .set_data(arg[4:0]),
      .crc_wr(crc_wr), .crc_wdata(wr_val),
      .stuff_due(stuff_due), .in_bit(bau_in_bit), .out_enc(bau_enc), .out_consume(bau_consume),
      .crc(crc), .berr(berr)
  );

  // ------------------------------------------------------------------ shifters
  wire        bau = arg[4];
  wire [4:0]  cnt = bau ? 5'd1 : ((arg[3:0] == 4'd0) ? 5'd16 : {1'b0, arg[3:0]});
  wire [15:0] cnt_mask = (cnt[4]) ? 16'hFFFF : ~(16'hFFFF << cnt[3:0]);
  /* verilator lint_off UNUSEDSIGNAL */
  wire [4:0]  cnt_inv = 5'd16 - cnt;  // 0..15
  /* verilator lint_on UNUSEDSIGNAL */

  wire [5:0]  isr_sum = {1'b0, isr_cnt} + {1'b0, cnt};
  wire [4:0]  isr_cnt_new = (isr_sum > 6'd16) ? 5'd16 : isr_sum[4:0];
  wire [5:0]  osr_sum = {1'b0, osr_cnt} + {1'b0, cnt};
  wire [4:0]  osr_cnt_new = (osr_sum > 6'd16) ? 5'd16 : osr_sum[4:0];

  wire [15:0] in_data = (bau ? {15'd0, bau_in_bit} : src_val) & cnt_mask;
  wire [15:0] isr_right = cnt[4] ? in_data : ((isr >> cnt[3:0]) | (in_data << cnt_inv[3:0]));
  wire [15:0] isr_left = cnt[4] ? in_data : ((isr << cnt[3:0]) | in_data);
  wire [15:0] isr_shifted = r_in_right ? isr_right : isr_left;

  wire [15:0] out_right_d = osr & cnt_mask;
  wire [15:0] out_left_d = cnt[4] ? osr : ((osr >> cnt_inv[3:0]) & cnt_mask);
  wire [15:0] out_raw = r_out_right ? out_right_d : out_left_d;
  wire [15:0] out_data = bau ? {14'd0, ~bau_enc, bau_enc} : out_raw;
  wire [15:0] osr_shifted = cnt[4] ? 16'd0 : (r_out_right ? (osr >> cnt[3:0]) : (osr << cnt[3:0]));

  wire isr_full = (isr_cnt >= push_thresh);
  wire osr_empty = (osr_cnt >= pull_thresh);

  // MOV operation on the source value.
  reg [15:0] mov_val;
  integer bi;
  always @* begin
    case (arg[4:3])
      2'd1: mov_val = ~src_val;
      2'd2: for (bi = 0; bi < 16; bi = bi + 1) mov_val[bi] = src_val[15-bi];
      2'd3: mov_val = {src_val[7:0], src_val[15:8]};
      default: mov_val = src_val;
    endcase
  end

  // ------------------------------------------------------------------ execute
  wire run = ~restart & (xh | (tick & (delay == 5'd0)));

  reg        done;
  reg        jump_imm, jump_data, skip;
  reg [4:0]  jdata;
  reg        exec_load;
  reg [15:0] exec_data;
  reg [15:0] n_x, n_y, n_isr, n_osr, n_tmo_cnt;
  reg [4:0]  n_isr_cnt, n_osr_cnt;
  reg        n_iw, n_tw;
  reg        main_pins, main_dirs, main_set;
  reg [15:0] main_val;
  reg        e_irq_set, e_irq_clr;
  reg        e_tx_pop, e_rx_push;
  reg [15:0] e_rx_wdata;
  reg        e_txstall, e_rxstall;
  reg        cond;
  reg [15:0] remaining;

  always @* begin
    done = 1'b1;
    jump_imm = 1'b0;
    jump_data = 1'b0;
    skip = 1'b0;
    jdata = 5'd0;
    exec_load = 1'b0;
    exec_data = 16'd0;
    n_x = x;
    n_y = y;
    n_isr = isr;
    n_osr = osr;
    n_isr_cnt = isr_cnt;
    n_osr_cnt = osr_cnt;
    n_iw = iw;
    n_tw = tw;
    n_tmo_cnt = tmo_cnt;
    main_pins = 1'b0;
    main_dirs = 1'b0;
    main_set = 1'b0;
    main_val = 16'd0;
    e_irq_set = 1'b0;
    e_irq_clr = 1'b0;
    e_tx_pop = 1'b0;
    e_rx_push = 1'b0;
    e_rx_wdata = isr;
    e_txstall = 1'b0;
    e_rxstall = 1'b0;
    bau_in_commit = 1'b0;
    bau_out_commit = 1'b0;
    bau_set = 1'b0;
    crc_wr = 1'b0;
    wr_val = mov_val;
    cond = 1'b0;
    remaining = tw ? tmo_cnt : r_tmo;

    case (op)
      `BW_OP_JMP: begin
        case (arg[7:5])
          `BW_COND_ALWAYS: cond = 1'b1;
          `BW_COND_NOT_X:  cond = (x == 16'd0);
          `BW_COND_X_DEC: begin
            cond = (x != 16'd0);
            n_x = x - 16'd1;
          end
          `BW_COND_NOT_Y:  cond = (y == 16'd0);
          `BW_COND_Y_DEC: begin
            cond = (y != 16'd0);
            n_y = y - 16'd1;
          end
          `BW_COND_X_NE_Y: cond = (x != y);
          `BW_COND_PIN:    cond = jmp_pin_val;
          default:         cond = ~osr_empty;
        endcase
        jump_imm = cond;
      end

      `BW_OP_WAIT: begin
        // arg[7] polarity, arg[6] timed, arg[5] source (0 pin, 1 IRQ flag)
        cond = ((arg[5] ? irq_val : wait_pin_val) == arg[7]);
        if (cond) begin
          skip = arg[6];
          e_irq_clr = arg[5] & arg[7];
        end else if (arg[6] && remaining == 16'd0) begin
          done = 1'b1;  // timed out: fall through to the next instruction
        end else begin
          done = 1'b0;
          if (arg[6]) begin
            n_tmo_cnt = remaining - 16'd1;
            n_tw = 1'b1;
          end
        end
      end

      `BW_OP_IN: begin
        if (!(bau && stuff_due) && r_autopush && rx_full && isr_cnt_new >= push_thresh) begin
          done = 1'b0;
          e_rxstall = 1'b1;
        end else begin
          bau_in_commit = bau;
          if (!(bau && stuff_due)) begin
            if (r_autopush && isr_cnt_new >= push_thresh) begin
              e_rx_push = 1'b1;
              e_rx_wdata = isr_shifted;
              n_isr = 16'd0;
              n_isr_cnt = 5'd0;
            end else begin
              n_isr = isr_shifted;
              n_isr_cnt = isr_cnt_new;
            end
          end
        end
      end

      `BW_OP_OUT: begin
        if (!(bau && stuff_due) && r_autopull && osr_empty) begin
          // The OSR is exhausted: refill it now and run the OUT on the next tick.
          done = 1'b0;
          if (tx_empty) begin
            e_txstall = 1'b1;
          end else begin
            n_osr = tx_head;
            n_osr_cnt = 5'd0;
            e_tx_pop = 1'b1;
          end
        end else begin
          bau_out_commit = bau;
          if (!(bau && (stuff_due || !bau_consume))) begin
            if (r_autopull && osr_cnt_new >= pull_thresh && !tx_empty) begin
              n_osr = tx_head;
              n_osr_cnt = 5'd0;
              e_tx_pop = 1'b1;
            end else begin
              n_osr = osr_shifted;
              n_osr_cnt = osr_cnt_new;
            end
          end
          case (arg[7:5])
            `BW_OUT_PINS: begin
              main_pins = 1'b1;
              main_val = out_data;
            end
            `BW_OUT_X: n_x = out_data;
            `BW_OUT_Y: n_y = out_data;
            `BW_OUT_NULL: ;
            `BW_OUT_PINDIRS: begin
              main_dirs = 1'b1;
              main_val = out_data;
            end
            `BW_OUT_PC: begin
              jump_data = 1'b1;
              jdata = out_data[4:0];
            end
            `BW_OUT_ISR: begin
              n_isr = out_data;
              n_isr_cnt = cnt;
            end
            default: begin
              exec_load = 1'b1;
              exec_data = out_data;
            end
          endcase
        end
      end

      `BW_OP_PUSHPULL: begin
        // arg[7] 1 = PULL, arg[6] IFFULL / IFEMPTY, arg[5] block
        if (arg[7]) begin
          if (arg[6] && !osr_empty) begin
            // nothing to do
          end else if (tx_empty) begin
            e_txstall = 1'b1;
            if (arg[5]) begin
              done = 1'b0;
            end else begin
              n_osr = x;
              n_osr_cnt = 5'd0;
            end
          end else begin
            n_osr = tx_head;
            n_osr_cnt = 5'd0;
            e_tx_pop = 1'b1;
          end
        end else begin
          if (arg[6] && !isr_full) begin
            // nothing to do
          end else if (rx_full) begin
            e_rxstall = 1'b1;
            if (arg[5]) begin
              done = 1'b0;
            end else begin
              n_isr = 16'd0;
              n_isr_cnt = 5'd0;
            end
          end else begin
            e_rx_push = 1'b1;
            n_isr = 16'd0;
            n_isr_cnt = 5'd0;
          end
        end
      end

      `BW_OP_MOV: begin
        case (arg[7:5])
          `BW_MOV_PINS: begin
            main_pins = 1'b1;
            main_val = mov_val;
          end
          `BW_MOV_X: n_x = mov_val;
          `BW_MOV_Y: n_y = mov_val;
          `BW_MOV_CRC: crc_wr = 1'b1;
          `BW_MOV_EXEC: begin
            exec_load = 1'b1;
            exec_data = mov_val;
          end
          `BW_MOV_PC: begin
            jump_data = 1'b1;
            jdata = mov_val[4:0];
          end
          `BW_MOV_ISR: begin
            n_isr = mov_val;
            n_isr_cnt = 5'd0;
          end
          default: begin
            n_osr = mov_val;
            n_osr_cnt = 5'd0;
          end
        endcase
      end

      `BW_OP_IRQ: begin
        // arg[6] clear, arg[5] wait
        if (arg[6]) begin
          e_irq_clr = 1'b1;
        end else if (arg[5]) begin
          if (!iw) begin
            e_irq_set = 1'b1;
            n_iw = 1'b1;
            done = 1'b0;
          end else if (irq_val) begin
            done = 1'b0;
          end else begin
            n_iw = 1'b0;
          end
        end else begin
          e_irq_set = 1'b1;
        end
      end

      default: begin  // SET
        case (arg[7:5])
          `BW_SET_PINS: begin
            main_pins = 1'b1;
            main_set = 1'b1;
            main_val = {11'd0, arg[4:0]};
          end
          `BW_SET_X: n_x = {11'd0, arg[4:0]};
          `BW_SET_Y: n_y = {11'd0, arg[4:0]};
          `BW_SET_BAU: bau_set = 1'b1;
          `BW_SET_PINDIRS: begin
            main_dirs = 1'b1;
            main_set = 1'b1;
            main_val = {11'd0, arg[4:0]};
          end
          default: ;
        endcase
      end
    endcase

    // Nothing above takes effect unless the engine runs this cycle.
    if (!run) begin
      bau_in_commit = 1'b0;
      bau_out_commit = 1'b0;
      bau_set = 1'b0;
      crc_wr = 1'b0;
    end
  end

  assign tx_pop     = run & e_tx_pop;
  assign rx_push    = run & e_rx_push;
  assign rx_wdata   = e_rx_wdata;
  assign irq_set    = (run & e_irq_set) ? irq_bit : 8'd0;
  assign irq_clr    = (run & e_irq_clr) ? irq_bit : 8'd0;
  assign ev_txstall = run & e_txstall;
  assign ev_rxstall = run & e_rxstall;

  // ------------------------------------------------------------------ program counter
  wire [5:0] nxt1 = (pc == r_wrap_top) ? r_wrap_bot : pc + 6'd1;
  wire [5:0] nxt2 = (nxt1 == r_wrap_top) ? r_wrap_bot : nxt1 + 6'd1;
  wire [5:0] seq0 = xq ? pc : nxt1;
  wire [5:0] seq1 = xq ? nxt1 : nxt2;
  wire [5:0] jt_imm = r_org + {1'b0, arg[4:0]};
  wire [5:0] jt_data = r_org + {1'b0, jdata};
  wire [5:0] newpc = jump_imm ? jt_imm : jump_data ? jt_data : skip ? seq1 : seq0;

  assign pc_next = restart ? r_org : (run && done) ? newpc : pc;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc      <= 6'd0;
      ir      <= 16'd0;
      x       <= 16'd0;
      y       <= 16'd0;
      isr     <= 16'd0;
      osr     <= 16'd0;
      isr_cnt <= 5'd0;
      osr_cnt <= 5'd16;
      delay   <= 5'd0;
      xq      <= 1'b0;
      xh      <= 1'b0;
      iw      <= 1'b0;
      tw      <= 1'b0;
      tmo_cnt <= 16'd0;
    end else if (restart) begin
      pc      <= pc_next;
      ir      <= imem_rdata;
      x       <= 16'd0;
      y       <= 16'd0;
      isr     <= 16'd0;
      osr     <= 16'd0;
      isr_cnt <= 5'd0;
      osr_cnt <= 5'd16;
      delay   <= 5'd0;
      xq      <= 1'b0;
      xh      <= 1'b0;
      iw      <= 1'b0;
      tw      <= 1'b0;
      tmo_cnt <= 16'd0;
    end else begin
      pc <= pc_next;
      if (run) begin
        x       <= n_x;
        y       <= n_y;
        isr     <= n_isr;
        osr     <= n_osr;
        isr_cnt <= n_isr_cnt;
        osr_cnt <= n_osr_cnt;
        iw      <= n_iw;
        tw      <= done ? 1'b0 : n_tw;
        tmo_cnt <= n_tmo_cnt;
        if (done && !xh) delay <= dly_field;
      end else if (tick && delay != 5'd0) begin
        delay <= delay - 5'd1;
      end

      if (host_exec) begin
        ir <= cfg_wdata;
        xq <= 1'b1;
        xh <= 1'b1;
        iw <= 1'b0;
        tw <= 1'b0;
      end else if (run && done) begin
        if (exec_load) begin
          ir <= exec_data;
          xq <= 1'b1;
        end else begin
          ir <= imem_rdata;
          xq <= 1'b0;
          xh <= 1'b0;
        end
      end else if (!xq) begin
        ir <= imem_rdata;
      end
    end
  end

  // ------------------------------------------------------------------ pin effects
  // Main write (OUT / MOV / SET): a block of pins starting at a base, rotated into the pin space.
  wire [4:0]  m_base = main_set ? r_set_base : r_out_base;
  wire [4:0]  m_count = main_set ? {2'b00, r_set_count} : r_out_count;
  wire [15:0] m_mask16 = m_count[4] ? 16'hFFFF : ~(16'hFFFF << m_count[3:0]);
  /* verilator lint_off UNUSEDSIGNAL */
  wire [63:0] m_mask_dbl = {16'd0, m_mask16, 16'd0, m_mask16} << m_base;
  wire [63:0] m_val_dbl = {16'd0, main_val & m_mask16, 16'd0, main_val & m_mask16} << m_base;
  /* verilator lint_on UNUSEDSIGNAL */
  wire [18:0] m_mask = m_mask_dbl[50:32];
  wire [18:0] m_val = m_val_dbl[50:32];

  // Side-set: up to three pins.
  wire [2:0]  s_mask3 = (r_side_count == 2'd0) ? 3'b000 : (r_side_count == 2'd1) ? 3'b001 :
                        (r_side_count == 2'd2) ? 3'b011 : 3'b111;
  /* verilator lint_off UNUSEDSIGNAL */
  wire [63:0] s_mask_dbl = {29'd0, s_mask3, 29'd0, s_mask3} << r_side_base;
  wire [63:0] s_val_dbl = {29'd0, side_val & s_mask3, 29'd0, side_val & s_mask3} << r_side_base;
  /* verilator lint_on UNUSEDSIGNAL */
  wire [18:0] s_mask = s_mask_dbl[50:32];
  wire [18:0] s_val = s_val_dbl[50:32];

  wire        m_wr = run & done;
  wire        s_wr = run & side_on;
  wire [18:0] mp_mask = (m_wr & main_pins) ? m_mask : 19'd0;
  wire [18:0] md_mask = (m_wr & main_dirs) ? m_mask : 19'd0;
  wire [18:0] sp_mask = (s_wr & ~r_side_pindir) ? s_mask : 19'd0;
  wire [18:0] sd_mask = (s_wr & r_side_pindir) ? s_mask : 19'd0;

  assign po_mask = mp_mask | sp_mask;
  assign po_val  = (mp_mask & m_val & ~sp_mask) | (sp_mask & s_val);
  assign pd_mask = md_mask | sd_mask;
  assign pd_val  = (md_mask & m_val & ~sd_mask) | (sd_mask & s_val);

  // ------------------------------------------------------------------ register read-back
  always @* begin
    case (cfg_raddr)
      `BW_O_CLKDIV_INT:  cfg_rdata = r_div_int;
      `BW_O_CLKDIV_FRAC: cfg_rdata = {8'd0, r_div_frac};
      `BW_O_WRAP:        cfg_rdata = {2'd0, r_wrap_top, 2'd0, r_wrap_bot};
      `BW_O_ORG:         cfg_rdata = {3'd0, r_jmp_pin, 2'd0, r_org};
      `BW_O_PINCTRL0:    cfg_rdata = {1'b0, r_set_base, r_out_base, r_in_base};
      `BW_O_PINCTRL1:    cfg_rdata = {r_side_en, r_side_count, r_set_count, r_out_count, r_side_base};
      `BW_O_SHIFTCTRL:   cfg_rdata = {3'd0, r_side_pindir, r_out_right, r_in_right, r_autopull, r_autopush,
                                      r_pull_thresh, r_push_thresh};
      `BW_O_FIFOCTRL:    cfg_rdata = {11'd0, r_chain_src, r_chain_en, r_join_rx, r_join_tx};
      `BW_O_TMO:         cfg_rdata = r_tmo;
      `BW_O_BAU_POLY:    cfg_rdata = r_poly;
      `BW_O_BAU_CFG:     cfg_rdata = {12'd0, r_stuff_any, r_stuff_n};
      `BW_O_FIFO:        cfg_rdata = rx_empty ? 16'd0 : rx_head;
      `BW_O_EXEC:        cfg_rdata = ir;
      `BW_O_STAT:        cfg_rdata = {rx_level, tx_level, 1'b0, xq, pc};
      default:           cfg_rdata = 16'd0;
    endcase
  end

`ifdef FORMAL
  `include "bw_sm_formal.vh"
`endif

endmodule
