# Polymarket Trading Framework - Implementation Guide

## Overview
This framework provides systematic approaches to trading on Polymarket with proper risk management. It focuses on **arbitrage** and **statistical edge** rather than the risky "tail-betting" strategy described in your original document.

---

## Strategy Types

### 1. Pure Arbitrage (Riskless)
**Goal**: Lock in guaranteed profits with zero directional risk

#### Type A: Multi-Outcome Arbitrage
When all outcomes in a mutually exclusive market sum to ≠ $1.00:

**Sell All Arbitrage** (prices sum > $1):
- Condition: Σ(all YES prices) > $1.00
- Action: Sell YES on all outcomes
- Profit: (Sum of prices - $1.00) × (1 - 2×fees)
- Example: Outcomes at 28c, 26c, 25c, 24c = $1.03
  - Sell all four, collect $1.03, pay out $1.00 = 3c profit

**Buy All Arbitrage** (prices sum < $1):
- Condition: Σ(all YES prices) < $1.00
- Action: Buy YES on all outcomes
- Profit: ($1.00 - Sum of prices) × (1 - 2×fees)
- Example: Outcomes at 22c, 23c, 24c, 24c = $0.93
  - Buy all four for 93c, collect $1.00 = 7c profit

**Real-World Constraints**:
- Opportunities rare and fleeting (seconds to minutes)
- Requires API access for fast execution
- Need sufficient liquidity at displayed prices
- Fees (2% on Polymarket) eat into thin margins
- Capital locked until market resolution

#### Type B: Cross-Market Arbitrage
Find logically related markets with inconsistent pricing:

**Temporal Inconsistency**:
```
Market A: "Event by March" = 45c
Market B: "Event by June" = 40c
Logic: If event happens by March, it happened by June
Arb: Market A should be ≤ Market B
Action: Buy June @ 40c, Sell March @ 45c = 5c locked profit
```

**Subset Inconsistency**:
```
Market A: "BTC hits $150k in 2026" = 35c
Market B: "BTC hits $200k in 2026" = 40c
Logic: $200k implies $150k already hit
Arb: Market A should be ≥ Market B
Action: Buy $150k @ 35c, Sell $200k @ 40c = 5c locked profit
```

**Complementary Events**:
```
Market has 3 mutually exclusive outcomes:
- Fed cuts rates: 30c
- Fed holds rates: 65c
- Fed raises rates: 8c
Sum = 103c
Action: Sell all three = 3c locked profit per share
```

---

### 2. Statistical Arbitrage (Model-Based)
**Goal**: Exploit mispricing using probability models

#### Core Concept
Your model estimates "fair value" probability. When market price differs significantly, there's an edge to exploit.

**Framework**:
```
Fair Value = Your probability estimate (from model)
Market Price = Current contract price
Edge = Fair Value - Market Price

If Edge > threshold (e.g., 5c): Trade has positive expected value
```

#### Kelly Criterion Position Sizing
**Never bet your entire bankroll**. Use Kelly Criterion to determine optimal bet size:

```
Full Kelly = (Odds × Win_Prob - Lose_Prob) / Odds
Fractional Kelly = Full Kelly × Fraction

Recommended: Use 1/4 Kelly (25%) for safety
```

**Example**:
```
Market Price: 45c
Fair Value: 65c (your estimate)
Confidence: 80% (account for estimation error)

Adjusted Win Prob: 0.65 × 0.80 = 0.52
Odds: (1/0.45) - 1 = 1.22
Full Kelly: (1.22 × 0.52 - 0.48) / 1.22 = 12.9%
Quarter Kelly: 12.9% × 0.25 = 3.2% of bankroll

If bankroll = $10,000:
Position size = $320
Shares to buy = 320 / 0.45 = 711 shares
```

