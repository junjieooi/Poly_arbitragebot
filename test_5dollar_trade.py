"""
$5 Test Trade on Polymarket
============================

Uses Gamma API for market discovery and CLOB API for trading.

Usage: python3 test_5dollar_trade.py
"""

import os
import json
import requests
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY


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

load_dotenv()

# Configuration
TEST_AMOUNT = 5.0  # $5 test trade
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


def get_clob_client():
    """Initialize CLOB client with credentials"""
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if private_key and private_key.startswith("0x"):
        private_key = private_key[2:]

    api_creds = ApiCreds(
        api_key=os.getenv("POLYMARKET_API_KEY"),
        api_secret=os.getenv("POLYMARKET_API_SECRET"),
        api_passphrase=os.getenv("POLYMARKET_PASSPHRASE"),
    )

    return ClobClient(
        host=CLOB_HOST,
        chain_id=CHAIN_ID,
        key=private_key,
        creds=api_creds,
    )


def get_live_markets(limit=20):
    """Get live markets from Gamma API"""
    response = requests.get(
        f"{GAMMA_API}/markets",
        params={"limit": limit, "active": True, "closed": False}
    )
    if response.status_code == 200:
        return response.json()
    return []


def get_market_details(condition_id):
    """Get detailed market info including CLOB token IDs"""
    response = requests.get(f"{GAMMA_API}/markets/{condition_id}")
    if response.status_code == 200:
        return response.json()
    return None


def main():
    print("=" * 60)
    print("POLYMARKET $5 TEST TRADE")
    print("=" * 60)

    # Step 1: Get live markets from Gamma API
    print("\n[1] Fetching live markets from Gamma API...")
    markets = get_live_markets(50)
    print(f"    ✓ Found {len(markets)} live markets")

    if not markets:
        print("    ✗ No live markets found")
        return

    # Show top markets
    print("\n    Top markets:")
    for i, m in enumerate(markets[:5], 1):
        q = m.get('question', '?')[:55]
        print(f"    {i}. {q}...")

    # Step 2: Initialize CLOB client
    print("\n[2] Connecting to CLOB API...")
    try:
        client = get_clob_client()
        print("    ✓ Connected!")
    except Exception as e:
        print(f"    ✗ Failed: {e}")
        return

    # Step 3: Find a market with good order book
    print("\n[3] Finding market with order book...")

    selected_market = None
    selected_token_id = None
    selected_outcome = None
    selected_price = None

    for market in markets:
        # Get CLOB token IDs from market (may be JSON string or list)
        clob_token_ids = parse_json_field(market.get('clobTokenIds', []))
        outcomes = parse_json_field(market.get('outcomes', []))

        if not clob_token_ids:
            # Try to get from detailed endpoint
            details = get_market_details(market.get('conditionId', ''))
            if details:
                clob_token_ids = parse_json_field(details.get('clobTokenIds', []))
                outcomes = parse_json_field(details.get('outcomes', []))

        for i, token_id in enumerate(clob_token_ids):
            if not token_id:
                continue

            try:
                book = client.get_order_book(token_id)
                if book and book.asks and len(book.asks) > 0:
                    price = float(book.asks[0].price)
                    # Look for reasonable price (15-85 cents)
                    if 0.15 <= price <= 0.85:
                        selected_market = market
                        selected_token_id = token_id
                        selected_outcome = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
                        selected_price = price
                        break
            except Exception as e:
                continue

        if selected_market:
            break

    if not selected_market:
        print("    ✗ No market with suitable order book found")
        print("\n    Trying to show available order books...")

        # Debug: show what's available
        for m in markets[:5]:
            token_ids = parse_json_field(m.get('clobTokenIds', []))
            for tid in token_ids[:2]:
                if tid:
                    try:
                        book = client.get_order_book(tid)
                        print(f"    Token {tid[:20]}...: bids={len(book.bids) if book.bids else 0}, asks={len(book.asks) if book.asks else 0}")
                    except Exception as e:
                        print(f"    Token {tid[:20]}...: Error - {e}")
        return

    # Display selection
    question = selected_market.get('question', 'Unknown')
    print(f"    ✓ Found market:")
    print(f"      Question: {question[:65]}...")
    print(f"      Outcome: {selected_outcome}")
    print(f"      Price: ${selected_price:.2f}")

    # Calculate trade
    shares = round(TEST_AMOUNT / selected_price, 2)
    actual_cost = shares * selected_price

    print(f"\n[4] Trade details:")
    print(f"      Amount: ${TEST_AMOUNT:.2f}")
    print(f"      Shares: {shares}")
    print(f"      Price: ${selected_price:.2f} per share")
    print(f"      Total: ${actual_cost:.2f}")

    # Confirm
    print("\n" + "-" * 60)
    print("READY TO EXECUTE TRADE")
    print("-" * 60)
    confirm = input("Execute this $5 trade? (yes/no): ").strip().lower()

    if confirm != 'yes':
        print("\nTrade cancelled.")
        return

    # Execute
    print("\n[5] Executing trade...")
    try:
        order_args = OrderArgs(
            price=selected_price,
            size=shares,
            side=BUY,
            token_id=selected_token_id,
        )

        print("    Creating and signing order...")
        signed_order = client.create_order(order_args)
        print("    ✓ Order created")

        print("    Submitting to exchange...")
        result = client.post_order(signed_order, OrderType.GTC)

        print("\n" + "=" * 60)
        print("✓ ORDER SUBMITTED!")
        print("=" * 60)
        print(f"  Order ID: {result.get('orderID', result)}")
        print(f"  Market: {question[:50]}...")
        print(f"  Outcome: {selected_outcome}")
        print(f"  Shares: {shares} @ ${selected_price:.2f}")

    except Exception as e:
        print(f"\n    ✗ Trade failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
