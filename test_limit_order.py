"""
Test Limit Order on Polymarket
==============================

Since order books have thin liquidity (all asks at $0.99),
this script places a LIMIT order at a fair price and waits for fills.

Usage: python3 test_limit_order.py
"""
import os
import sys
import json
import time
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY


def parse_json_field(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return []


# Configuration
TEST_AMOUNT = 5.0  # $5 test trade
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137

print("=" * 60)
print("POLYMARKET LIMIT ORDER TEST")
print("=" * 60)

# Get credentials
api_key = os.getenv("POLYMARKET_API_KEY")
api_secret = os.getenv("POLYMARKET_API_SECRET")
passphrase = os.getenv("POLYMARKET_PASSPHRASE")
private_key = os.getenv("POLYMARKET_PRIVATE_KEY")

pk = private_key
if pk and pk.startswith("0x"):
    pk = pk[2:]

creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase)
client = ClobClient(host=CLOB_HOST, chain_id=CHAIN_ID, key=pk, creds=creds)
print("✓ Connected to CLOB API")

# Find a market with moderate price
print("\n[1] Finding market with moderate price (30-70c)...")

resp = requests.get(
    f"{GAMMA_API}/events",
    params={"active": True, "closed": False, "limit": 50},
    timeout=15
)
events = resp.json()

candidates = []
for event in events:
    for market in event.get('markets', []):
        prices = parse_json_field(market.get('outcomePrices', []))
        outcomes = parse_json_field(market.get('outcomes', []))
        token_ids = parse_json_field(market.get('clobTokenIds', []))
        q = market.get('question', '?')[:60]
        liq = market.get('liquidity', 0)
        try:
            liq = float(liq) if liq else 0
        except:
            liq = 0

        for i, (outcome, price_str, token_id) in enumerate(zip(outcomes, prices, token_ids)):
            try:
                price = float(price_str)
                if 0.30 <= price <= 0.70:  # Moderate price = uncertain outcome
                    candidates.append({
                        'question': q,
                        'outcome': outcome,
                        'price': price,
                        'token_id': token_id,
                        'liquidity': liq,
                        'market': market
                    })
            except ValueError:
                pass

# Sort by uncertainty (closest to 50c)
candidates.sort(key=lambda x: abs(x['price'] - 0.5))

if not candidates:
    print("  ✗ No markets found with prices between 30-70 cents")
    sys.exit(1)

print(f"  Found {len(candidates)} candidate markets")
print("\n  Top candidates (most uncertain):")
for i, c in enumerate(candidates[:5], 1):
    print(f"  {i}. {c['outcome']}: ${c['price']:.4f} - {c['question'][:45]}...")

# Select the best candidate
selected = candidates[0]
print(f"\n[2] Selected market:")
print(f"    Question: {selected['question']}")
print(f"    Outcome: {selected['outcome']}")
print(f"    Market Price: ${selected['price']:.4f}")
print(f"    Token ID: {selected['token_id'][:50]}...")

# Check current order book
print("\n[3] Checking order book...")
try:
    book = client.get_order_book(selected['token_id'])
    best_ask = float(book.asks[0].price) if book.asks else None
    best_bid = float(book.bids[0].price) if book.bids else None
    num_asks = len(book.asks) if book.asks else 0
    num_bids = len(book.bids) if book.bids else 0

    print(f"    Best Ask: ${best_ask:.4f} ({num_asks} offers)" if best_ask else "    No asks")
    print(f"    Best Bid: ${best_bid:.4f} ({num_bids} offers)" if best_bid else "    No bids")
    if best_ask and best_bid:
        print(f"    Spread: ${best_ask - best_bid:.4f}")
except Exception as e:
    print(f"    Error: {e}")
    best_ask = None
    best_bid = None

# Determine limit price
# Place order slightly above current best bid but at fair market price
fair_price = selected['price']
if best_bid:
    # Offer slightly better than best bid
    limit_price = round(min(fair_price + 0.01, best_bid + 0.01), 4)
else:
    limit_price = round(fair_price, 4)

# Ensure price is valid (between 0.01 and 0.99)
limit_price = max(0.01, min(0.99, limit_price))

# Calculate shares
shares = round(TEST_AMOUNT / limit_price, 2)
actual_cost = round(shares * limit_price, 2)

print(f"\n[4] Limit Order Details:")
print(f"    Side: BUY")
print(f"    Price: ${limit_price:.4f} per share")
print(f"    Shares: {shares}")
print(f"    Max Cost: ${actual_cost:.2f}")
print(f"    Order Type: GTC (Good Till Cancelled)")

if best_ask:
    if limit_price >= best_ask:
        print(f"\n    ⚠️  Your limit price (${limit_price:.4f}) >= best ask (${best_ask:.4f})")
        print(f"    Order will fill immediately at best ask price")
    else:
        print(f"\n    ℹ️  Your limit (${limit_price:.4f}) < best ask (${best_ask:.4f})")
        print(f"    Order will wait in the book until someone sells at your price")

# Confirm
print("\n" + "-" * 60)
confirm = input("Place this limit order? (yes/no): ").strip().lower()

if confirm != 'yes':
    print("\nOrder cancelled.")
    sys.exit(0)

# Execute
print("\n[5] Placing order...")
try:
    order_args = OrderArgs(
        price=limit_price,
        size=shares,
        side=BUY,
        token_id=selected['token_id'],
    )

    print("    Creating and signing order...")
    signed_order = client.create_order(order_args)
    print("    ✓ Order created")

    print("    Submitting to exchange...")
    result = client.post_order(signed_order, OrderType.GTC)

    print("\n" + "=" * 60)
    print("✓ LIMIT ORDER PLACED!")
    print("=" * 60)
    order_id = result.get('orderID', str(result))
    print(f"  Order ID: {order_id}")
    print(f"  Market: {selected['question'][:50]}...")
    print(f"  Outcome: {selected['outcome']}")
    print(f"  BUY {shares} shares @ ${limit_price:.4f}")
    print(f"\n  Status: Order is now in the order book")
    print(f"  It will fill when someone sells at ${limit_price:.4f} or lower")

    # Check if we can view open orders
    print("\n[6] Checking open orders...")
    try:
        open_orders = client.get_orders()
        if open_orders:
            print(f"  You have {len(open_orders)} open order(s)")
            for o in open_orders[:5]:
                print(f"    - {o}")
    except Exception as e:
        print(f"  Could not check open orders: {e}")

except Exception as e:
    print(f"\n  ✗ Order failed: {e}")
    import traceback
    traceback.print_exc()
