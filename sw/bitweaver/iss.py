"""Cycle-accurate instruction-set simulator of the whole Bitweaver chip.

`Bitweaver.step()` advances the model by one clock cycle. It takes the pad inputs that were stable
before the rising clock edge and returns the pad outputs after it, so the model can be compared with
the RTL pin for pin, cycle for cycle. Internally it mirrors the RTL: every piece of state is a
register, and one call computes all next-state values from the current state and the inputs.

The model is the reference for the RTL (see docs/verification.md): the lock-step tests load the same
program and stimulus into both and require identical outputs on every cycle.
"""

from __future__ import annotations

from collections import deque

from . import regmap as R

MASK16 = 0xFFFF
_SM_OFF = {r.addr: r.name for r in R.SM_REGS}
_PIN_OUT_LO_MASK = R.PIN_OUT_MASK & 0xFFFF
_PIN_OD_LO_MASK = R.PIN_OD_MASK & 0xFFFF
_PIN_FILT_LO_MASK = R.PIN_FILT_MASK & 0xFFFF


def _rev16(v: int) -> int:
    return int(f"{v & MASK16:016b}"[::-1], 2)


class SM:
    """State and configuration of one engine."""

    def __init__(self, index: int):
        self.index = index
        self.cfg = {r.name: r.reset for r in R.SM_REGS if r.name not in ("FIFO", "EXEC", "STAT")}
        self.tx: deque[int] = deque()
        self.rx: deque[int] = deque()
        self.decode_cfg()
        self.restart(0)

    def decode_cfg(self) -> None:
        c = self.cfg
        self.div_int = c["CLKDIV_INT"]
        self.div_frac = c["CLKDIV_FRAC"]
        self.wrap_bot = c["WRAP"] & 63
        self.wrap_top = (c["WRAP"] >> 8) & 63
        self.org = c["ORG"] & 63
        self.jmp_pin = (c["ORG"] >> 8) & 31
        self.in_base = c["PINCTRL0"] & 31
        self.out_base = (c["PINCTRL0"] >> 5) & 31
        self.set_base = (c["PINCTRL0"] >> 10) & 31
        self.side_base = c["PINCTRL1"] & 31
        self.out_count = (c["PINCTRL1"] >> 5) & 31
        self.set_count = (c["PINCTRL1"] >> 10) & 7
        self.side_count = (c["PINCTRL1"] >> 13) & 3
        self.side_en = (c["PINCTRL1"] >> 15) & 1
        s = c["SHIFTCTRL"]
        self.push_thresh = (s & 15) or 16
        self.pull_thresh = ((s >> 4) & 15) or 16
        self.autopush = (s >> 8) & 1
        self.autopull = (s >> 9) & 1
        self.in_right = (s >> 10) & 1
        self.out_right = (s >> 11) & 1
        self.side_pindir = (s >> 12) & 1
        f = c["FIFOCTRL"]
        self.join_tx = f & 1
        self.join_rx = (f >> 1) & 1 if not self.join_tx else 0
        self.chain_en = (f >> 2) & 1
        self.chain_src = (f >> 3) & 3
        self.tx_depth = 8 if self.join_tx else (0 if self.join_rx else R.FIFO_DEPTH)
        self.rx_depth = 0 if self.join_tx else (8 if self.join_rx else R.FIFO_DEPTH)
        self.tmo = c["TMO"]
        self.poly = c["BAU_POLY"]
        self.stuff_n = c["BAU_CFG"] & 7
        self.stuff_any = (c["BAU_CFG"] >> 3) & 1

    def restart(self, ir: int) -> None:
        self.pc = self.org
        self.ir = ir
        self.x = self.y = self.isr = self.osr = 0
        self.isr_cnt = 0
        self.osr_cnt = 16
        self.delay = 0
        self.xq = self.xh = self.iw = self.tw = 0
        self.tmo_cnt = 0
        self.div_cnt = self.div_acc = 0
        self.crc = 0
        self.run_len = self.run_val = 0
        self.level = 1
        self.phase = 0
        self.berr = 0
        self.mode = 0

    def nxt(self, pc: int) -> int:
        return self.wrap_bot if pc == self.wrap_top else (pc + 1) & 63


