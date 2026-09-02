# IMC Prosperity 4 — Algorithmic Trading

My strategies for [IMC Prosperity 4](https://prosperity.imc.com/), a global algorithmic
trading competition. Competitors write a Python trading agent that quotes and takes
liquidity across a set of simulated products, and are ranked on realised PnL against
thousands of other participants.

**Result:** Top 3 in Denmark, top ~1000 globally (rounds 1–2).

---

## How it works

Each product exposes an order book (`OrderDepth`) every tick. The agent receives the
full market state, decides which orders to place, and is filled against resting liquidity
and the simulation's bots. The core challenge is that each product behaves differently —
some drift, some mean-revert, some are driven by predictable bot flow — so a single
generic market-making rule leaves a lot of PnL on the table.

My approach was to characterise each product from backtest data first, then write a
tailored strategy per product rather than one strategy for all.

---

## Strategies

### Drift harvesting (persistently trending product)

One product drifted steadily in a single direction. The edge is real but not free:
quoting passively on both sides leaks the drift straight back to counterparties. The
strategy instead:

- holds a long position and **buys aggressively** up to a fixed premium over fair value,
  since paying a few ticks to capture a deterministic drift is worth it;
- tracks a **rolling drift estimate** over a moving window of mid-prices;
- **pauses buying** when the drift weakens and **unwinds gradually** if it reverses —
  a regime-change stop that protects against the trend ending.

### Wide market-making (mean-reverting product)

A second product oscillated around a stable anchor near 10,000. The key insight came from
inspecting the **bot fill histogram**: counterparties crossed the spread 7–8 ticks off the
anchor, not inside it, so tight quotes almost never filled at a profit. The strategy:

- quotes **wide** (fair ± 7) to sit where flow actually occurs;
- applies a light **mean-reversion tilt** based on the last mid-price move;
- adds a **soft inventory skew** to lean against building one-sided positions — kept
  gentle, because aggressive skew at wide quotes pushes prices past the fill zone;
- still **takes** any resting order that is already through fair value.

---

## What changed between rounds

Round 1 used tight symmetric quotes on both products. Backtesting exposed two problems:
the drift product was leaking edge through passive asks, and the mean-reverting product's
quotes were too tight to catch bot flow. Round 2 addressed both, plus added the regime
stop. Net effect on the Round 2 backtest: **+11.5% realised PnL** (283,060 vs 253,832
for the baseline).

---

## Backtesting note

One important lesson: Prosperity's live matching uses `worse` fill logic — quotes resting
at the best bid/offer go **behind** the existing queue — whereas the local backtester's
default (`--match-trades all`) fills optimistically. To get realistic backtest numbers you
have to quote strictly one tick inside the bot's best bid/offer, otherwise the backtester
over-reports fills that never happen live.

---

## Repository layout

```
rounds/
  round2_trader.py    # final Round 2 strategy (drift harvesting + wide MM)
```

---

## Stack

Python · order-book microstructure · market-making · mean-reversion · drift/regime detection
