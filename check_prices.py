"""Check what prices are available across markets"""
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

print("Fetching markets...")
resp = requests.get(
    "https://gamma-api.polymarket.com/markets",
    params={"limit": 100, "active": True, "closed": False},
    timeout=15
)
markets = resp.json()
print(f"Found {len(markets)} markets\n")

# Collect all prices
prices = []
price_ranges = {
    '0-10c': 0,
    '10-20c': 0,
    '20-30c': 0,
    '30-40c': 0,
    '40-50c': 0,
    '50-60c': 0,
    '60-70c': 0,
    '70-80c': 0,
    '80-90c': 0,
    '90-100c': 0,
}

print("Scanning prices...")
count = 0
for market in markets:
    token_ids = parse_json_field(market.get('clobTokenIds', []))
    outcomes = parse_json_field(market.get('outcomes', []))
    q = market.get('question', '?')[:50]

    for j, token_id in enumerate(token_ids[:2]):
        if not token_id:
            continue
        try:
            book = client.get_order_book(token_id)
            outcome = outcomes[j] if j < len(outcomes) else f"O{j}"

            if book.asks:
                ask = float(book.asks[0].price)
                prices.append({
                    'price': ask,
                    'outcome': outcome,
                    'question': q,
                    'token_id': token_id,
                    'market': market
                })

                # Categorize
                if ask < 0.10:
                    price_ranges['0-10c'] += 1
                elif ask < 0.20:
                    price_ranges['10-20c'] += 1
                elif ask < 0.30:
                    price_ranges['20-30c'] += 1
                elif ask < 0.40:
                    price_ranges['30-40c'] += 1
                elif ask < 0.50:
                    price_ranges['40-50c'] += 1
                elif ask < 0.60:
                    price_ranges['50-60c'] += 1
                elif ask < 0.70:
                    price_ranges['60-70c'] += 1
                elif ask < 0.80:
                    price_ranges['70-80c'] += 1
                elif ask < 0.90:
                    price_ranges['80-90c'] += 1
                else:
                    price_ranges['90-100c'] += 1

                count += 1

        except:
            pass

print(f"\nScanned {count} outcomes\n")
print("=" * 50)
print("PRICE DISTRIBUTION")
print("=" * 50)
for range_name, cnt in price_ranges.items():
    bar = '█' * (cnt * 2)
    print(f"{range_name:10s}: {cnt:3d} {bar}")

# Show cheapest and most moderate prices
prices.sort(key=lambda x: x['price'])

print("\n" + "=" * 50)
print("CHEAPEST OUTCOMES (best value buys)")
print("=" * 50)
for p in prices[:10]:
    print(f"${p['price']:.4f} - {p['outcome']:5s} - {p['question'][:35]}...")

# Find the most "moderate" prices (closest to 50c)
prices.sort(key=lambda x: abs(x['price'] - 0.5))

print("\n" + "=" * 50)
print("MOST UNCERTAIN (prices near 50c)")
print("=" * 50)
for p in prices[:10]:
    print(f"${p['price']:.4f} - {p['outcome']:5s} - {p['question'][:35]}...")

# Find best trading opportunity - has to have both bid and ask
print("\n" + "=" * 50)
print("CHECKING FOR BEST SPREAD OPPORTUNITIES")
print("=" * 50)

best_opportunities = []
for p in prices:
    try:
        book = client.get_order_book(p['token_id'])
        if book.asks and book.bids:
            ask = float(book.asks[0].price)
            bid = float(book.bids[0].price)
            spread = ask - bid
            mid = (ask + bid) / 2
            if spread < 0.10 and 0.05 < ask < 0.95:  # Reasonable spread
                best_opportunities.append({
                    'ask': ask,
                    'bid': bid,
                    'spread': spread,
                    'mid': mid,
                    'outcome': p['outcome'],
                    'question': p['question'],
                    'token_id': p['token_id'],
                    'market': p['market']
                })
    except:
        pass

best_opportunities.sort(key=lambda x: x['spread'])

if best_opportunities:
    print(f"Found {len(best_opportunities)} tight-spread opportunities\n")
    for opp in best_opportunities[:10]:
        print(f"Spread: ${opp['spread']:.4f} | Bid: ${opp['bid']:.4f} Ask: ${opp['ask']:.4f}")
        print(f"  {opp['outcome']} - {opp['question'][:45]}...")
        print()
else:
    print("No tight spread opportunities found")
