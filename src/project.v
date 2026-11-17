/*
 * Copyright (c) 2026 nnphanusf
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bitweaver: a programmable protocol emulator.
 *
 * Four identical engines (bw_sm) run small programs from a shared 64-word instruction memory and
 * drive a 19-pin virtual pin space. A host loads programs and moves data over an SPI target port.
 * See docs/architecture.md for the block diagram and docs/isa.md for the instruction set.
 *
 * Pin plan (fixed; see docs/info.md):
 *   ui_in[7]  HOST_CS_N    ui_in[6] HOST_SCK    ui_in[5] HOST_MOSI
 *   uo_out[7] HOST_MISO    uo_out[6] HOST_IRQ
 *   ui_in[4:0]  -> PIN0..PIN4   (input)
 *   uo_out[5:0] -> PIN5..PIN10  (output; engines read back the driven level)
 *   uio[7:0]    -> PIN11..PIN18 (bidirectional, optional open drain)
 *
 * The cycle-accurate reference model of this file is sw/bitweaver/iss.py (Bitweaver.step).
 */

`default_nettype none
`include "bw_defs.vh"

module tt_um_nnphanusf_bitweaver (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  localparam NSM = 4;
  localparam [18:0] OUT_MASK = 19'h7FFE0;  // PIN5..PIN18 have output registers
  localparam [18:0] OD_MASK = 19'h7F800;   // PIN11..PIN18 support open drain
  localparam [18:0] FILT_MASK = 19'h7F81F; // PIN0..4 and PIN11..18 have glitch filters

  // ------------------------------------------------------------------ reset synchronizer
  // rst_n asserts asynchronously and releases two clock edges later, synchronously.
  reg rs1, rs2;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rs1 <= 1'b0;
      rs2 <= 1'b0;
    end else begin
      rs1 <= 1'b1;
      rs2 <= rs1;
    end
  end
  wire rstn = rs2;

  // ------------------------------------------------------------------ input synchronizers and filters
  // 2-FF synchronizer on every input; the 13 protocol inputs also get an optional glitch filter that
  // only passes a level once it has been stable for three clock cycles.
  reg [15:0] s1, s2;
  reg [12:0] s3, s4, fo;
  wire [12:0] s2p = {s2[15:8], s2[4:0]};  // protocol inputs only
  wire [12:0] agree = ~(s2p ^ s3) & ~(s3 ^ s4);
  always @(posedge clk or negedge rstn) begin
    if (!rstn) begin
      s1 <= 16'h0080;
      s2 <= 16'h0080;
      s3 <= 13'd0;
      s4 <= 13'd0;
      fo <= 13'd0;
    end else begin
      s1 <= {uio_in, ui_in};
      s2 <= s1;
      s3 <= s2p;
      s4 <= s3;
      fo <= (fo & ~agree) | (s2p & agree);
    end
  end

  wire host_cs_n = s2[7];
  wire host_sck = s2[6];
  wire host_mosi = s2[5];

  // ------------------------------------------------------------------ shared registers
  reg  [3:0]  en;
  reg  [15:0] int_en;
  reg  [7:0]  irq;
  reg  [15:0] fdebug;
  reg  [7:0]  ts_prescale, ts_pre;
  reg  [15:0] ts;
  reg  [18:0] pin_out, pin_oe, pad_od, pad_filt;

  wire [12:0] filt13 = {pad_filt[18:11], pad_filt[4:0]};
  wire [12:0] inp13 = (fo & filt13) | (s2p & ~filt13);
  wire [18:0] pins_in = {inp13[12:5], pin_out[10:5] & pin_oe[10:5], inp13[4:0]};

  // ------------------------------------------------------------------ host port
  wire        wr_en, rd_load, rd_pop;
  wire [7:0]  wr_addr, rd_addr;
  wire [15:0] wr_data;
  reg  [15:0] rd_data;
  wire [7:0]  spi_status;
  wire        miso;

  bw_host_spi u_host (
      .clk(clk), .rst_n(rstn),
      .cs_n(host_cs_n), .sck(host_sck), .mosi(host_mosi), .miso(miso),
      .status(spi_status),
      .rd_addr(rd_addr), .rd_data(rd_data), .rd_load(rd_load), .rd_pop(rd_pop),
      .wr_en(wr_en), .wr_addr(wr_addr), .wr_data(wr_data)
  );

  wire wr_global = wr_en && (wr_addr < `BW_SM_BASE);
  wire wr_imem = wr_en && (wr_addr[7:6] == 2'b10);
  wire [NSM-1:0] wr_sm;
  wire [NSM-1:0] restart = (wr_en && wr_addr == `BW_A_CTRL) ? wr_data[7:4] : 4'd0;

  // Engine register windows are 0x20..0x5F; rd_k is the engine a read address belongs to.
  wire       rd_is_sm = (rd_addr >= `BW_SM_BASE) && (rd_addr < 8'h60);
  wire [1:0] rd_k = rd_addr[5:4] ^ 2'b10;
  // A FIFO read only pops if the FIFO held data when the word was latched for the host.
  wire       rd_is_fifo_sm = rd_is_sm && (rd_addr[3:0] == `BW_O_FIFO);
  reg        rd_armed;

  // ------------------------------------------------------------------ instruction memory
  wire [NSM*6-1:0]  pc_next;
  wire [NSM*16-1:0] imem_rdata;
  wire [15:0]       imem_host_rdata;
  bw_imem #(.NUM_SM(NSM)) u_imem (
      .clk(clk), .rst_n(rstn),
      .we(wr_imem), .waddr(wr_addr[5:0]), .wdata(wr_data),
      .sm_raddr(pc_next), .sm_rdata(imem_rdata),
      .host_raddr(rd_addr[5:0]), .host_rdata(imem_host_rdata)
  );

  // ------------------------------------------------------------------ engines
  wire [NSM*16-1:0] sm_rdata;
  wire [NSM*19-1:0] po_mask, po_val, pd_mask, pd_val;
  wire [NSM*8-1:0]  irq_set, irq_clr;
  wire [NSM-1:0]    tx_full, tx_empty, rx_full, rx_empty, host_push, chain_en;
  wire [NSM*2-1:0]  chain_src;
  wire [NSM*16-1:0] rx_head;
  wire [NSM-1:0]    ev_txover, ev_txstall, ev_rxstall;
  reg  [NSM-1:0]    rx_pop, chain_push;
  reg  [NSM*16-1:0] chain_wdata;

  genvar g;
  generate
    for (g = 0; g < NSM; g = g + 1) begin : g_sm
      assign wr_sm[g] = wr_en && (wr_addr[7:4] == (4'd2 + g));
      bw_sm u_sm (
          .clk(clk), .rst_n(rstn), .sm_id(g[1:0]), .en(en[g]), .restart(restart[g]),
          .cfg_we(wr_sm[g]), .cfg_waddr(wr_addr[3:0]), .cfg_wdata(wr_data),
          .cfg_raddr(rd_addr[3:0]), .cfg_rdata(sm_rdata[g*16+:16]),
          .pc_next(pc_next[g*6+:6]), .imem_rdata(imem_rdata[g*16+:16]),
          .pins_in(pins_in), .irq_flags(irq), .ts(ts),
          .po_mask(po_mask[g*19+:19]), .po_val(po_val[g*19+:19]),
          .pd_mask(pd_mask[g*19+:19]), .pd_val(pd_val[g*19+:19]),
          .irq_set(irq_set[g*8+:8]), .irq_clr(irq_clr[g*8+:8]),
          .tx_full(tx_full[g]), .tx_empty(tx_empty[g]), .rx_full(rx_full[g]), .rx_empty(rx_empty[g]),
          .rx_head(rx_head[g*16+:16]), .host_push(host_push[g]),
          .rx_pop(rx_pop[g]), .chain_push(chain_push[g]), .chain_wdata(chain_wdata[g*16+:16]),
          .chain_en(chain_en[g]), .chain_src(chain_src[g*2+:2]),
          .ev_txover(ev_txover[g]), .ev_txstall(ev_txstall[g]), .ev_rxstall(ev_rxstall[g])
      );
    end
  endgenerate

  // ------------------------------------------------------------------ FIFO movers (host pops, chaining)
  wire [NSM-1:0] host_pop;
  wire [NSM-1:0] rxunder;
  generate
    for (g = 0; g < NSM; g = g + 1) begin : g_pop
      assign host_pop[g] = rd_pop & rd_is_fifo_sm & (rd_k == g) & rd_armed;
      assign rxunder[g] = rd_pop & rd_is_fifo_sm & (rd_k == g) & ~rd_armed;
    end
  endgenerate

  integer k;
  reg [1:0] cs;
  reg       mv;
  always @* begin
    rx_pop = host_pop;
    chain_push = 4'd0;
    chain_wdata = {NSM * 16{1'b0}};
    for (k = 0; k < NSM; k = k + 1) begin
      cs = chain_src[k*2+:2];
      mv = chain_en[k] & ~rx_empty[cs] & ~tx_full[k] & ~host_push[k] & ~host_pop[cs];
      chain_push[k] = mv;
      chain_wdata[k*16+:16] = rx_head[cs*16+:16];
      if (mv) rx_pop[cs] = 1'b1;
    end
  end

  // ------------------------------------------------------------------ merge engine effects
  // Higher-numbered engines win on a pin that several engines write in the same cycle.
  reg [18:0] m_po_mask, m_po_val, m_pd_mask, m_pd_val;
  reg [7:0]  m_irq_set, m_irq_clr;
  always @* begin
    m_po_mask = 19'd0;
    m_po_val = 19'd0;
    m_pd_mask = 19'd0;
    m_pd_val = 19'd0;
    m_irq_set = 8'd0;
    m_irq_clr = 8'd0;
    for (k = 0; k < NSM; k = k + 1) begin
      m_po_val = (m_po_val & ~po_mask[k*19+:19]) | po_val[k*19+:19];
      m_po_mask = m_po_mask | po_mask[k*19+:19];
      m_pd_val = (m_pd_val & ~pd_mask[k*19+:19]) | pd_val[k*19+:19];
      m_pd_mask = m_pd_mask | pd_mask[k*19+:19];
      m_irq_set = m_irq_set | irq_set[k*8+:8];
      m_irq_clr = m_irq_clr | irq_clr[k*8+:8];
    end
  end

  // ------------------------------------------------------------------ status
  wire [15:0] int_stat = {~tx_full, ~rx_empty, irq};
  wire [15:0] fstat = {rx_empty, rx_full, tx_empty, tx_full};
  assign spi_status = {~rx_empty, ~tx_full};

  // ------------------------------------------------------------------ shared register writes
  wire        hw = wr_global;
  wire [18:0] host_out = (hw && wr_addr == `BW_A_PIN_OUT_LO) ? {pin_out[18:16], wr_data} :
                         (hw && wr_addr == `BW_A_PIN_OUT_HI) ? {wr_data[2:0], pin_out[15:0]} : pin_out;
  wire [18:0] host_oe = (hw && wr_addr == `BW_A_PIN_OE_LO) ? {pin_oe[18:16], wr_data} :
                        (hw && wr_addr == `BW_A_PIN_OE_HI) ? {wr_data[2:0], pin_oe[15:0]} : pin_oe;
  wire [7:0]  w1c_irq = (hw && wr_addr == `BW_A_IRQ) ? wr_data[7:0] : 8'd0;
  wire [7:0]  force_irq = (hw && wr_addr == `BW_A_IRQ_FORCE) ? wr_data[7:0] : 8'd0;
  wire [15:0] w1c_dbg = (hw && wr_addr == `BW_A_FDEBUG) ? wr_data : 16'd0;
  wire [15:0] dbg_ev = {ev_rxstall, ev_txstall, rxunder, ev_txover};

  always @(posedge clk or negedge rstn) begin
    if (!rstn) begin
      en          <= 4'd0;
      int_en      <= 16'd0;
      irq         <= 8'd0;
      fdebug      <= 16'd0;
      ts_prescale <= 8'd0;
      ts_pre      <= 8'd0;
      ts          <= 16'd0;
      pin_out     <= 19'd0;
      pin_oe      <= 19'd0;
      pad_od      <= 19'd0;
      pad_filt    <= 19'd0;
      rd_armed    <= 1'b0;
    end else begin
      if (hw) begin
        case (wr_addr)
          `BW_A_CTRL:        en <= wr_data[3:0];
          `BW_A_INT_EN:      int_en <= wr_data;
          `BW_A_TS_PRESCALE: ts_prescale <= wr_data[7:0];
          `BW_A_PAD_OD_LO:   pad_od[15:0] <= wr_data & OD_MASK[15:0];
          `BW_A_PAD_OD_HI:   pad_od[18:16] <= wr_data[2:0];
          `BW_A_PAD_FILT_LO: pad_filt[15:0] <= wr_data & FILT_MASK[15:0];
          `BW_A_PAD_FILT_HI: pad_filt[18:16] <= wr_data[2:0];
          default: ;
        endcase
      end
      pin_out <= ((host_out & ~m_po_mask) | m_po_val) & OUT_MASK;
      pin_oe  <= ((host_oe & ~m_pd_mask) | m_pd_val) & OUT_MASK;
      irq     <= (irq & ~(w1c_irq | m_irq_clr)) | m_irq_set | force_irq;
      fdebug  <= (fdebug & ~w1c_dbg) | dbg_ev;
      if (hw && wr_addr == `BW_A_TIME) begin
        ts     <= wr_data;
        ts_pre <= 8'd0;
      end else if (ena) begin
        if (ts_pre == ts_prescale) begin
          ts_pre <= 8'd0;
          ts     <= ts + 16'd1;
        end else begin
          ts_pre <= ts_pre + 8'd1;
        end
      end
      if (rd_load) rd_armed <= rd_is_fifo_sm & ~rx_empty[rd_k];
    end
  end

  // ------------------------------------------------------------------ host read mux
  always @* begin
    if (rd_addr[7]) begin
      rd_data = rd_addr[6] ? 16'd0 : imem_host_rdata;
    end else if (rd_is_sm) begin
      rd_data = sm_rdata[rd_k*16+:16];
    end else if (rd_addr >= `BW_SM_BASE) begin
      rd_data = 16'd0;
    end else begin
      case (rd_addr)
        `BW_A_ID:          rd_data = `BW_CHIP_ID;
        `BW_A_VERSION:     rd_data = `BW_CHIP_VERSION;
        `BW_A_CTRL:        rd_data = {12'd0, en};
        `BW_A_INT_STAT:    rd_data = int_stat;
        `BW_A_INT_EN:      rd_data = int_en;
        `BW_A_IRQ:         rd_data = {8'd0, irq};
        `BW_A_FSTAT:       rd_data = fstat;
        `BW_A_FDEBUG:      rd_data = fdebug;
        `BW_A_TS_PRESCALE: rd_data = {8'd0, ts_prescale};
        `BW_A_TIME:        rd_data = ts;
        `BW_A_PIN_IN_LO:   rd_data = pins_in[15:0];
        `BW_A_PIN_IN_HI:   rd_data = {13'd0, pins_in[18:16]};
        `BW_A_PIN_OUT_LO:  rd_data = pin_out[15:0];
        `BW_A_PIN_OUT_HI:  rd_data = {13'd0, pin_out[18:16]};
        `BW_A_PIN_OE_LO:   rd_data = pin_oe[15:0];
        `BW_A_PIN_OE_HI:   rd_data = {13'd0, pin_oe[18:16]};
        `BW_A_PAD_OD_LO:   rd_data = pad_od[15:0];
        `BW_A_PAD_OD_HI:   rd_data = {13'd0, pad_od[18:16]};
        `BW_A_PAD_FILT_LO: rd_data = pad_filt[15:0];
        `BW_A_PAD_FILT_HI: rd_data = {13'd0, pad_filt[18:16]};
        default:           rd_data = 16'd0;
      endcase
    end
  end

  // ------------------------------------------------------------------ pads
  wire host_irq = |(int_stat & int_en);
  wire [5:0] drive = pin_out[10:5] & pin_oe[10:5];

  assign uo_out  = {miso, host_irq, drive};
  assign uio_out = pin_out[18:11] & ~pad_od[18:11];
  assign uio_oe  = pin_oe[18:11] & ~(pad_od[18:11] & pin_out[18:11]);

`ifdef FORMAL
  // Open-drain pins never drive high.
  always @(*) begin
    if (rstn) begin
      assert ((uio_oe & uio_out & pad_od[18:11]) == 8'd0);
      assert ((pin_out & ~OUT_MASK) == 19'd0);
    end
  end
`endif

endmodule
