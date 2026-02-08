#!/usr/bin/env python3
"""
Paper Trading Test Script

Runs the arbitrage detection system in paper trading mode:
- Fetches live market data from Polymarket
- Scans for arbitrage opportunities
- Simulates trades WITHOUT using real money
- Writes status to data/bot_status.json for monitoring

Usage:
    python paper_trade_test.py [--duration MINUTES]

Monitor status from another terminal:
    python -m src.monitor status
    python -m src.monitor watch
"""

import asyncio
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from src.monitor import StatusManager

from src.data.websocket_feed import OrderBook, OrderBookLevel
from src.detection.simple_arbitrage import (
    SimpleArbitrageDetector,
    ArbitrageOpportunity,
    MarketPair
)
from src.optimization.ip_solver import (
    Market,
    DependencyConstraint,
    ConstraintType,
    ScipyIPSolver
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class PaperTrader:
    """Paper trading simulator for arbitrage strategies."""

    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.trades = []
        self.opportunities_found = 0
        self.start_time = None

    def simulate_trade(self, opp: ArbitrageOpportunity, position_size: float = 100.0):
        """Simulate executing an arbitrage trade."""
        # Don't trade if profit too small
        if opp.guaranteed_profit < 0.02:
            return None

        # Calculate trade size (limited by liquidity and balance)
        max_size = min(position_size, opp.max_position_size, self.balance * 0.1)
        if max_size < 10:
            return None

        cost = max_size * opp.combined_price
        profit = max_size * opp.guaranteed_profit

        # Simulate the trade
        self.balance -= cost
        self.balance += max_size  # Guaranteed payout of $1 per unit

        trade = {
            "timestamp": datetime.utcnow().isoformat(),
            "market_id": opp.market_id,
            "type": opp.arb_type,
            "size": max_size,
            "cost": cost,
            "profit": profit,
            "balance_after": self.balance
        }
        self.trades.append(trade)

        return trade

    def get_summary(self):
        """Get trading summary."""
        total_profit = self.balance - self.initial_balance
        return {
            "initial_balance": self.initial_balance,
            "final_balance": self.balance,
            "total_profit": total_profit,
            "profit_pct": (total_profit / self.initial_balance) * 100,
            "num_trades": len(self.trades),
            "opportunities_found": self.opportunities_found
        }


async def fetch_live_markets(limit: int = 30) -> list:
    """Fetch live markets from Polymarket."""
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "order": "liquidityNum",
        "ascending": "false"
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=30) as resp:
                if resp.status != 200:
                    logger.error(f"API error: {resp.status}")
                    return []

                data = await resp.json()
                markets = []

                for market in data:
                    liquidity = float(market.get('liquidityNum', 0) or 0)
                    if liquidity < 1000:
                        continue

                    try:
                        token_ids = json.loads(market.get('clobTokenIds', '[]'))
                        prices = json.loads(market.get('outcomePrices', '[]'))
                        outcomes = json.loads(market.get('outcomes', '[]'))
                    except:
                        continue

                    if len(token_ids) >= 2 and len(prices) >= 2:
                        markets.append({
                            'market_id': market.get('conditionId', ''),
                            'question': market.get('question', ''),
                            'outcomes': outcomes,
                            'prices': [float(p) for p in prices],
                            'token_ids': token_ids,
                            'liquidity': liquidity,
                            'best_bid': market.get('bestBid'),
                            'best_ask': market.get('bestAsk')
                        })

                return markets

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []


def check_simple_arbitrage(markets: list) -> list:
    """Check for simple arbitrage opportunities in fetched markets."""
    opportunities = []

    for market in markets:
        prices = market['prices']
        if len(prices) < 2:
            continue

        # For binary markets: YES + NO should equal 1
        price_sum = sum(prices)

        # Buy arbitrage: can buy all outcomes for < $1
        if price_sum < 0.98:
            profit = 1.0 - price_sum
            opportunities.append({
                'type': 'buy',
                'market': market,
                'price_sum': price_sum,
                'profit': profit,
                'profit_pct': profit * 100
            })

        # Sell arbitrage: can sell all outcomes for > $1
        elif price_sum > 1.02:
            profit = price_sum - 1.0
            opportunities.append({
                'type': 'sell',
                'market': market,
                'price_sum': price_sum,
                'profit': profit,
                'profit_pct': profit * 100
            })

    return opportunities


def check_ip_arbitrage(markets: list) -> dict:
    """Use IP solver to check for cross-market arbitrage."""
    solver = ScipyIPSolver()

    # Convert to IP format
    ip_markets = {}
    for m in markets[:20]:  # Limit for performance
        market_id = m['market_id']
        outcomes = m.get('outcomes', ['Yes', 'No'])
        prices = m.get('prices', [0.5, 0.5])

        price_dict = {outcomes[i]: prices[i] for i in range(min(len(outcomes), len(prices)))}
        ip_markets[market_id] = Market(
            market_id=market_id,
            conditions=outcomes,
            prices=price_dict
        )

    # No dependencies for now (would need Claude API)
    constraints = []

    result = solver.solve(ip_markets, constraints, arbitrage_threshold=0.98)
    return {
        'has_arbitrage': result.has_arbitrage,
        'min_cost': result.min_cost,
        'profit': result.profit,
        'positions': {f"{k[0]}:{k[1]}": v for k, v in result.positions.items()}
    }


