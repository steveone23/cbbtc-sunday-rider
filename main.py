"""CBBTC Sunday rider: a long-only cbBTC bot with a trend-gated ladder and a washout-bounce module.

Protocol agent-fun/bars-replay/v1 via vendor/makefun_bars.py. Standard library only, deterministic.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))
import makefun_bars  # noqa: E402

SYMBOL = "CBBTC"
WINDOW_BARS = 1440                   # batches in the scored window, k = 0..1439
START_UNITS = 1_000_000_000          # 1,000 USDC
MIN_NOTIONAL = 1_000_000             # 1 USDC, the smallest buy (and sell, unless it empties the position)

SMA_BARS = 240                       # ladder gate: simple moving average of closes
GATE_BAND = 0.0005                   # ladder starts when close >= SMA * (1 + GATE_BAND)
RUNG2_DROP = 0.004                   # rung 2 is added when close <= first_close * (1 - RUNG2_DROP)
LADDER_TARGET_UNITS = 2_000_000      # bank the ladder once marked equity gain >= 2 USDC net of estimated exit cost
EXIT_COST_BPS = 9                    # estimated cost of selling the position, in bps of its value
LADDER_STOP = 0.02                   # give up the ladder when close <= first_close * (1 - LADDER_STOP)
LADDER_LAST_BAR = 1200               # no new ladder after this k
TWAP_IN = 5                          # ladder buys are cut into this many slices, one per bar

WASH_HIGH_BARS = 240                 # washout: rolling high window
WASH_LOW_BARS = 30                   # washout: rolling low window
WASH_DIP = 0.04                      # washout needs low <= high * (1 - WASH_DIP)
WASH_BOUNCE = 0.0025                 # ... and close >= low * (1 + WASH_BOUNCE)
WASH_CHASE = 0.0095                  # ... and close <= low * (1 + WASH_CHASE)
WASH_TP = 0.04                       # washout take profit vs entry price
WASH_ARM = 0.015                     # washout trailing stop arms once the peak reaches entry * (1 + WASH_ARM)
WASH_TRAIL = 0.008                   # ... then exits at peak * (1 - WASH_TRAIL)
WASH_SL = 0.02                       # washout stop loss vs entry price
WASH_TWAP_IN = 2                     # washout buys are cut into this many slices, one per bar
WASH_MAX_ENTRIES = 2                 # at most this many washout entries per window
WASH_COOLDOWN = 30                   # bars after the last exit before a washout entry
WASH_LAST_BAR = 1380                 # no new washout after this k

TWAP_OUT = 3                         # exits are cut into this many slices, one per bar
PROBE_BAR = 1380                     # if nothing has ever filled by this k, buy MIN_NOTIONAL so the entry has a trade
BUY_SLIPPAGE_BPS = 150               # max slippage on buys
SELL_SLIPPAGE_BPS = 300              # max slippage on sells
HISTORY_KEEP = 1000                  # closes kept in memory

FLAT, LADDER, WASH, EXIT = "FLAT", "LADDER", "WASH", "EXIT"


def load_seed():
    """Seed (t, c) lists from data/seed.json; empty when missing or malformed."""
    try:
        with open(os.path.join(HERE, "data", "seed.json"), encoding="utf-8") as f:
            raw = json.load(f)
        ts = [int(x) for x in raw["t"]]
        cs = [int(x) for x in raw["c"]]
        if len(ts) != len(cs):
            return [], []
        return ts, cs
    except (OSError, ValueError, KeyError, TypeError):
        return [], []


SEED_T, SEED_C = load_seed()


class Bot:
    def __init__(self):
        self.closes = None
        self.mode = FLAT
        # ladder
        self.ladder_done = False
        self.rung = 0
        self.first_close = 0
        self.base_equity = 0
        # washout
        self.wash_entries = 0
        self.entry_price = None
        self.peak = 0
        # execution
        self.last_exit = -100000
        self.buy_left = 0
        self.slice_units = 0
        self.sell_slices = 0
        self.traded = False
        self.pending = {}

    def start_exit(self, slices):
        self.mode = EXIT
        self.buy_left = 0
        self.sell_slices = slices

    def on_batch(self, state, batch):
        # bar count within the scored window, after any warm-up
        k = batch["seq"] - state.warmup_bars

        # the CBBTC bar and the fills of the previous batch's orders
        bar = None
        for b in batch["bars"]:
            if b["symbol"] == SYMBOL:
                bar = b
        for f in batch["fills"]:
            if f["status"] == "filled":
                self.traded = True
                if f["side"] == "buy" and self.mode == WASH and self.entry_price is None:
                    self.entry_price = int(f["price"])
            elif f["status"] == "rejected" and f["side"] == "buy" and self.mode in (LADDER, WASH):
                self.buy_left += self.pending.get(f["client_id"], 0)
        self.pending = {}

        if bar is None:
            return []

        # history: seed bars strictly before the first batch, then every live close
        c = int(bar["c"])
        if self.closes is None:
            t = batch["t"]
            self.closes = [x for tt, x in zip(SEED_T, SEED_C) if tt < t][-HISTORY_KEEP:]
        self.closes.append(c)
        if len(self.closes) > HISTORY_KEEP:
            del self.closes[:-HISTORY_KEEP]
        closes = self.closes
        n = len(closes)
        cash = state.cash
        eq = state.equity
        held = state.positions.get(SYMBOL)
        qty = held.qty if held is not None else 0

        # 1. no orders during a warm-up or on the last batch
        if state.warming_up or k >= WINDOW_BARS - 1:
            return []

        # 2. washout trigger, checked first, from FLAT or on top of a running ladder
        washed = False
        if (self.mode in (FLAT, LADDER) and self.wash_entries < WASH_MAX_ENTRIES and k <= WASH_LAST_BAR
                and k - self.last_exit >= WASH_COOLDOWN and n >= WASH_HIGH_BARS):
            hi = max(closes[-WASH_HIGH_BARS:])
            lo = min(closes[-WASH_LOW_BARS:])
            if lo <= hi * (1 - WASH_DIP) and lo * (1 + WASH_BOUNCE) <= c <= lo * (1 + WASH_CHASE):
                washed = True
                self.mode = WASH
                self.wash_entries += 1
                self.peak = c
                # any ladder buys still to come are replaced by the washout's
                self.buy_left = cash if cash >= MIN_NOTIONAL else 0
                self.slice_units = self.buy_left // WASH_TWAP_IN
                self.entry_price = None if self.buy_left else c

        # 3. ladder start
        if (not washed and self.mode == FLAT and not self.ladder_done and k <= LADDER_LAST_BAR
                and n >= SMA_BARS):
            sma = sum(closes[-SMA_BARS:]) / SMA_BARS
            if c >= sma * (1 + GATE_BAND):
                self.mode = LADDER
                self.rung = 1
                self.first_close = c
                self.base_equity = eq
                self.buy_left = eq // 2
                self.slice_units = self.buy_left // TWAP_IN

        # 4. ladder held: bank, stop, or add rung 2
        elif not washed and self.mode == LADDER:
            est = state.value(SYMBOL, qty) * EXIT_COST_BPS // 10000
            if qty > 0 and eq - est >= self.base_equity + LADDER_TARGET_UNITS:
                self.start_exit(TWAP_OUT)
                self.ladder_done = True
            elif c <= self.first_close * (1 - LADDER_STOP):
                self.start_exit(TWAP_OUT)
                self.ladder_done = True
            elif self.rung == 1 and c <= self.first_close * (1 - RUNG2_DROP):
                self.buy_left += self.base_equity // 2
                self.slice_units = self.buy_left // TWAP_IN
                self.rung = 2

        # 5. washout held: take profit, armed trailing stop, stop loss
        elif not washed and self.mode == WASH:
            self.peak = max(self.peak, c)
            # every washout buy failed on top of a held position: price it at this close
            if self.entry_price is None and qty > 0 and self.buy_left < MIN_NOTIONAL:
                self.entry_price = c
            p = self.entry_price
            if p is not None and qty > 0 and (
                    c >= p * (1 + WASH_TP)
                    or (self.peak >= p * (1 + WASH_ARM) and c <= self.peak * (1 - WASH_TRAIL))
                    or c <= p * (1 - WASH_SL)):
                self.start_exit(TWAP_OUT)

        # 6. end-of-window unwind: sell the rest in the remaining bars
        if k >= WINDOW_BARS - 2 - TWAP_OUT and (self.mode in (LADDER, WASH, EXIT) or qty > 0):
            left = max(1, WINDOW_BARS - 2 - k)
            if self.mode == EXIT:
                self.sell_slices = min(self.sell_slices, left)
            else:
                self.start_exit(left)

        # a module whose buys all failed holds nothing and has nothing left to buy: back to FLAT
        if self.mode in (LADDER, WASH) and qty == 0 and self.buy_left < MIN_NOTIONAL:
            self.mode = FLAT
            self.last_exit = k

        # execution, at most one order
        orders = []
        if self.mode in (LADDER, WASH) and self.buy_left >= MIN_NOTIONAL:
            a = min(self.slice_units, self.buy_left)
            if self.buy_left - a < MIN_NOTIONAL:
                a = self.buy_left
            a = min(a, cash)
            if a >= MIN_NOTIONAL:
                cid = f"b{k}"
                orders.append({"side": "buy", "client_id": cid, "symbol": SYMBOL,
                               "notional_quote_units": a, "max_slippage_bps": BUY_SLIPPAGE_BPS})
                self.pending[cid] = a
                self.buy_left -= a
            else:
                self.buy_left = 0
        elif self.mode == EXIT:
            if qty > 0:
                if self.sell_slices <= 1:
                    q = qty
                else:
                    q = qty // self.sell_slices
                    self.sell_slices -= 1
                    # never leave or send a piece below the minimum notional
                    if state.value(SYMBOL, q) < MIN_NOTIONAL or state.value(SYMBOL, qty - q) < MIN_NOTIONAL:
                        q = qty
                orders.append({"side": "sell", "client_id": f"s{k}", "symbol": SYMBOL,
                               "qty": q, "max_slippage_bps": SELL_SLIPPAGE_BPS})
            else:
                self.mode = FLAT
                self.last_exit = k

        # 7. probe so the entry has at least one trade
        if k == PROBE_BAR and not self.traded and not orders and qty == 0:
            orders.append({"side": "buy", "client_id": f"p{k}", "symbol": SYMBOL,
                           "notional_quote_units": MIN_NOTIONAL, "max_slippage_bps": BUY_SLIPPAGE_BPS})

        return orders


if __name__ == "__main__":
    makefun_bars.run(Bot().on_batch)
