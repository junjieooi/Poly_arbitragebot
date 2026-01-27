"""Find a tradeable market with reasonable prices"""
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
    """Parse a field that may be a JSON string or already a list"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []


print("=" * 70)
print("FINDING TRADEABLE MARKETS")
print("=" * 70)

# Get credentials
api_key = os.getenv("POLYMARKET_API_KEY")
api_secret = os.getenv("POLYMARKET_API_SECRET")
passphrase = os.getenv("POLYMARKET_PASSPHRASE")
private_key = os.getenv("POLYMARKET_PRIVATE_KEY")

pk = private_key
if pk and pk.startswith("0x"):
    pk = pk[2:]

creds = ApiCreds(
    api_key=api_key,
    api_secret=api_secret,
    api_passphrase=passphrase,
)

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=pk,
    creds=creds,
)
print("✓ CLOB client initialized")

# Fetch more markets sorted by volume
print("\n[1] Fetching markets (sorted by volume)...")
resp = requests.get(
    "https://gamma-api.polymarket.com/markets",
    params={
        "limit": 100,
        "active": True,
        "closed": False,
        "order": "volume24hr",  # Sort by 24h volume
        "ascending": False,
    },
    timeout=15
)

if resp.status_code != 200:
    print(f"  Error: {resp.status_code}")
    sys.exit(1)

markets = resp.json()
print(f"  Found {len(markets)} markets")

# Find markets with reasonable bid/ask prices
print("\n[2] Scanning for tradeable markets (price 15-85 cents)...")
tradeable = []

for i, market in enumerate(markets):
    q = market.get('question', '?')[:60]
    volume = market.get('volume24hr', 0)

    token_ids = parse_json_field(market.get('clobTokenIds', []))
    outcomes = parse_json_field(market.get('outcomes', []))

    if not token_ids:
        continue

    for j, token_id in enumerate(token_ids):
        if not token_id:
            continue

        outcome = outcomes[j] if j < len(outcomes) else f"Outcome {j}"

        try:
            book = client.get_order_book(token_id)

            if book.asks:
                ask_price = float(book.asks[0].price)
            else:
                ask_price = None

            if book.bids:
                bid_price = float(book.bids[0].price)
            else:
                bid_price = None

            # Check if this is tradeable (reasonable ask price)
            if ask_price and 0.15 <= ask_price <= 0.85:
                spread = (ask_price - bid_price) if bid_price else None
                tradeable.append({
                    'market': market,
                    'question': q,
                    'outcome': outcome,
                    'token_id': token_id,
                    'ask': ask_price,
                    'bid': bid_price,
                    'spread': spread,
                    'volume': volume,
                    'num_asks': len(book.asks) if book.asks else 0,
                    'num_bids': len(book.bids) if book.bids else 0,
                })
                print(f"  ✓ Found: {outcome} @ ${ask_price:.2f} - {q[:40]}...")

        except Exception as e:
            pass

print(f"\n[3] Results: Found {len(tradeable)} tradeable outcomes")

if tradeable:
    # Sort by lowest spread
    tradeable.sort(key=lambda x: x['spread'] if x['spread'] else 999)

    print("\n" + "=" * 70)
    print("TOP 5 TRADEABLE MARKETS (lowest spread)")
    print("=" * 70)

    for i, t in enumerate(tradeable[:5], 1):
        print(f"\n{i}. {t['question']}...")
        print(f"   Outcome: {t['outcome']}")
        print(f"   Ask: ${t['ask']:.4f} ({t['num_asks']} offers)")
        print(f"   Bid: ${t['bid']:.4f} ({t['num_bids']} offers)" if t['bid'] else "   Bid: None")
        print(f"   Spread: ${t['spread']:.4f}" if t['spread'] else "   Spread: N/A")
        print(f"   Token: {t['token_id'][:40]}...")
        print(f"   24h Volume: ${t['volume']:,.0f}" if t['volume'] else "   24h Volume: N/A")

    # Suggest the best one
    best = tradeable[0]
    print("\n" + "=" * 70)
    print("RECOMMENDED TRADE")
    print("=" * 70)
    print(f"Market: {best['question']}")
    print(f"Outcome: {best['outcome']}")
    print(f"Buy price: ${best['ask']:.4f}")
    print(f"Token ID: {best['token_id']}")

else:
    print("\n✗ No markets with reasonable prices found")
    print("  All markets have extreme prices (>$0.85 or <$0.15)")
    print("  This is common when markets are highly skewed")
