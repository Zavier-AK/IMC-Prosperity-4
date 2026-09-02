"""
IMC Prosperity 4 — Round 2 Trader

Two-product strategy:
  - IPR: a persistently drifting asset. Hold long and harvest the drift,
         with a rolling regime check that pauses buying (and unwinds) if the
         drift weakens or reverses.
  - ACO: a mean-reverting asset anchored near a stable fair value. Market-make
         with wide quotes, a light mean-reversion tilt, and soft inventory skew.

Result: 283,060 units of realised PnL across Round 2 days (-1, 0, 1),
        vs 253,832 for the baseline strategy (+11.5%).

Design notes carried over from Round 1:
  1. Removed passive asks on IPR — they were leaking the drift back to
     counterparties. Switched to aggressive buying up to fair + 7.
  2. Widened ACO quotes to fair ± 7 (from ± 1). Bot fill histograms showed
     counterparties crossing 7–8 ticks off the 10,000 anchor, not inside it,
     so tight quotes were never getting hit at a profit.
  3. Softened ACO inventory skew (pos / 60 rather than pos / 15). At wide
     quotes, aggressive skew pushed quotes past where flow actually occurs.
  4. Added a regime-change stop on IPR: if rolling drift falls below a
     threshold, pause new buys; if it turns negative, unwind gradually.
"""

from typing import List

from datamodel import Order, OrderDepth, TradingState
import json