#### Expected Value Calculation
```
EV = (Win_Prob × $1.00) - (Lose_Prob × Cost)
ROI = EV / Cost

Example:
Win_Prob = 0.65
Cost = 0.45c
EV = (0.65 × 1.00) - (0.35 × 0.45) = 0.4925
ROI = 0.4925 / 0.45 = 109% expected return
```

**Only trade when**:
1. Edge > minimum threshold (e.g., 5c)
2. Expected ROI > minimum (e.g., 20%)
3. Kelly fraction > 0 (positive expected value)

---

### 3. Mean Reversion Strategy
**Goal**: Exploit temporary overreactions in market pricing

#### Method
Track price deviation from fair value over time. When deviation becomes extreme (high z-score), bet on reversion:

```
Z-Score = (Current_Deviation - Mean_Deviation) / Std_Deviation

If Z-Score > 2.0: Price too high → SELL
If Z-Score < -2.0: Price too low → BUY
```

**Requirements**:
- At least 20 price observations
- Stable underlying fair value estimate
- Works best in markets with frequent price updates

---

## Risk Management Framework

### Position-Level Limits
1. **Max Position Size**: 10% of bankroll per position
   - Prevents overconcentration
   - Even with high conviction, never exceed this

2. **Stop Loss**: -25% on any position
   - Cut losses before they destroy bankroll
   - Reassess your model if stops hit frequently

3. **Take Profit**: Consider taking profits at +50%
   - Lock in gains on winning positions
   - Can re-enter if edge remains

### Portfolio-Level Limits
1. **Max Market Exposure**: 25% in single market
   - Sum of all positions (multiple outcomes) in one market
   - Limits correlation risk

2. **Max Total Risk**: 50% of bankroll at risk
   - Sum of all capital deployed
   - Keeps 50% in reserve for opportunities

3. **Diversification**: Minimum 5 markets
   - Don't put all eggs in one basket
   - Reduces event-specific risk

### Execution Rules
1. **Always use limit orders** - Never market orders
2. **Check liquidity** - Ensure sufficient volume at target price
3. **Account for slippage** - Large orders move prices
4. **Monitor fills** - Partial fills create directional exposure
5. **Set alerts** - Don't manually watch markets 24/7

---

## Building Your Probability Model

### Components of a Good Model

#### 1. Base Rate (40% weight)
Historical frequency of similar events:
```python
# Example: Presidential election
# Republicans won 10 of last 20 elections
base_rate = 10 / 20 = 0.50
```

#### 2. Recent Trend (30% weight)
Short-term momentum indicators:
- Recent polls
- Betting flow direction
- News sentiment shift

#### 3. Sentiment Analysis (20% weight)
- Social media mentions
- Expert predictions
- News coverage tone

#### 4. Volume Signals (10% weight)
- Order flow imbalance
- Liquidity changes
- Smart money indicators

### Model Calibration
**Track your accuracy** using Brier Score:
```
Brier Score = (1/N) × Σ(forecast - outcome)²

Perfect calibration = 0.00
Random guessing = 0.25
Lower is better
```

Maintain a log of:
- Your predictions
- Actual outcomes
- Calculate Brier score monthly
- Adjust model weights if score deteriorates

---

## Common Pitfalls to Avoid

### ❌ Don't Do This
1. **Buying multiple outcomes without full hedge**
   - Example: Buy top 3 of 4 outcomes (95c total)
   - You're just betting against the longshot
   - Not "low risk" - it's selling insurance
   - One black swan wipes you out

2. **Overconfidence in fair value estimates**
   - Always use fractional Kelly (not full)
   - Account for model error
   - Markets know things you don't

3. **Ignoring fees**
   - 2% fee on each trade
   - Buy + Sell = 4% round-trip cost
   - Need >4% edge just to break even

4. **Chasing arbitrage without checking liquidity**
   - Displayed price ≠ available size
   - May only get partial fills
   - Creates unwanted directional exposure