async def run_paper_trading(duration_minutes: int = 5):
    """Run paper trading simulation."""
    print("\n" + "=" * 60)
    print("PAPER TRADING TEST - NO REAL MONEY")
    print("=" * 60)
    print(f"Duration: {duration_minutes} minutes")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Monitor status: python -m src.monitor watch")
    print("=" * 60)

    trader = PaperTrader(initial_balance=10000.0)
    trader.start_time = datetime.utcnow()
    end_time = trader.start_time + timedelta(minutes=duration_minutes)

    # Initialize status manager for monitoring
    status_mgr = StatusManager()
    status_mgr.start(mode="paper", initial_balance=10000.0)

    scan_count = 0
    all_opportunities = []

    try:
        while datetime.utcnow() < end_time:
            scan_count += 1
            print(f"\n[Scan #{scan_count}] {datetime.now().strftime('%H:%M:%S')}")

            # Fetch live markets
            print("  Fetching live markets...")
            markets = await fetch_live_markets(limit=50)
            print(f"  Found {len(markets)} active markets")

            # Update status
            status_mgr.update_scan(len(markets))

            if not markets:
                await asyncio.sleep(30)
                continue

            # Check for simple arbitrage
            print("  Checking for arbitrage opportunities...")
            simple_opps = check_simple_arbitrage(markets)

            if simple_opps:
                print(f"\n  *** FOUND {len(simple_opps)} OPPORTUNITIES ***")
                for opp in simple_opps:
                    q = opp['market']['question'][:50]
                    print(f"    {opp['type'].upper()}: {q}...")
                    print(f"      Prices sum: {opp['price_sum']:.4f}")
                    print(f"      Profit: {opp['profit_pct']:.2f}%")
                    trader.opportunities_found += 1
                    all_opportunities.append(opp)
                    # Record to status
                    status_mgr.add_opportunity(opp)
            else:
                print("  No simple arbitrage found (markets efficiently priced)")

            # Check IP solver
            print("\n  Running IP solver analysis...")
            ip_result = check_ip_arbitrage(markets)
            print(f"    Min cost: ${ip_result['min_cost']:.4f}")
            print(f"    Arbitrage: {ip_result['has_arbitrage']}")
            if ip_result['has_arbitrage']:
                print(f"    Potential profit: ${ip_result['profit']:.4f}")

            # Update balance in status
            status_mgr.update_balance(trader.balance)

            # Show market summary
            print("\n  Sample market prices:")
            for m in markets[:5]:
                q = m['question'][:40]
                p = m['prices']
                s = sum(p)
                print(f"    {q}... | Sum: {s:.4f}")

            # Wait before next scan
            remaining = (end_time - datetime.utcnow()).total_seconds()
            if remaining > 30:
                print(f"\n  Next scan in 30s... ({remaining/60:.1f} min remaining)")
                await asyncio.sleep(30)
            elif remaining > 0:
                await asyncio.sleep(remaining)

    except Exception as e:
        status_mgr.record_error(str(e))
        raise
    finally:
        status_mgr.stop()

    # Final summary
    summary = trader.get_summary()

    print("\n" + "=" * 60)
    print("PAPER TRADING SUMMARY")
    print("=" * 60)
    print(f"Duration: {duration_minutes} minutes")
    print(f"Scans performed: {scan_count}")
    print(f"Opportunities found: {summary['opportunities_found']}")
    print(f"Trades executed: {summary['num_trades']}")
    print(f"Initial balance: ${summary['initial_balance']:,.2f}")
    print(f"Final balance: ${summary['final_balance']:,.2f}")
    print(f"Total P&L: ${summary['total_profit']:,.2f} ({summary['profit_pct']:.2f}%)")
    print("=" * 60)

    if all_opportunities:
        print("\nOpportunities detected during test:")
        for i, opp in enumerate(all_opportunities[:10], 1):
            print(f"  {i}. {opp['type'].upper()} - {opp['market']['question'][:50]}...")
            print(f"     Profit potential: {opp['profit_pct']:.2f}%")

    print("\n✓ Paper trading test complete!")
    print("  No real money was used. This was a simulation only.")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Paper Trading Test")
    parser.add_argument(
        '--duration', type=int, default=5,
        help='Test duration in minutes (default: 5)'
    )
    args = parser.parse_args()

    asyncio.run(run_paper_trading(args.duration))


if __name__ == "__main__":
    main()
