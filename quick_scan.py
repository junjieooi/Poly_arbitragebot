"""Quick scan for tradeable markets with rate limiting"""
import os
import sys
import json
import time
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds


def parse_json_field(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []


api_key = os.getenv("POLYMARKET_API_KEY")
api_secret = os.getenv("POLYMARKET_API_SECRET")
passphrase = os.getenv("POLYMARKET_PASSPHRASE")
private_key = os.getenv("POLYMARKET_PRIVATE_KEY")

pk = private_key
if pk and pk.startswith("0x"):
    pk = pk[2:]

creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase)
client = ClobClient(host="https://clob.polymarket.com", chain_id=137, key=pk, creds=creds)

print("=" * 70)
print("QUICK SCAN FOR TRADEABLE MARKETS")
print("=" * 70)

# Get markets sorted by liquidity/volume
print("\n[1] Fetching top markets by liquidity...")
resp = requests.get(
    "https://gamma-api.polymarket.com/events",
    params={"active": True, "closed": False, "limit": 50},
    timeout=15
)
events = resp.json()

all_markets = []
for event in events:
    markets = event.get('markets', [])
    for m in markets:
        m['event_title'] = event.get('title', '?')
        # Parse liquidity
        liq = m.get('liquidity', 0)
        try:
            liq = float(liq) if liq else 0
        except:
            liq = 0
        m['liq_float'] = liq
    all_markets.extend(markets)

# Sort by liquidity
all_markets.sort(key=lambda x: x['liq_float'], reverse=True)
print(f"  Found {len(all_markets)} markets")

# Scan top markets by liquidity
print("\n[2] Checking order books (top 100 by liquidity)...")
tradeable = []
ask_distribution = {'>95c': 0, '90-95c': 0, '80-90c': 0, '50-80c': 0, '<50c': 0}

for i, market in enumerate(all_markets[:100]):
    token_ids = parse_json_field(market.get('clobTokenIds', []))
    outcomes = parse_json_field(market.get('outcomes', []))
    q = market.get('question', '?')[:50]
    liq = market.get('liq_float', 0)

    for j, token_id in enumerate(token_ids[:2]):
        if not token_id:
            continue

        try:
            book = client.get_order_book(token_id)

            if book.asks:
                best_ask = float(book.asks[0].price)
                best_bid = float(book.bids[0].price) if book.bids else 0
                num_asks = len(book.asks)

                # Categorize
                if best_ask > 0.95:
                    ask_distribution['>95c'] += 1
                elif best_ask > 0.90:
                    ask_distribution['90-95c'] += 1
                elif best_ask > 0.80:
                    ask_distribution['80-90c'] += 1
                elif best_ask > 0.50:
                    ask_distribution['50-80c'] += 1
                else:
                    ask_distribution['<50c'] += 1

                # Track tradeable ones
                if best_ask < 0.95:
                    outcome = outcomes[j] if j < len(outcomes) else f"Outcome {j}"
                    tradeable.append({
                        'question': q,
                        'outcome': outcome,
                        'ask': best_ask,
                        'bid': best_bid,
                        'spread': best_ask - best_bid,
                        'num_asks': num_asks,
                        'token_id': token_id,
                        'liquidity': liq
                    })
                    print(f"  ✓ {outcome} @ ${best_ask:.2f} - {q[:35]}...")

            time.sleep(0.1)  # Rate limit

        except Exception as e:
            if "404" not in str(e):
                pass

    if (i + 1) % 20 == 0:
        print(f"  ...checked {i+1} markets")

print(f"\n[3] Ask Price Distribution:")
for range_name, count in ask_distribution.items():
    bar = '█' * count
    print(f"  {range_name:8s}: {count:3d} {bar}")

if tradeable:
    tradeable.sort(key=lambda x: x['ask'])

    print(f"\n[4] Found {len(tradeable)} tradeable markets!")
    print("\n" + "=" * 70)
    print("TRADEABLE MARKETS")
    print("=" * 70)

    for i, t in enumerate(tradeable[:10], 1):
        print(f"\n{i}. {t['outcome']}: ${t['ask']:.4f}")
        print(f"   {t['question']}...")
        print(f"   Bid: ${t['bid']:.4f}, Spread: ${t['spread']:.4f}")
        print(f"   Token: {t['token_id'][:50]}...")

    best = tradeable[0]
    print("\n" + "=" * 70)
    print("EXECUTE THIS TRADE")
    print("=" * 70)
    print(f"Market: {best['question']}")
    print(f"Outcome: {best['outcome']}")
    print(f"Price: ${best['ask']:.4f}")
    print(f"Token ID: {best['token_id']}")

    shares = round(5.0 / best['ask'], 2)
    print(f"\nBuy {shares} shares at ${best['ask']:.4f} = ${shares * best['ask']:.2f}")

else:
    print("\n[4] NO TRADEABLE MARKETS FOUND")
    print("  All asks are >= $0.95")
    print("\n  This means Polymarket's order books are currently very thin.")
    print("  The displayed 'price' is based on AMM/last trade, not actual asks.")
    print("\n  Options:")
    print("  1. Place a LIMIT order at desired price and wait")
    print("  2. Accept the $0.99 ask (expensive but immediate)")
    print("  3. Wait for better liquidity")
