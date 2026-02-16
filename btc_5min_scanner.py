#!/usr/bin/env python3
"""
BTC 5-Minute Market Scanner

Focused scanner for BTC up/down 5-minute prediction markets.
Watches for arbitrage when YES + NO < 1.00 (or > 1.00).

These markets move fast, so opportunities may appear briefly.

Usage:
    python btc_5min_scanner.py [--threshold 0.98] [--alert]
"""

import asyncio
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import aiohttp

# Setup
sys.path.insert(0, str(Path(__file__).parent))
from src.monitor import StatusManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BTCScanner:
    """Scanner focused on BTC 5-minute markets."""

    # Polymarket API
    GAMMA_API = "https://gamma-api.polymarket.com/markets"
    CLOB_API = "https://clob.polymarket.com"

    def __init__(
        self,
        buy_threshold: float = 0.98,   # Buy if YES+NO < this
        sell_threshold: float = 1.02,  # Sell if YES+NO > this
        scan_interval: float = 2.0,    # Scan every N seconds
        alert_sound: bool = False
    ):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.scan_interval = scan_interval
        self.alert_sound = alert_sound

        # Stats
        self.scans = 0
        self.opportunities_found = 0
        self.last_opportunity = None
        self.start_time = None

        # Status manager
        self.status_mgr = StatusManager()

    async def fetch_btc_markets(self) -> list:
        """Fetch BTC-related prediction markets."""
        async with aiohttp.ClientSession() as session:
            try:
                # Search for BTC/Bitcoin markets
                params = {
                    "active": "true",
                    "closed": "false",
                    "limit": 100,
                }

                async with session.get(self.GAMMA_API, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return []

                    data = await resp.json()
                    btc_markets = []

                    for market in data:
                        question = market.get('question', '').lower()

                        # Filter for BTC/Bitcoin 5-minute or short-term markets
                        is_btc = any(term in question for term in [
                            'btc', 'bitcoin', 'crypto'
                        ])
                        is_short_term = any(term in question for term in [
                            '5 min', '5min', '1 min', '1min', '15 min', '15min',
                            'minute', 'hour', 'today', 'next'
                        ])

                        # Also check for price prediction markets
                        is_price = any(term in question for term in [
                            'price', 'above', 'below', 'up', 'down', 'higher', 'lower'
                        ])

                        if is_btc or (is_price and ('btc' in question or 'bitcoin' in question)):
                            try:
                                prices = json.loads(market.get('outcomePrices', '[]'))
                                outcomes = json.loads(market.get('outcomes', '[]'))
                                token_ids = json.loads(market.get('clobTokenIds', '[]'))

                                if len(prices) >= 2:
                                    yes_price = float(prices[0])
                                    no_price = float(prices[1])
                                    price_sum = yes_price + no_price

                                    btc_markets.append({
                                        'market_id': market.get('conditionId', ''),
                                        'question': market.get('question', ''),
                                        'yes_price': yes_price,
                                        'no_price': no_price,
                                        'price_sum': price_sum,
                                        'outcomes': outcomes,
                                        'token_ids': token_ids,
                                        'liquidity': float(market.get('liquidityNum', 0) or 0),
                                        'volume': float(market.get('volumeNum', 0) or 0),
                                        'end_date': market.get('endDate', ''),
                                        'best_bid': market.get('bestBid'),
                                        'best_ask': market.get('bestAsk'),
                                    })
                            except:
                                continue

                    return btc_markets

            except Exception as e:
                logger.error(f"Error fetching markets: {e}")
                return []

    async def fetch_orderbook(self, token_id: str) -> dict:
        """Fetch order book for a specific token."""
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.CLOB_API}/book"
                params = {"token_id": token_id}

                async with session.get(url, params=params, timeout=5) as resp:
                    if resp.status == 200:
                        return await resp.json()
            except:
                pass
            return {}

    def check_arbitrage(self, market: dict) -> dict | None:
        """Check if market has arbitrage opportunity."""
        price_sum = market['price_sum']

        # Buy arbitrage: YES + NO < threshold
        if price_sum < self.buy_threshold:
            profit = 1.0 - price_sum
            return {
                'type': 'BUY',
                'market': market,
                'price_sum': price_sum,
                'profit': profit,
                'profit_pct': profit * 100,
                'action': f"Buy YES @ ${market['yes_price']:.4f} + NO @ ${market['no_price']:.4f}"
            }

        # Sell arbitrage: YES + NO > threshold
        if price_sum > self.sell_threshold:
            profit = price_sum - 1.0
            return {
                'type': 'SELL',
                'market': market,
                'price_sum': price_sum,
                'profit': profit,
                'profit_pct': profit * 100,
                'action': f"Sell YES @ ${market['yes_price']:.4f} + NO @ ${market['no_price']:.4f}"
            }

        return None

    def alert(self, opportunity: dict):
        """Alert when opportunity found."""
        print("\n" + "🚨" * 20)
        print("🚨 ARBITRAGE OPPORTUNITY FOUND! 🚨")
        print("🚨" * 20)
        print(f"\nType: {opportunity['type']}")
        print(f"Market: {opportunity['market']['question']}")
        print(f"YES: ${opportunity['market']['yes_price']:.4f}")
        print(f"NO: ${opportunity['market']['no_price']:.4f}")
        print(f"SUM: ${opportunity['price_sum']:.4f}")
        print(f"PROFIT: {opportunity['profit_pct']:.2f}%")
        print(f"Action: {opportunity['action']}")
        print(f"Liquidity: ${opportunity['market']['liquidity']:,.0f}")
        print("\n" + "🚨" * 20 + "\n")

        if self.alert_sound:
            # Terminal bell
            print('\a' * 3)

    async def scan_once(self) -> list:
        """Run a single scan."""
        self.scans += 1

        markets = await self.fetch_btc_markets()
        opportunities = []

        for market in markets:
            opp = self.check_arbitrage(market)
            if opp:
                opportunities.append(opp)
                self.opportunities_found += 1
                self.last_opportunity = opp
                self.alert(opp)

                # Record to status
                self.status_mgr.add_opportunity({
                    'type': opp['type'],
                    'market': {'question': opp['market']['question']},
                    'price_sum': opp['price_sum'],
                    'profit_pct': opp['profit_pct']
                })

        return markets, opportunities

    async def run(self, duration_minutes: int = None):
        """Run the scanner."""
        self.start_time = datetime.utcnow()
        self.status_mgr.start(mode="btc_scanner", initial_balance=0)

        print("\n" + "=" * 60)
        print("🔍 BTC 5-MINUTE MARKET SCANNER")
        print("=" * 60)
        print(f"Buy threshold:  YES + NO < ${self.buy_threshold}")
        print(f"Sell threshold: YES + NO > ${self.sell_threshold}")
        print(f"Scan interval:  {self.scan_interval}s")
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        print("\nWatching for BTC arbitrage opportunities...\n")

        scan_count = 0
        try:
            while True:
                scan_count += 1
                markets, opportunities = await self.scan_once()

                # Update status
                self.status_mgr.update_scan(len(markets))

                # Print status line
                timestamp = datetime.now().strftime('%H:%M:%S')
                if markets:
                    # Show closest to arbitrage
                    sorted_markets = sorted(markets, key=lambda m: abs(m['price_sum'] - 1.0), reverse=True)
                    closest = sorted_markets[0] if sorted_markets else None

                    if closest:
                        diff = closest['price_sum'] - 1.0
                        sign = "+" if diff > 0 else ""
                        q = closest['question'][:35]
                        print(
                            f"[{timestamp}] Scan #{scan_count} | "
                            f"{len(markets)} BTC markets | "
                            f"Best: {sign}{diff:.4f} | "
                            f"{q}..."
                        )
                    else:
                        print(f"[{timestamp}] Scan #{scan_count} | {len(markets)} BTC markets | All efficient")
                else:
                    print(f"[{timestamp}] Scan #{scan_count} | No BTC markets found")

                if opportunities:
                    print(f"    ⚡ {len(opportunities)} OPPORTUNITIES FOUND!")

                # Check duration
                if duration_minutes:
                    elapsed = (datetime.utcnow() - self.start_time).total_seconds() / 60
                    if elapsed >= duration_minutes:
                        break

                await asyncio.sleep(self.scan_interval)

        except KeyboardInterrupt:
            print("\n\nStopping scanner...")
        finally:
            self.status_mgr.stop()

        # Summary
        print("\n" + "=" * 60)
        print("SCANNER SUMMARY")
        print("=" * 60)
        print(f"Total scans: {self.scans}")
        print(f"Opportunities found: {self.opportunities_found}")
        if self.last_opportunity:
            print(f"Last opportunity: {self.last_opportunity['type']} - {self.last_opportunity['profit_pct']:.2f}%")
        print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="BTC 5-Minute Market Scanner")
    parser.add_argument('--buy-threshold', type=float, default=0.98,
                        help='Buy when YES+NO < this (default: 0.98)')
    parser.add_argument('--sell-threshold', type=float, default=1.02,
                        help='Sell when YES+NO > this (default: 1.02)')
    parser.add_argument('--interval', type=float, default=2.0,
                        help='Scan interval in seconds (default: 2)')
    parser.add_argument('--duration', type=int, default=None,
                        help='Run for N minutes (default: forever)')
    parser.add_argument('--alert', action='store_true',
                        help='Enable sound alerts')

    args = parser.parse_args()

    scanner = BTCScanner(
        buy_threshold=args.buy_threshold,
        sell_threshold=args.sell_threshold,
        scan_interval=args.interval,
        alert_sound=args.alert
    )

    await scanner.run(duration_minutes=args.duration)


if __name__ == "__main__":
    asyncio.run(main())
