"""Find active trading markets directly from CLOB API"""
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

# Try CLOB API directly for markets
print("=" * 70)
print("QUERYING CLOB API DIRECTLY")
print("=" * 70)

print("\n[1] Getting markets from CLOB API...")
try:
    clob_markets = client.get_markets()
    print(f"  Got {len(clob_markets)} markets from CLOB")

    # Show first few
    for i, m in enumerate(clob_markets[:5]):
        print(f"\n  Market {i+1}:")
        print(f"    {m}")
except Exception as e:
    print(f"  Error: {e}")

# Try sampling order books
print("\n[2] Sampling order books from CLOB API...")
try:
    sampling = client.get_sampling_markets()
    print(f"  Got {len(sampling) if sampling else 0} sampling markets")
except Exception as e:
    print(f"  Error: {e}")

# Get simplified markets
print("\n[3] Getting simplified markets...")
try:
    simple = client.get_sampling_simplified_markets()
    print(f"  Got {len(simple) if simple else 0} simplified markets")
    if simple:
        for i, s in enumerate(simple[:5]):
            print(f"    {i+1}. {s}")
except Exception as e:
    print(f"  Error: {e}")

# Try to get a specific well-known market
print("\n[4] Trying Gamma API with different params...")
try:
    # Try without ordering
    resp = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={
            "active": True,
            "closed": False,
            "limit": 20,
        },
        timeout=15
    )
    markets = resp.json()
    print(f"  Got {len(markets)} markets")

    # Check prices
    print("\n  Checking actual midpoint prices...")
    for m in markets[:10]:
        q = m.get('question', '?')[:45]
        # outcomePrices contains the actual prices
        outcome_prices = m.get('outcomePrices')
        if outcome_prices:
            prices = parse_json_field(outcome_prices)
            print(f"  {q}...")
            print(f"    Prices: {prices}")
except Exception as e:
    print(f"  Error: {e}")
    import traceback
    traceback.print_exc()

# Try events endpoint
print("\n[5] Trying events endpoint...")
try:
    resp = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={
            "active": True,
            "closed": False,
            "limit": 20,
        },
        timeout=15
    )
    events = resp.json()
    print(f"  Got {len(events)} events")

    for evt in events[:5]:
        title = evt.get('title', '?')[:50]
        print(f"\n  Event: {title}...")

        # Get markets in this event
        event_markets = evt.get('markets', [])
        for em in event_markets[:2]:
            q = em.get('question', '?')[:40]
            prices = parse_json_field(em.get('outcomePrices', []))
            outcomes = parse_json_field(em.get('outcomes', []))
            print(f"    Market: {q}...")
            if prices and outcomes:
                for oc, pr in zip(outcomes, prices):
                    print(f"      {oc}: {pr}")
except Exception as e:
    print(f"  Error: {e}")
    import traceback
    traceback.print_exc()

# Let's look at a specific popular market manually
print("\n[6] Looking for specific high-liquidity markets...")
try:
    resp = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={
            "active": True,
            "closed": False,
            "limit": 50,
            "_sort": "liquidity:desc"  # Try different sort param
        },
        timeout=15
    )
    markets = resp.json()

    for m in markets[:15]:
        q = m.get('question', '?')[:50]
        liquidity = m.get('liquidity', 0)
        volume = m.get('volume', 0)
        prices = parse_json_field(m.get('outcomePrices', []))
        outcomes = parse_json_field(m.get('outcomes', []))

        print(f"\n  {q}...")
        print(f"    Liquidity: ${liquidity:,.0f}, Volume: ${volume:,.0f}")
        if prices and outcomes:
            for oc, pr in zip(outcomes, prices):
                # Check if price is moderate
                try:
                    p = float(pr)
                    marker = " <-- TRADEABLE!" if 0.10 <= p <= 0.90 else ""
                    print(f"      {oc}: ${p:.4f}{marker}")
                except:
                    print(f"      {oc}: {pr}")

except Exception as e:
    print(f"  Error: {e}")