class Bitweaver:
    """The whole chip. Call `step()` once per clock cycle."""

    def __init__(self, coverage: dict | None = None):
        self.cov = coverage
        self.cycle = 0
        self._reset()
        self.rs1 = self.rs2 = 0

    # ------------------------------------------------------------------ reset
    def _reset(self) -> None:
        self.imem = [0] * R.IMEM_DEPTH
        self.sm = [SM(i) for i in range(R.NUM_SM)]
        self.en = 0
        self.int_en = 0
        self.irq = 0
        self.fdebug = 0
        self.ts_prescale = 0
        self.ts_pre = 0
        self.time = 0
        self.pin_out = 0
        self.pin_oe = 0
        self.pad_od = 0
        self.pad_filt = 0
        # Input synchronizer and glitch filter: bits 7:0 = ui_in, bits 15:8 = uio_in.
        self.s1 = self.s2 = 0x0080
        self.s3 = self.s4 = self.fo = 0
        # Host SPI target.
        self.sck_q = 0
        self.bitcnt = 0
        self.phase = 0
        self.cmd_w = self.cmd_inc = 0
        self.addr = 0
        self.sr_in = 0
        self.sr_out = R.SPI_ID_BYTE << 8
        self.ld = 0
        self.wr_en = 0
        self.wr_addr = 0
        self.wr_data = 0
        self.rd_pop = 0
        self.rd_armed = 0

    # ------------------------------------------------------------------ pads
    def outputs(self) -> tuple[int, int, int]:
        """Return (uo_out, uio_out, uio_oe) for the current state."""
        drive = self.pin_out & self.pin_oe
        miso = (self.sr_out >> 15) & ~(self.s2 >> 7) & 1
        irq = 1 if self._int_stat() & self.int_en else 0
        uo = ((drive >> 5) & 0x3F) | (irq << 6) | (miso << 7)
        uio_out = ((self.pin_out & ~self.pad_od) >> 11) & 0xFF
        uio_oe = ((self.pin_oe & ~(self.pad_od & self.pin_out)) >> 11) & 0xFF
        return uo, uio_out, uio_oe

    def pins(self) -> int:
        """The 19 pin inputs as the engines see them."""
        f = self.pad_filt
        filt16 = (f & 0x1F) | (((f >> 11) & 0xFF) << 8)
        inp = (self.fo & filt16) | (self.s2 & ~filt16)
        return (inp & 0x1F) | (self.pin_out & self.pin_oe & 0x7E0) | (((inp >> 8) & 0xFF) << 11)

    def _int_stat(self) -> int:
        v = self.irq
        for k, sm in enumerate(self.sm):
            if sm.rx:
                v |= 1 << (8 + k)
            if len(sm.tx) < sm.tx_depth:
                v |= 1 << (12 + k)
        return v

    def _fstat(self) -> int:
        v = 0
        for k, sm in enumerate(self.sm):
            if len(sm.tx) >= sm.tx_depth:
                v |= 1 << k
            if not sm.tx:
                v |= 1 << (4 + k)
            if len(sm.rx) >= sm.rx_depth:
                v |= 1 << (8 + k)
            if not sm.rx:
                v |= 1 << (12 + k)
        return v

    # ------------------------------------------------------------------ register read
    def peek(self, addr: int) -> int:
        """Value a host read of `addr` returns, without side effects."""
        addr &= 0xFF
        if addr >= R.IMEM_BASE:
            return self.imem[addr & 63] if addr < R.IMEM_BASE + R.IMEM_DEPTH else 0
        if addr >= R.SM_BASE:
            k = (addr - R.SM_BASE) >> 4
            if k >= R.NUM_SM:
                return 0
            sm = self.sm[k]
            name = _SM_OFF.get(addr & 15)
            if name is None:
                return 0
            if name == "FIFO":
                return sm.rx[0] if sm.rx else 0
            if name == "EXEC":
                return sm.ir
            if name == "STAT":
                return sm.pc | (sm.xq << 6) | (len(sm.tx) << 8) | (len(sm.rx) << 12)
            return sm.cfg[name]
        if addr == 0x00:
            return R.CHIP_ID
        if addr == 0x01:
            return R.CHIP_VERSION
        if addr == 0x02:
            return self.en
        if addr == 0x03:
            return self._int_stat()
        if addr == 0x04:
            return self.int_en
        if addr == 0x05:
            return self.irq
        if addr == 0x07:
            return self._fstat()
        if addr == 0x08:
            return self.fdebug
        if addr == 0x09:
            return self.ts_prescale
        if addr == 0x0A:
            return self.time
        if addr == 0x10:
            return self.pins() & MASK16
        if addr == 0x11:
            return self.pins() >> 16
        if addr == 0x12:
            return self.pin_out & MASK16
        if addr == 0x13:
            return self.pin_out >> 16
        if addr == 0x14:
            return self.pin_oe & MASK16
        if addr == 0x15:
            return self.pin_oe >> 16
        if addr == 0x16:
            return self.pad_od & MASK16
        if addr == 0x17:
            return self.pad_od >> 16
        if addr == 0x18:
            return self.pad_filt & MASK16
        if addr == 0x19:
            return self.pad_filt >> 16
        return 0

    # ------------------------------------------------------------------ one clock cycle
    def step(self, ui_in: int = 0x80, uio_in: int = 0, rst_n: int = 1, ena: int = 1) -> tuple[int, int, int]:
        self.cycle += 1
        if not rst_n:
            self.rs1 = self.rs2 = 0
            self._reset()
            return self.outputs()
        if not self.rs2:
            self.rs2 = self.rs1
            self.rs1 = 1
            return self.outputs()

        sms = self.sm
        # ---- registered host strobes from the previous cycle
        wr_en, wa, wd = self.wr_en, self.wr_addr, self.wr_data
        rd_pop, rd_addr = self.rd_pop, self.addr

        # ---- snapshot of the state the engines and the FIFO movers see
        pins19 = self.pins()
        flags = self.irq
        tx_empty = [not sm.tx for sm in sms]
        tx_full = [len(sm.tx) >= sm.tx_depth for sm in sms]
        rx_empty = [not sm.rx for sm in sms]
        rx_full = [len(sm.rx) >= sm.rx_depth for sm in sms]
        rx_head = [sm.rx[0] if sm.rx else 0 for sm in sms]

        # ---- host SPI target (uses the synchronized pins)
        cs_n = (self.s2 >> 7) & 1
        sck = (self.s2 >> 6) & 1
        mosi = (self.s2 >> 5) & 1
        self.wr_en = 0
        self.rd_pop = 0
        if cs_n:
            self.bitcnt = 0
            self.phase = 0
            self.cmd_w = self.cmd_inc = 0
            self.ld = 0
            self.sr_out = R.SPI_ID_BYTE << 8
        else:
            rise = sck and not self.sck_q
            fall = self.sck_q and not sck
            if rise:
                word = ((self.sr_in << 1) | mosi) & MASK16
                self.sr_in = word
                if self.phase == 0:
                    if self.bitcnt == 7:
                        self.cmd_w = (word >> 7) & 1
                        self.cmd_inc = (word >> 6) & 1
                        self.phase = 1
                        self.bitcnt = 0
                        self.ld = 1
                    else:
                        self.bitcnt += 1
                elif self.phase == 1:
                    if self.bitcnt == 7:
                        self.addr = word & 0xFF
                        self.phase = 2
                        self.bitcnt = 0
                        self.ld = 1
                    else:
                        self.bitcnt += 1
                else:
                    if self.bitcnt == 0 and not self.cmd_w:
                        self.rd_pop = 1
                    if self.bitcnt == 15:
                        if self.cmd_w:
                            self.wr_en = 1
                            self.wr_addr = self.addr
                            self.wr_data = word
                        self.addr = (self.addr + self.cmd_inc) & 0xFF
                        self.bitcnt = 0
                        self.ld = 1
                    else:
                        self.bitcnt += 1
            elif fall:
                if self.ld:
                    self.ld = 0
                    if self.phase == 1:
                        st = 0
                        for k in range(R.NUM_SM):
                            if not tx_full[k]:
                                st |= 1 << k
                            if not rx_empty[k]:
                                st |= 16 << k
                        self.sr_out = st << 8
                    else:
                        self.sr_out = self.peek(self.addr)
                        k = self._fifo_index(self.addr)
                        self.rd_armed = 1 if k is not None and not rx_empty[k] else 0
                else:
                    self.sr_out = (self.sr_out << 1) & MASK16
        self.sck_q = sck

        # ---- decode the host write
        restart = 0
        host_exec = None
        host_push = None
        fifo_clr = None
        if wr_en:
            if wa >= R.IMEM_BASE:
                if wa < R.IMEM_BASE + R.IMEM_DEPTH:
                    self.imem[wa & 63] = wd  # write-first: an engine fetching this address sees wd
            elif wa >= R.SM_BASE:
                k = (wa - R.SM_BASE) >> 4
                name = _SM_OFF.get(wa & 15)
                if k < R.NUM_SM:
                    if name == "EXEC":
                        host_exec = k
                    elif name == "FIFO":
                        host_push = k
                    elif name == "FIFOCTRL":
                        fifo_clr = k
            elif wa == 0x02:
                restart = (wd >> 4) & 15
        host_pop = None
        rxunder = 0
        if rd_pop:
            k = self._fifo_index(rd_addr)
            if k is not None:
                if self.rd_armed:
                    host_pop = k
                else:
                    rxunder = 1 << k

        # ---- engines
        po_mask = po_val = pd_mask = pd_val = 0
        irq_set = irq_clr = 0
        tx_pop = [False] * R.NUM_SM
        rx_push: list[int | None] = [None] * R.NUM_SM
        dbg = rxunder << 4
        vbase = pins19 | (flags << R.VPIN_IRQ0)
        for k, sm in enumerate(sms):
            en = (self.en >> k) & 1
            tick = en and sm.div_cnt == 0
            # clock divider
            if not en:
                sm.div_cnt = sm.div_acc = 0
            elif sm.div_cnt == 0:
                s = sm.div_acc + sm.div_frac
                sm.div_acc = s & 0xFF
                sm.div_cnt = (sm.div_int - 1 + (s >> 8)) & MASK16
            else:
                sm.div_cnt -= 1

            if (restart >> k) & 1:
                sm.restart(self.imem[sm.org])
                continue

            run = sm.xh or (tick and sm.delay == 0)
            done = False
            exec_load = None
            newpc = sm.pc
            if run:
                pins = vbase
                if tx_empty[k]:
                    pins |= 1 << R.VPIN_TXE
                if rx_full[k]:
                    pins |= 1 << R.VPIN_RXF
                if (sm.mode & R.BAU_STUFF) and sm.stuff_n and sm.run_len == sm.stuff_n and (
                        sm.stuff_any or sm.run_val):
                    pins |= 1 << R.VPIN_STUFF
                if sm.berr:
                    pins |= 1 << R.VPIN_BERR
                res = self._exec(k, sm, pins, flags, tx_empty[k], rx_full[k])
                done, newpc, exec_load, e_po_m, e_po_v, e_pd_m, e_pd_v, e_set, e_clr, e_pop, e_push, e_dbg = res
                # Higher-numbered engines override lower ones on the same pin.
                po_val = (po_val & ~e_po_m) | e_po_v
                po_mask |= e_po_m
                pd_val = (pd_val & ~e_pd_m) | e_pd_v
                pd_mask |= e_pd_m
                irq_set |= e_set
                irq_clr |= e_clr
                tx_pop[k] = e_pop
                rx_push[k] = e_push
                dbg |= e_dbg
                if done:
                    if not sm.xh:
                        sm.delay = self._delay(sm, sm.ir)
                    sm.tw = 0
            elif tick and not sm.xh and sm.delay:
                sm.delay -= 1

            # program counter and instruction register
            if run and done:
                sm.pc = newpc
            if host_exec == k:
                sm.ir = wd
                sm.xq = sm.xh = 1
                sm.iw = sm.tw = 0
            elif run and done:
                if exec_load is not None:
                    sm.ir = exec_load
                    sm.xq = 1
                else:
                    sm.ir = self.imem[sm.pc]
                    sm.xq = sm.xh = 0
            elif not sm.xq:
                sm.ir = self.imem[sm.pc]

        # ---- FIFOs
        for k, sm in enumerate(sms):
            if host_push == k and tx_full[k]:
                dbg |= 1 << k
        moves = []
        for d, sm in enumerate(sms):
            if sm.chain_en:
                s = sm.chain_src
                if not rx_empty[s] and not tx_full[d] and host_push != d and host_pop != s:
                    moves.append((s, d))
        rx_popped = [False] * R.NUM_SM
        if host_pop is not None and not rx_empty[host_pop]:
            rx_popped[host_pop] = True
        for s, d in moves:
            rx_popped[s] = True
        for k, sm in enumerate(sms):
            if tx_pop[k]:
                sm.tx.popleft()
            if rx_popped[k]:
                sm.rx.popleft()
        for k, sm in enumerate(sms):
            if host_push == k:
                if not tx_full[k]:
                    sm.tx.append(wd)
            if rx_push[k] is not None:
                sm.rx.append(rx_push[k])
        for s, d in moves:
            sms[d].tx.append(rx_head[s])
        if fifo_clr is not None:
            sms[fifo_clr].tx.clear()
            sms[fifo_clr].rx.clear()

        # ---- shared registers: host write first, then engine effects on top
        nxt_out, nxt_oe = self.pin_out, self.pin_oe
        prescale = self.ts_prescale  # a prescaler write takes effect from the next cycle
        w1c_irq = force_irq = w1c_dbg = 0
        time_wr = False
        if wr_en and wa < R.SM_BASE:
            if wa == 0x02:
                self.en = wd & 15
            elif wa == 0x04:
                self.int_en = wd
            elif wa == 0x05:
                w1c_irq = wd & 0xFF
            elif wa == 0x06:
                force_irq = wd & 0xFF
            elif wa == 0x08:
                w1c_dbg = wd
            elif wa == 0x09:
                self.ts_prescale = wd & 0xFF
            elif wa == 0x0A:
                time_wr = True
            elif wa == 0x12:
                nxt_out = (nxt_out & ~MASK16) | (wd & _PIN_OUT_LO_MASK)
            elif wa == 0x13:
                nxt_out = (nxt_out & MASK16) | ((wd & 7) << 16)
            elif wa == 0x14:
                nxt_oe = (nxt_oe & ~MASK16) | (wd & _PIN_OUT_LO_MASK)
            elif wa == 0x15:
                nxt_oe = (nxt_oe & MASK16) | ((wd & 7) << 16)
            elif wa == 0x16:
                self.pad_od = (self.pad_od & ~MASK16) | (wd & _PIN_OD_LO_MASK)
            elif wa == 0x17:
                self.pad_od = (self.pad_od & MASK16) | ((wd & 7) << 16)
            elif wa == 0x18:
                self.pad_filt = (self.pad_filt & ~MASK16) | (wd & _PIN_FILT_LO_MASK)
            elif wa == 0x19:
                self.pad_filt = (self.pad_filt & MASK16) | ((wd & 7) << 16)
        elif wr_en and wa < R.IMEM_BASE:
            k = (wa - R.SM_BASE) >> 4
            name = _SM_OFF.get(wa & 15)
            if k < R.NUM_SM and name in sms[k].cfg:
                reg = R.SM_BY_NAME[name]
                sms[k].cfg[name] = wd & reg.mask
                sms[k].decode_cfg()
        self.pin_out = ((nxt_out & ~po_mask) | po_val) & R.PIN_OUT_MASK
        self.pin_oe = ((nxt_oe & ~pd_mask) | pd_val) & R.PIN_OUT_MASK
        self.irq = (self.irq & ~(w1c_irq | irq_clr)) | irq_set | force_irq
        self.fdebug = (self.fdebug & ~w1c_dbg) | dbg

        # ---- timestamp
        if time_wr:
            self.time = wd
            self.ts_pre = 0
        elif ena:
            if self.ts_pre == prescale:
                self.ts_pre = 0
                self.time = (self.time + 1) & MASK16
            else:
                self.ts_pre = (self.ts_pre + 1) & 0xFF

        # ---- input synchronizer and glitch filter
        agree = ~(self.s2 ^ self.s3) & ~(self.s3 ^ self.s4) & MASK16
        self.fo = (self.fo & ~agree) | (self.s2 & agree)
        self.s4 = self.s3
        self.s3 = self.s2
        self.s2 = self.s1
        self.s1 = (ui_in & 0xFF) | ((uio_in & 0xFF) << 8)
        return self.outputs()

    @staticmethod
    def _fifo_index(addr: int) -> int | None:
        if R.SM_BASE <= addr < R.SM_BASE + R.NUM_SM * R.SM_STRIDE and (addr & 15) == R.SM_BY_NAME["FIFO"].addr:
            return (addr - R.SM_BASE) >> 4
        return None

    # ------------------------------------------------------------------ instruction helpers
    @staticmethod
    def _side_delay(sm: SM, ir: int) -> tuple[int, int, int]:
        """Split the 5-bit delay/side-set field. Returns (side_active, side_value, delay)."""
        fld5 = (ir >> 8) & 31
        k = sm.side_count
        if sm.side_en:
            active = (fld5 >> 4) & 1 if k else 0
            dbits = 4 - k
        else:
            active = 1 if k else 0
            dbits = 5 - k
        side = (fld5 >> dbits) & ((1 << k) - 1)
        return active, side, fld5 & ((1 << dbits) - 1)

    def _delay(self, sm: SM, ir: int) -> int:
        return self._side_delay(sm, ir)[2]

    @staticmethod
    def _spread(value: int, base: int, count: int) -> tuple[int, int]:
        """Place `count` low bits of `value` on pins base, base+1, ... Returns (mask, value)."""
        m = (1 << min(count, 16)) - 1
        v = value & m
        mask = ((m << base) | (m >> (32 - base))) & R.PIN_OUT_MASK
        val = ((v << base) | (v >> (32 - base))) & R.PIN_OUT_MASK
        return mask, val

    def _hit(self, *key) -> None:
        if self.cov is not None:
            self.cov[key] = self.cov.get(key, 0) + 1

    def _exec(self, k: int, sm: SM, pins: int, flags: int, tx_empty: bool, rx_full: bool):
        """Evaluate the engine's instruction register for one tick.

        Updates the engine's private registers in place and returns the effects on shared state:
        (done, newpc, exec_load, po_mask, po_val, pd_mask, pd_val, irq_set, irq_clr, tx_pop, rx_push,
        fdebug_bits).
        """
        ir = sm.ir
        op = ir >> 13
        arg = ir & 0xFF
        po_m = po_v = pd_m = pd_v = 0
        irq_set = irq_clr = 0
        tx_pop = False
        rx_push = None
        dbg = 0
        exec_load = None
        done = True
        seq0 = sm.pc if sm.xq else sm.nxt(sm.pc)
        newpc = seq0

        if op == R.OP_JMP:
            cond = arg >> 5
            if cond == R.COND_ALWAYS:
                take = True
            elif cond == R.COND_NOT_X:
                take = sm.x == 0
            elif cond == R.COND_X_DEC:
                take = sm.x != 0
                sm.x = (sm.x - 1) & MASK16
            elif cond == R.COND_NOT_Y:
                take = sm.y == 0
            elif cond == R.COND_Y_DEC:
                take = sm.y != 0
                sm.y = (sm.y - 1) & MASK16
            elif cond == R.COND_X_NE_Y:
                take = sm.x != sm.y
            elif cond == R.COND_PIN:
                take = bool((pins >> sm.jmp_pin) & 1)
            else:
                take = sm.osr_cnt < sm.pull_thresh
            if take:
                newpc = (sm.org + (arg & 31)) & 63
            self._hit("JMP", R.JMP_CONDS[cond], "taken" if take else "not taken")

        elif op == R.OP_WAIT:
            pol = arg >> 7
            timed = (arg >> 6) & 1
            idx = arg & 31
            if (arg >> 5) & 1:
                f = self._irq_index(k, idx)
                met = ((flags >> f) & 1) == pol
                if met and pol:
                    irq_clr = 1 << f
                src = "irq"
            else:
                met = ((pins >> ((sm.in_base + idx) & 31)) & 1) == pol
                src = "pin"
            if met:
                if timed:
                    newpc = sm.nxt(sm.pc) if sm.xq else sm.nxt(sm.nxt(sm.pc))
                outcome = "met"
            elif timed:
                remaining = sm.tmo_cnt if sm.tw else sm.tmo
                if remaining == 0:
                    outcome = "timeout"
                else:
                    sm.tmo_cnt = remaining - 1
                    sm.tw = 1
                    done = False
                    outcome = "stall"
            else:
                done = False
                outcome = "stall"
            self._hit("WAIT", src, pol, "timed" if timed else "untimed", outcome)

        elif op == R.OP_IN:
            src = arg >> 5
            bau = (arg >> 4) & 1
            c = 1 if bau else ((arg & 15) or 16)
            v = self._source(sm, src, pins)
            outcome = "ok"
            if bau:
                raw = v & 1
                bit = (1 if raw == sm.level else 0) if sm.mode & R.BAU_NRZI else raw
                is_stuff = bool((pins >> R.VPIN_STUFF) & 1)
            else:
                is_stuff = False
                bit = 0
            if not is_stuff and sm.autopush and rx_full and min(sm.isr_cnt + c, 16) >= sm.push_thresh:
                done = False
                dbg |= 1 << (12 + k)
                outcome = "stall"
            else:
                if bau:
                    sm.level = raw
                    if is_stuff:
                        if bit == sm.run_val:
                            sm.berr = 1
                        sm.run_val = bit
                        sm.run_len = 1
                        outcome = "destuff"
                    else:
                        v = bit
                        self._bau_track(sm, bit)
                if not is_stuff:
                    data = v & ((1 << c) - 1)
                    if sm.in_right:
                        sm.isr = ((sm.isr >> c) | (data << (16 - c))) & MASK16
                    else:
                        sm.isr = ((sm.isr << c) | data) & MASK16
                    sm.isr_cnt = min(sm.isr_cnt + c, 16)
                    if sm.autopush and sm.isr_cnt >= sm.push_thresh:
                        rx_push = sm.isr
                        sm.isr = 0
                        sm.isr_cnt = 0
                        outcome = "autopush" if outcome == "ok" else outcome
            self._hit("IN", R.SOURCES[src], "bau" if bau else "raw", outcome)

        elif op == R.OP_OUT:
            dst = arg >> 5
            bau = (arg >> 4) & 1
            c = 1 if bau else ((arg & 15) or 16)
            is_stuff = bool(bau and (pins >> R.VPIN_STUFF) & 1)
            outcome = "ok"
            if not is_stuff and sm.autopull and sm.osr_cnt >= sm.pull_thresh:
                done = False
                if tx_empty:
                    dbg |= 1 << (8 + k)
                    outcome = "stall"
                else:
                    sm.osr = sm.tx[0]
                    sm.osr_cnt = 0
                    tx_pop = True
                    outcome = "refill"
            else:
                consume = True
                if bau:
                    if is_stuff:
                        s = sm.run_val ^ 1
                    else:
                        s = (sm.osr & 1) if sm.out_right else (sm.osr >> 15)
                    if sm.mode & R.BAU_MAN:
                        enc = s ^ 1 if sm.phase == 0 else s
                        consume = sm.phase == 1
                        sm.phase ^= 1
                    elif sm.mode & R.BAU_NRZI:
                        enc = sm.level if s else sm.level ^ 1
                    else:
                        enc = s
                    sm.level = enc
                    d = ((enc ^ 1) << 1) | enc
                    if consume and is_stuff:
                        sm.run_val = s
                        sm.run_len = 1
                        outcome = "stuff"
                    elif consume:
                        self._bau_track(sm, s)
                    else:
                        outcome = "half"
                if consume and not is_stuff:
                    if sm.out_right:
                        if not bau:
                            d = sm.osr & ((1 << c) - 1)
                        sm.osr >>= c
                    else:
                        if not bau:
                            d = (sm.osr >> (16 - c)) & ((1 << c) - 1)
                        sm.osr = (sm.osr << c) & MASK16
                    sm.osr_cnt = min(sm.osr_cnt + c, 16)
                    if sm.autopull and sm.osr_cnt >= sm.pull_thresh and not tx_empty:
                        sm.osr = sm.tx[0]
                        sm.osr_cnt = 0
                        tx_pop = True
                        outcome = "autopull" if outcome == "ok" else outcome
                if dst == R.OUT_PINS:
                    po_m, po_v = self._spread(d, sm.out_base, sm.out_count)
                elif dst == R.OUT_X:
                    sm.x = d
                elif dst == R.OUT_Y:
                    sm.y = d
                elif dst == R.OUT_PINDIRS:
                    pd_m, pd_v = self._spread(d, sm.out_base, sm.out_count)
                elif dst == R.OUT_PC:
                    newpc = (sm.org + (d & 31)) & 63
                elif dst == R.OUT_ISR:
                    sm.isr = d
                    sm.isr_cnt = c
                elif dst == R.OUT_EXEC:
                    exec_load = d
            self._hit("OUT", R.OUT_DESTS[dst], "bau" if bau else "raw", outcome)

        elif op == R.OP_PUSHPULL:
            cond = (arg >> 6) & 1
            block = (arg >> 5) & 1
            if arg & 0x80:
                if cond and sm.osr_cnt < sm.pull_thresh:
                    outcome = "skip"
                elif tx_empty:
                    dbg |= 1 << (8 + k)
                    if block:
                        done = False
                        outcome = "stall"
                    else:
                        sm.osr = sm.x
                        sm.osr_cnt = 0
                        outcome = "empty"
                else:
                    sm.osr = sm.tx[0]
                    sm.osr_cnt = 0
                    tx_pop = True
                    outcome = "ok"
                self._hit("PULL", "ifempty" if cond else "always", "block" if block else "noblock", outcome)
            else:
                if cond and sm.isr_cnt < sm.push_thresh:
                    outcome = "skip"
                elif rx_full:
                    dbg |= 1 << (12 + k)
                    if block:
                        done = False
                        outcome = "stall"
                    else:
                        sm.isr = 0
                        sm.isr_cnt = 0
                        outcome = "drop"
                else:
                    rx_push = sm.isr
                    sm.isr = 0
                    sm.isr_cnt = 0
                    outcome = "ok"
                self._hit("PUSH", "iffull" if cond else "always", "block" if block else "noblock", outcome)

        elif op == R.OP_MOV:
            dst = arg >> 5
            mop = (arg >> 3) & 3
            src = arg & 7
            v = self._source(sm, src, pins)
            if mop == R.MOVOP_INVERT:
                v ^= MASK16
            elif mop == R.MOVOP_REVERSE:
                v = _rev16(v)
            elif mop == R.MOVOP_SWAP:
                v = ((v << 8) | (v >> 8)) & MASK16
            if dst == R.MOV_PINS:
                po_m, po_v = self._spread(v, sm.out_base, sm.out_count)
            elif dst == R.MOV_X:
                sm.x = v
            elif dst == R.MOV_Y:
                sm.y = v
            elif dst == R.MOV_CRC:
                sm.crc = v
            elif dst == R.MOV_EXEC:
                exec_load = v
            elif dst == R.MOV_PC:
                newpc = (sm.org + (v & 31)) & 63
            elif dst == R.MOV_ISR:
                sm.isr = v
                sm.isr_cnt = 0
            else:
                sm.osr = v
                sm.osr_cnt = 0
            self._hit("MOV", R.MOV_DESTS[dst], R.MOV_OPS[mop], R.SOURCES[src])

        elif op == R.OP_IRQ:
            f = self._irq_index(k, arg & 31)
            if arg & 0x40:
                irq_clr = 1 << f
                outcome = "clear"
            elif arg & 0x20:
                if not sm.iw:
                    irq_set = 1 << f
                    sm.iw = 1
                    done = False
                    outcome = "set+wait"
                elif (flags >> f) & 1:
                    done = False
                    outcome = "waiting"
                else:
                    sm.iw = 0
                    outcome = "wait done"
            else:
                irq_set = 1 << f
                outcome = "set"
            self._hit("IRQ", outcome, "rel" if arg & 0x10 else "abs")

        else:  # SET
            dst = arg >> 5
            data = arg & 31
            if dst == R.SET_PINS:
                po_m, po_v = self._spread(data, sm.set_base, sm.set_count)
            elif dst == R.SET_X:
                sm.x = data
            elif dst == R.SET_Y:
                sm.y = data
            elif dst == R.SET_BAU:
                sm.mode = data & 15
                if data & R.BAU_RESET:
                    sm.run_len = sm.run_val = 0
                    sm.level = 1
                    sm.phase = 0
                    sm.berr = 0
            elif dst == R.SET_PINDIRS:
                pd_m, pd_v = self._spread(data, sm.set_base, sm.set_count)
            self._hit("SET", R.SET_DESTS[dst] or "reserved")

        # Side-set applies on every tick the instruction is evaluated, stalled or not, and wins
        # over OUT/SET/MOV on the same pin.
        active, side, _ = self._side_delay(sm, ir)
        if active:
            m, v = self._spread(side, sm.side_base, sm.side_count)
            if sm.side_pindir:
                pd_v = (pd_v & ~m) | v
                pd_m |= m
            else:
                po_v = (po_v & ~m) | v
                po_m |= m
            self._hit("SIDE", "pindirs" if sm.side_pindir else "pins")
        if not done:
            # A stalled instruction changes no shared state except side-set, the IRQ flag raised by
            # IRQ WAIT, and the FIFO pop of an autopull refill.
            if op != R.OP_OUT:
                tx_pop = False
            rx_push = None
        return (done, newpc, exec_load, po_m, po_v, pd_m, pd_v, irq_set, irq_clr, tx_pop, rx_push, dbg)

    @staticmethod
    def _irq_index(k: int, idx: int) -> int:
        if idx & 0x10:
            return (idx & 4) | ((idx + k) & 3)
        return idx & 7

    def _source(self, sm: SM, src: int, pins: int) -> int:
        if src == R.SRC_PINS:
            b = sm.in_base
            return ((pins >> b) | (pins << (32 - b))) & MASK16
        if src == R.SRC_X:
            return sm.x
        if src == R.SRC_Y:
            return sm.y
        if src == R.SRC_NULL:
            return 0
        if src == R.SRC_TIME:
            return self.time
        if src == R.SRC_CRC:
            return sm.crc
        if src == R.SRC_ISR:
            return sm.isr
        return sm.osr

    @staticmethod
    def _bau_track(sm: SM, bit: int) -> None:
        """Account for one data bit in the CRC and the run-length counter."""
        if sm.mode & R.BAU_CRC:
            fb = (sm.crc ^ bit) & 1
            sm.crc = (sm.crc >> 1) ^ (sm.poly if fb else 0)
        if sm.mode & R.BAU_STUFF:
            if bit == sm.run_val:
                sm.run_len = min(sm.run_len + 1, 7)
            else:
                sm.run_val = bit
                sm.run_len = 1

    # ------------------------------------------------------------------ fast host access for tests
    def poke(self, addr: int, data: int, **pads) -> None:
        """Write a register as the SPI port would, without simulating the SPI bit clock.

        Takes two clock cycles: one to present the write strobe and one for it to take effect.
        """
        self.wr_en, self.wr_addr, self.wr_data = 1, addr & 0xFF, data & MASK16
        self.step(**pads)

    def pop(self, sm: int, **pads) -> int:
        """Pop one word from an engine's RX FIFO as an SPI read would."""
        value = self.peek(R.addr("FIFO", sm))
        self.rd_armed = 1 if self.sm[sm].rx else 0
        self.rd_pop, self.addr = 1, R.addr("FIFO", sm)
        self.step(**pads)
        return value

    def run(self, cycles: int, **pads) -> None:
        for _ in range(cycles):
            self.step(**pads)
