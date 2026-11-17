// Formal properties of one engine, included into bw_sm under `ifdef FORMAL (see formal/sm_props.sby).

  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  initial assume (!rst_n);

  wire f_in_wrap = (pc >= r_wrap_bot) && (pc <= r_wrap_top);
  wire f_no_jump = !(run && done && (jump_imm || jump_data));

  always @(posedge clk) begin
    if (f_past && rst_n && $past(rst_n)) begin
      // Shift counters and the delay counter stay in range.
      assert (isr_cnt <= 5'd16);
      assert (osr_cnt <= 5'd16);
      // A host-injected instruction is always also an injected instruction.
      if (xh) assert (xq);
      // A timed WAIT in progress only exists while a WAIT instruction is in IR.
      if (tw) assert (op == `BW_OP_WAIT);
      if (iw) assert (op == `BW_OP_IRQ);
      // After a restart the engine is at its origin with clean state.
      if ($past(restart)) begin
        assert (pc == $past(r_org));
        assert (x == 16'd0 && y == 16'd0 && osr_cnt == 5'd16 && isr_cnt == 5'd0 && !xq);
      end
      // The program counter never leaves the wrap range by sequential execution: starting inside
      // [WRAP.BOTTOM, WRAP.TOP], without a jump and without a configuration write, it stays inside.
      if ($past(f_in_wrap) && $past(r_wrap_bot <= r_wrap_top) && $past(f_no_jump) && !$past(restart)
          && !$past(cfg_we))
        assert (f_in_wrap);
      // An engine that is disabled and not running an injected instruction does not move.
      if (!$past(en) && !$past(xh) && !$past(restart) && !$past(cfg_we)) assert (pc == $past(pc));
      // When IR holds a fetched instruction it is the word at PC.
      if (!xq) assert (ir == $past(imem_rdata));
    end
  end

  // Every opcode can execute and complete.
  always @(posedge clk) begin
    if (rst_n) begin
      cover (run && done && op == `BW_OP_JMP && jump_imm);
      cover (run && done && op == `BW_OP_WAIT);
      cover (run && !done && op == `BW_OP_WAIT && arg[6]);
      cover (run && done && op == `BW_OP_IN && rx_push);
      cover (run && done && op == `BW_OP_OUT && tx_pop);
      cover (run && done && op == `BW_OP_PUSHPULL && arg[7]);
      cover (run && done && op == `BW_OP_PUSHPULL && !arg[7]);
      cover (run && done && op == `BW_OP_MOV);
      cover (run && done && op == `BW_OP_IRQ && iw);
      cover (run && done && op == `BW_OP_SET && bau_set);
      cover (run && done && exec_load);
      cover (run && stuff_due);
    end
  end