class Trader:
    # Position limit. Set to 100 if the +25% volume contract is won.
    LIMIT = 80

    # --- IPR (drift-harvesting) parameters ---
    IPR_STOP_WINDOW = 300          # rolling window (ticks) for drift estimation
    IPR_PAUSE_DRIFT = 0.03         # pause new buys if rolling drift falls below this
    IPR_REVERSE_DRIFT = -0.02      # unwind if rolling drift turns negative past this
    IPR_AGGRESSIVE_BUY_THRESH = 7  # take any ask at or below fair + this

    # --- ACO (market-making) parameters ---
    ACO_BASE_WIDTH = 7             # passive quote half-width around fair
    ACO_FAIR = 10000               # anchor; the EMA only refines this marginally
    ACO_INV_SKEW_DIV = 60          # larger divisor => softer inventory skew

    def run(self, state: TradingState):
        result = {}
        data = {}
        if state.traderData:
            try:
                data = json.loads(state.traderData)
            except (ValueError, TypeError):
                data = {}

        if "INTARIAN_PEPPER_ROOT" in state.order_depths:
            result["INTARIAN_PEPPER_ROOT"] = self._trade_ipr(state, data)
        if "ASH_COATED_OSMIUM" in state.order_depths:
            result["ASH_COATED_OSMIUM"] = self._trade_aco(state, data)

        return result, 0, json.dumps(data)

    # ------------------------------------------------------------------
    # IPR — hold long, harvest drift, regime-aware safety stop
    # ------------------------------------------------------------------
    def _trade_ipr(self, state: TradingState, data: dict) -> List[Order]:
        orders: List[Order] = []
        product = "INTARIAN_PEPPER_ROOT"
        od = state.order_depths[product]
        pos = state.position.get(product, 0)
        buy_cap = self.LIMIT - pos
        sell_cap = self.LIMIT + pos

        best_bid = max(od.buy_orders) if od.buy_orders else None
        best_ask = min(od.sell_orders) if od.sell_orders else None
        if best_bid is None and best_ask is None:
            return orders

        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2
        else:
            mid = best_ask if best_ask is not None else best_bid
        fair = round(mid)

        # Track a rolling mid-price window to estimate drift.
        hist = data.get("ipr_hist", [])
        hist.append(mid)
        if len(hist) > self.IPR_STOP_WINDOW:
            hist = hist[-self.IPR_STOP_WINDOW:]
        data["ipr_hist"] = hist

        rolling_drift = None
        if len(hist) >= 50:
            rolling_drift = (hist[-1] - hist[0]) / len(hist)

        regime_ok = rolling_drift is None or rolling_drift >= self.IPR_PAUSE_DRIFT
        regime_reversed = (
            rolling_drift is not None and rolling_drift <= self.IPR_REVERSE_DRIFT
        )

        # Drift is close to deterministic, so it is worth paying up to capture it.
        if regime_ok and od.sell_orders:
            for price in sorted(od.sell_orders):
                if price <= fair + self.IPR_AGGRESSIVE_BUY_THRESH and buy_cap > 0:
                    qty = min(-od.sell_orders[price], buy_cap)
                    if qty > 0:
                        orders.append(Order(product, price, qty))
                        buy_cap -= qty

        # If the regime has reversed, exit the long gradually at the best bid.
        if regime_reversed and pos > 0 and od.buy_orders:
            unwind_size = min(sell_cap, max(1, pos // 10))
            best = max(od.buy_orders)
            orders.append(Order(product, best, -unwind_size))
            sell_cap -= unwind_size

        # Otherwise, only sell into an extreme premium as a rare safety valve.
        elif od.buy_orders:
            for price in sorted(od.buy_orders, reverse=True):
                if price >= fair + 8 and sell_cap > 0:
                    qty = min(od.buy_orders[price], sell_cap)
                    if qty > 0:
                        orders.append(Order(product, price, -qty))
                        sell_cap -= qty

        # A single passive bid to catch any friendly flow at a small discount.
        if regime_ok and buy_cap > 0:
            orders.append(Order(product, fair - 1, buy_cap))

        return orders

    # ------------------------------------------------------------------
    # ACO — wide market-making with mean-reversion tilt and soft skew
    # ------------------------------------------------------------------
    def _trade_aco(self, state: TradingState, data: dict) -> List[Order]:
        orders: List[Order] = []
        product = "ASH_COATED_OSMIUM"
        od = state.order_depths[product]
        pos = state.position.get(product, 0)
        buy_cap = self.LIMIT - pos
        sell_cap = self.LIMIT + pos

        best_bid = max(od.buy_orders) if od.buy_orders else None
        best_ask = min(od.sell_orders) if od.sell_orders else None
        if best_bid is None or best_ask is None:
            return orders
        mid = (best_bid + best_ask) / 2

        # Slow EMA around the anchor. Marginal benefit, essentially free.
        ema = data.get("aco_ema") or float(self.ACO_FAIR)
        ema = 0.02 * mid + 0.98 * ema
        data["aco_ema"] = ema
        fair = round(ema)

        # Mean-reversion tilt from the last mid-price move.
        prev_mid = data.get("aco_pm")
        d_mid = 0 if prev_mid is None else mid - prev_mid
        data["aco_pm"] = mid
        mr_shift = 0
        if d_mid >= 3:
            mr_shift = -1
        elif d_mid <= -3:
            mr_shift = 1

        # Take any resting order that is already through fair value.
        if od.sell_orders:
            for price in sorted(od.sell_orders):
                if price < fair and buy_cap > 0:
                    qty = min(-od.sell_orders[price], buy_cap)
                    if qty > 0:
                        orders.append(Order(product, price, qty))
                        buy_cap -= qty
        if od.buy_orders:
            for price in sorted(od.buy_orders, reverse=True):
                if price > fair and sell_cap > 0:
                    qty = min(od.buy_orders[price], sell_cap)
                    if qty > 0:
                        orders.append(Order(product, price, -qty))
                        sell_cap -= qty

        # Passive quotes: wide, with the MR tilt and a soft inventory skew.
        inv_skew = round(pos / self.ACO_INV_SKEW_DIV)
        bid_price = fair - self.ACO_BASE_WIDTH + mr_shift - inv_skew
        ask_price = fair + self.ACO_BASE_WIDTH + mr_shift - inv_skew
        bid_price = min(bid_price, fair - 1)
        ask_price = max(ask_price, fair + 1)

        if buy_cap > 0:
            orders.append(Order(product, bid_price, buy_cap))
        if sell_cap > 0:
            orders.append(Order(product, ask_price, -sell_cap))

        return orders
