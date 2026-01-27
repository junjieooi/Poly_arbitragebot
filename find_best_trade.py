"""Find best trade opportunity with moderate prices"""
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
print("FINDING BEST TRADE OPPORTUNITY")
print("=" * 70)

# Get events (contain markets)
print("\n[1] Fetching events...")
resp = requests.get(
    "https://gamma-api.polymarket.com/events",
    params={"active": True, "closed": False, "limit": 100},
    timeout=15
)
events = resp.json()
print(f"  Found {len(events)} events")

# Find markets with moderate prices
print("\n[2] Scanning for moderate prices (10-90 cents)...")
tradeable = []

for event in events:
    event_title = event.get('title', '?')[:40]
    markets = event.get('markets', [])

    for market in markets:
        q = market.get('question', '?')[:50]
        prices = parse_json_field(market.get('outcomePrices', []))
        outcomes = parse_json_field(market.get('outcomes', []))
        token_ids = parse_json_field(market.get('clobTokenIds', []))

        if not prices or not token_ids:
            continue

        for i, (outcome, price_str, token_id) in enumerate(zip(outcomes, prices, token_ids)):
            try:
                price = float(price_str)
                # Look for moderate prices (10-90 cents)
                if 0.10 <= price <= 0.90:
                    tradeable.append({
                        'event': event_title,
                        'question': q,
                        'outcome': outcome,
                        'price': price,
                        'token_id': token_id,
                        'market': market
                    })
            except ValueError:
                pass

print(f"  Found {len(tradeable)} tradeable outcomes (10-90c)")

# Sort by price closest to 50c (most uncertain = most interesting)
tradeable.sort(key=lambda x: abs(x['price'] - 0.5))

print("\n" + "=" * 70)
print("TRADEABLE OPPORTUNITIES (sorted by uncertainty)")
print("=" * 70)

for i, t in enumerate(tradeable[:15], 1):
    print(f"\n{i}. {t['outcome']}: ${t['price']:.4f}")
    print(f"   {t['question']}...")
    print(f"   Event: {t['event']}...")

# Verify order book liquidity for top candidates
print("\n" + "=" * 70)
print("VERIFYING ORDER BOOK LIQUIDITY")
print("=" * 70)

verified = []
for t in tradeable[:20]:
    try:
        book = client.get_order_book(t['token_id'])
        asks = len(book.asks) if book.asks else 0
        bids = len(book.bids) if book.bids else 0

        if book.asks and book.bids:
            best_ask = float(book.asks[0].price)
            best_bid = float(book.bids[0].price)
            spread = best_ask - best_bid

            # Check if we can actually buy at a reasonable price
            if best_ask <= 0.95:  # Can actually buy
                t['best_ask'] = best_ask
                t['best_bid'] = best_bid
                t['spread'] = spread
                t['num_asks'] = asks
                t['num_bids'] = bids
                verified.append(t)
                print(f"✓ {t['outcome']}: Ask ${best_ask:.4f}, Bid ${best_bid:.4f}, Spread ${spread:.4f}")
            else:
                print(f"✗ {t['outcome']}: Ask too high (${best_ask:.4f})")
        else:
            print(f"✗ {t['outcome']}: No liquidity (asks={asks}, bids={bids})")

    except Exception as e:
        print(f"✗ {t['outcome']}: Error - {e}")

if verified:
    print("\n" + "=" * 70)
    print("RECOMMENDED TRADES")
    print("=" * 70)

    # Sort by spread (tighter = better)
    verified.sort(key=lambda x: x['spread'])

    for i, v in enumerate(verified[:5], 1):
        print(f"\n{i}. {v['question'][:55]}...")
        print(f"   Outcome: {v['outcome']}")
        print(f"   Market Price: ${v['price']:.4f}")
        print(f"   Best Ask: ${v['best_ask']:.4f} ({v['num_asks']} offers)")
        print(f"   Best Bid: ${v['best_bid']:.4f} ({v['num_bids']} offers)")
        print(f"   Spread: ${v['spread']:.4f}")
        print(f"   Token ID: {v['token_id'][:50]}...")

    # Output best trade details for the test script
    best = verified[0]
    print("\n" + "=" * 70)
    print("BEST TRADE FOR $5 TEST")
    print("=" * 70)
    print(f"Question: {best['question']}")
    print(f"Outcome: {best['outcome']}")
    print(f"Buy at: ${best['best_ask']:.4f}")
    print(f"Token ID: {best['token_id']}")

    shares = round(5.0 / best['best_ask'], 2)
    cost = shares * best['best_ask']
    print(f"\nFor $5:")
    print(f"  Shares: {shares}")
    print(f"  Cost: ${cost:.2f}")

else:
    print("\n✗ No markets with verified liquidity found")