5. **Martingale / revenge trading**
   - Never double down after losses
   - Each trade should stand on own merits
   - Emotional decisions = bad decisions

### ✅ Do This Instead
1. **Wait for clear opportunities**
   - Quality over quantity
   - 3-5 high-conviction trades > 20 marginal ones

2. **Size positions properly**
   - Use Kelly Criterion
   - Respect position limits
   - Leave room for future opportunities

3. **Keep detailed records**
   - Track every trade
   - Calculate realized returns
   - Learn from mistakes

4. **Start small**
   - Test strategies with small size
   - Scale up after proving profitability
   - Protect capital while learning

---

## Performance Expectations

### Realistic Returns
- **Pure Arbitrage**: 0.5-3% per trade, rare opportunities
- **Statistical Arbitrage**: 15-30% annual return (if model is good)
- **Mean Reversion**: 10-20% annual return

### Win Rate Expectations
- **Pure Arbitrage**: 100% (if executed correctly)
- **Statistical Arbitrage**: 55-60% win rate is excellent
- **Mean Reversion**: 50-55% win rate

**Key insight**: You don't need a high win rate. You need:
- Win rate × Avg Win Size > Lose rate × Avg Loss Size
- Proper position sizing
- Consistent execution

---

## Tools Provided in This Framework

### 1. Python Scripts
- `arbitrage_scanner.py` - Detect arbitrage opportunities
- `risk_management.py` - Kelly calculator, position sizing, portfolio metrics

### 2. Excel Dashboard
- **Arbitrage Scanner**: Identify multi-outcome and cross-market arbs
- **Kelly Calculator**: Compute optimal position sizes
- **Position Tracker**: Monitor all open positions
- **Risk Dashboard**: Portfolio-level risk metrics
- **Market Analysis**: Mean reversion signals, z-scores

### 3. Usage
```bash
# Run arbitrage scanner
python arbitrage_scanner.py

# Test risk management
python risk_management.py
```

---

## Next Steps

1. **Paper Trade First**
   - Track opportunities without real money
   - Validate your model accuracy
   - Build confidence in execution

2. **Start Small**
   - Begin with $1,000-$5,000
   - Use conservative position sizing (1/4 Kelly)
   - Focus on learning, not profits

3. **Build Your Model**
   - Choose a market category (politics, crypto, sports)
   - Develop domain expertise
   - Track predictions vs outcomes

4. **Iterate and Improve**
   - Calculate monthly Brier score
   - Analyze winning vs losing trades
   - Refine model and sizing

5. **Scale Gradually**
   - Only increase size after 3 months of profitability
   - Never risk more than you can afford to lose
   - Keep emergency fund separate

---

## Resources

### Learning
- **Kelly Criterion**: "Fortune's Formula" by William Poundstone
- **Probability**: "Thinking in Bets" by Annie Duke
- **Market Making**: "Trading and Exchanges" by Larry Harris

### Tools
- Polymarket API: https://docs.polymarket.com/
- Historical data: Can scrape from Polymarket or use third-party aggregators
- Brier score calculator: Build into your tracking spreadsheet

### Community
- Polymarket Discord: Trade ideas and discussion
- Prediction market forums: LessWrong, Metaculus
- Quant trading communities: QuantConnect, Quantopian archives

---

## Final Thoughts

The strategy in your original document has a critical flaw: it's **not** low-risk. It's **high-risk, high-consistency** - you win small amounts frequently until a rare event wipes you out. This is the classic "picking up pennies in front of a steamroller."

This framework emphasizes:
1. **True arbitrage** when available (rare but riskless)
2. **Model-based trading** with proper sizing (positive expected value)
3. **Risk management** that protects capital (survive to trade another day)

Success in prediction markets comes from:
- **Better information** (faster or more accurate)
- **Better models** (quantifying probabilities)
- **Better execution** (sizing, timing, discipline)

Start conservative, learn continuously, and let your edge compound over time.

Good luck! 🎯
