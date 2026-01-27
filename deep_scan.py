"""Deep scan for any tradeable liquidity"""
import os
import sys
import json
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
print("DEEP SCAN FOR TRADEABLE MARKETS")
print("=" * 70)

# Get all events
print("\n[1] Fetching all events...")
all_markets = []
offset = 0
while True:
    resp = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={"active": True, "closed": False, "limit": 100, "offset": offset},
        timeout=15
    )
    events = resp.json()
    if not events:
        break

    for event in events:
        markets = event.get('markets', [])
        for m in markets:
            m['event_title'] = event.get('title', '?')
        all_markets.extend(markets)

    if len(events) < 100:
        break
    offset += 100
    print(f"  Fetched {len(all_markets)} markets...")

print(f"  Total markets: {len(all_markets)}")

# Scan all order books
print("\n[2] Scanning order books for reasonable asks (<$0.95)...")
tradeable = []
scanned = 0
errors = 0

for market in all_markets:
    token_ids = parse_json_field(market.get('clobTokenIds', []))
    outcomes = parse_json_field(market.get('outcomes', []))
    q = market.get('question', '?')[:50]
    evt = market.get('event_title', '?')[:30]

    for i, token_id in enumerate(token_ids):
        if not token_id:
            continue

        try:
            book = client.get_order_book(token_id)
            scanned += 1

            if book.asks:
                best_ask = float(book.asks[0].price)
                best_bid = float(book.bids[0].price) if book.bids else 0
                num_asks = len(book.asks)
                num_bids = len(book.bids) if book.bids else 0

                # Look for ANY ask below $0.95
                if best_ask < 0.95:
                    outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
                    tradeable.append({
                        'question': q,
                        'event': evt,
                        'outcome': outcome,
                        'ask': best_ask,
                        'bid': best_bid,
                        'spread': best_ask - best_bid,
                        'num_asks': num_asks,
                        'num_bids': num_bids,
                        'token_id': token_id,
                        'market': market
                    })
                    print(f"  ✓ Found: {outcome} @ ${best_ask:.4f} - {q[:30]}...")

        except Exception as e:
            if "404" not in str(e):
                errors += 1

    if scanned % 50 == 0 and scanned > 0:
        print(f"  ...scanned {scanned} order books, found {len(tradeable)} tradeable")

print(f"\n  Scanned {scanned} order books, {errors} errors")
print(f"  Found {len(tradeable)} tradeable markets")

if tradeable:
    # Sort by price
    tradeable.sort(key=lambda x: x['ask'])

    print("\n" + "=" * 70)
    print("ALL TRADEABLE MARKETS (sorted by ask price)")
    print("=" * 70)

    for i, t in enumerate(tradeable[:20], 1):
        print(f"\n{i}. {t['outcome']}: Ask ${t['ask']:.4f}, Bid ${t['bid']:.4f}")
        print(f"   {t['question']}...")
        print(f"   Spread: ${t['spread']:.4f}, Offers: {t['num_asks']} asks, {t['num_bids']} bids")

    # Find best opportunity (tight spread + moderate price)
    best = min(tradeable, key=lambda x: x['spread'] + abs(x['ask'] - 0.5) * 0.1)

    print("\n" + "=" * 70)
    print("BEST TRADE OPPORTUNITY")
    print("=" * 70)
    print(f"Question: {best['question']}")
    print(f"Outcome: {best['outcome']}")
    print(f"Ask: ${best['ask']:.4f}")
    print(f"Bid: ${best['bid']:.4f}")
    print(f"Spread: ${best['spread']:.4f}")
    print(f"Token: {best['token_id']}")

    shares = round(5.0 / best['ask'], 2)
    cost = shares * best['ask']
    print(f"\nFor $5 trade:")
    print(f"  Shares: {shares}")
    print(f"  Cost: ${cost:.2f}")

else:
    print("\n✗ NO TRADEABLE MARKETS FOUND")
    print("  All order books have asks >= $0.95")
    print("  This means no one is willing to sell at fair prices")
    print("\n  Options:")
    print("  1. Place a limit order and wait for someone to fill it")
    print("  2. Buy at $0.99 (very expensive)")
    print("  3. Try at a different time when more liquidity is available")
