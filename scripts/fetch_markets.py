#!/usr/bin/env python3
"""
Fetch active markets from Polymarket CLOB API.

Usage:
    python scripts/fetch_markets.py [--limit N] [--output FILE]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import aiohttp


async def fetch_markets(limit: int = 20, min_liquidity: float = 100) -> list:
    """Fetch active markets from Polymarket CLOB API."""
    # Use the Gamma API for better filtering
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": min(limit * 3, 100),  # Fetch more to filter
        "order": "liquidityNum",
        "ascending": "false"
    }

    markets = []

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=30) as resp:
                if resp.status != 200:
                    print(f"API returned status {resp.status}")
                    return []

                data = await resp.json()

                # Handle both CLOB API and Gamma API response formats
                market_list = data if isinstance(data, list) else data.get('data', [])

                for market in market_list:
                    # Skip markets with low liquidity
                    liquidity = float(market.get('liquidityNum', market.get('liquidity', 0)) or 0)
                    if liquidity < min_liquidity:
                        continue

                    # Skip if not accepting orders
                    if not market.get('acceptingOrders', True):
                        continue

                    # Parse token IDs and prices from Gamma API format
                    try:
                        clob_token_ids = json.loads(market.get('clobTokenIds', '[]'))
                        outcome_prices = json.loads(market.get('outcomePrices', '[]'))
                        outcomes = json.loads(market.get('outcomes', '[]'))
                    except (json.JSONDecodeError, TypeError):
                        continue

                    if len(clob_token_ids) < 2 or len(outcome_prices) < 2:
                        continue

                    # Match outcomes to tokens
                    yes_idx = 0
                    no_idx = 1
                    if len(outcomes) >= 2:
                        for i, outcome in enumerate(outcomes):
                            if outcome.lower() == 'yes':
                                yes_idx = i
                            elif outcome.lower() == 'no':
                                no_idx = i

                    yes_token = clob_token_ids[yes_idx]
                    no_token = clob_token_ids[no_idx]
                    yes_price = float(outcome_prices[yes_idx])
                    no_price = float(outcome_prices[no_idx])

                    # Skip if no prices
                    if yes_price == 0 and no_price == 0:
                        continue

                    markets.append({
                        'market_id': market.get('conditionId', market.get('condition_id', '')),
                        'question': market.get('question', ''),
                        'yes_token': yes_token,
                        'no_token': no_token,
                        'yes_price': yes_price,
                        'no_price': no_price,
                        'volume': float(market.get('volumeNum', market.get('volume', 0)) or 0),
                        'liquidity': liquidity,
                        'best_bid': market.get('bestBid'),
                        'best_ask': market.get('bestAsk')
                    })

                    if len(markets) >= limit:
                        break

        except Exception as e:
            print(f"Error fetching markets: {e}")

    return markets


def main():
    parser = argparse.ArgumentParser(description="Fetch active Polymarket markets")
    parser.add_argument('--limit', type=int, default=20, help='Number of markets to fetch')
    parser.add_argument('--output', type=str, help='Output file (YAML or JSON)')
    parser.add_argument('--yaml', action='store_true', help='Output as YAML for config')

    args = parser.parse_args()

    markets = asyncio.run(fetch_markets(args.limit))

    if not markets:
        print("No markets found")
        return 1

    print(f"\nFetched {len(markets)} markets:\n")

    # Display market summaries
    for i, m in enumerate(markets, 1):
        question = m['question'][:60] + '...' if len(m['question']) > 60 else m['question']
        price_sum = float(m['yes_price']) + float(m['no_price'])
        deviation = abs(price_sum - 1.0)

        print(f"{i:2}. {question}")
        print(f"    YES: ${m['yes_price']:.4f}  NO: ${m['no_price']:.4f}  Sum: ${price_sum:.4f}", end='')

        if deviation > 0.02:
            print(f"  ** POTENTIAL ARB: {deviation:.4f} **")
        else:
            print()

        print(f"    Volume: ${float(m['volume']):,.0f}  Liquidity: ${float(m['liquidity']):,.0f}")
        print()

    # Output to file if requested
    if args.output:
        output_path = Path(args.output)

        if args.yaml or output_path.suffix in ('.yaml', '.yml'):
            import yaml
            config = {'markets': [
                {
                    'market_id': m['market_id'],
                    'question': m['question'],
                    'yes_token': m['yes_token'],
                    'no_token': m['no_token']
                }
                for m in markets
            ]}
            with open(output_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            print(f"\nSaved YAML config to {output_path}")
        else:
            with open(output_path, 'w') as f:
                json.dump(markets, f, indent=2)
            print(f"\nSaved JSON to {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
