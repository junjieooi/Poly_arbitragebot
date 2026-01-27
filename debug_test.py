"""Debug test to identify the issue"""
import os
import sys
import json
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()


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


print("=== TESTING COMPONENTS ===")

# Test 1: Check env vars
print("\n[1] Environment Variables:")
api_key = os.getenv("POLYMARKET_API_KEY")
api_secret = os.getenv("POLYMARKET_API_SECRET")
passphrase = os.getenv("POLYMARKET_PASSPHRASE")
private_key = os.getenv("POLYMARKET_PRIVATE_KEY")

print(f"  API_KEY: {'✓ Set' if api_key else '✗ Missing'} ({api_key[:20] if api_key else 'N/A'}...)")
print(f"  API_SECRET: {'✓ Set' if api_secret else '✗ Missing'}")
print(f"  PASSPHRASE: {'✓ Set' if passphrase else '✗ Missing'}")
print(f"  PRIVATE_KEY: {'✓ Set' if private_key else '✗ Missing'}")

# Test 2: Gamma API
print("\n[2] Gamma API Test:")
import requests
markets = []
try:
    resp = requests.get("https://gamma-api.polymarket.com/markets", params={"limit": 10, "active": True, "closed": False}, timeout=10)
    print(f"  Status: {resp.status_code}")
    if resp.status_code == 200:
        markets = resp.json()
        print(f"  Markets found: {len(markets)}")
        if markets:
            m = markets[0]
            print(f"  First market: {m.get('question', 'N/A')[:60]}...")

            # Parse the token IDs correctly
            raw_tokens = m.get('clobTokenIds', [])
            print(f"  Raw clobTokenIds type: {type(raw_tokens).__name__}")
            print(f"  Raw clobTokenIds: {str(raw_tokens)[:80]}...")

            token_ids = parse_json_field(raw_tokens)
            print(f"  Parsed token IDs: {len(token_ids)} tokens")
            for i, tid in enumerate(token_ids):
                print(f"    [{i}]: {tid[:40]}...")
except Exception as e:
    print(f"  Error: {e}")

# Test 3: CLOB Client
print("\n[3] CLOB Client Test:")
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

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
    print("  ✓ Client initialized")

    # Try to get order books for markets
    print("\n[4] Order Book Test:")
    found_market = False

    for i, market in enumerate(markets[:10]):
        q = market.get('question', '?')[:50]

        # Parse token IDs properly
        token_ids = parse_json_field(market.get('clobTokenIds', []))
        outcomes = parse_json_field(market.get('outcomes', []))

        if not token_ids:
            continue

        print(f"\n  Market {i+1}: {q}...")
        print(f"    Tokens: {len(token_ids)}, Outcomes: {len(outcomes)}")

        for j, token_id in enumerate(token_ids[:2]):  # Only check first 2
            if not token_id:
                continue
            outcome = outcomes[j] if j < len(outcomes) else f"Outcome {j}"
            print(f"    {outcome}:")
            print(f"      Token: {token_id[:40]}...")
            try:
                book = client.get_order_book(token_id)
                bids = len(book.bids) if book.bids else 0
                asks = len(book.asks) if book.asks else 0
                print(f"      Bids: {bids}, Asks: {asks}")
                if book.asks:
                    price = float(book.asks[0].price)
                    print(f"      Best ask: ${price:.4f}")
                    if 0.15 <= price <= 0.85:
                        print(f"      *** SUITABLE FOR TRADING! ***")
                        found_market = True
                if book.bids:
                    print(f"      Best bid: ${float(book.bids[0].price):.4f}")
            except Exception as e:
                print(f"      Error: {e}")

        if found_market:
            break

    if not found_market:
        print("\n  ✗ No markets with suitable order books found")

except Exception as e:
    print(f"  Error: {e}")
    import traceback
    traceback.print_exc()

print("\n=== DONE ===")
