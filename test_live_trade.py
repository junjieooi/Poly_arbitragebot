"""
Live Trading Test Script - $5 Test Trades
==========================================

This script tests the Polymarket connection with small $5 trades.

BEFORE RUNNING:
1. Set your API credentials in .env file:
   - POLYMARKET_API_KEY=your_key
   - POLYMARKET_API_SECRET=your_secret
   - POLYMARKET_PRIVATE_KEY=your_wallet_private_key

2. Make sure you have USDC on Polygon network in your wallet

3. Run with: python test_live_trade.py
"""

import asyncio
import os
from datetime import datetime

# Add project to path
import sys
sys.path.insert(0, '.')

from config import get_config
from logger import setup_logging, get_logger
from polymarket_client import PolymarketClient, OrderSide, OrderType

setup_logging(level='INFO')
logger = get_logger(__name__)

# Test configuration
TEST_AMOUNT_USD = 5.0  # $5 per trade
MAX_TRADES = 1  # Only 1 trade for testing


async def check_credentials():
    """Check if API credentials are configured"""
    config = get_config()

    missing = []
    if not config.api.api_key:
        missing.append("POLYMARKET_API_KEY")
    if not config.api.api_secret:
        missing.append("POLYMARKET_API_SECRET")
    if not config.api.private_key:
        missing.append("POLYMARKET_PRIVATE_KEY")

    if missing:
        print("\n" + "=" * 60)
        print("MISSING API CREDENTIALS")
        print("=" * 60)
        print("\nPlease add these to your .env file:")
        for m in missing:
            print(f"  - {m}")
        print("\nGet credentials from: https://polymarket.com/")
        print("  1. Connect your wallet")
        print("  2. Go to Account Settings → API")
        print("  3. Generate API key and secret")
        print("  4. Your private key is from your Polygon wallet")
        print("\n" + "=" * 60)
        return False

    return True


async def test_connection():
    """Test API connection without trading"""
    print("\n" + "=" * 60)
    print("TESTING POLYMARKET CONNECTION")
    print("=" * 60)

    async with PolymarketClient() as client:
        # Test 1: Fetch markets
        print("\n[1] Fetching markets...")
        try:
            markets = await client.get_markets(limit=5, active=True)
            print(f"    ✓ Found {len(markets)} active markets")

            for i, market in enumerate(markets[:3], 1):
                print(f"\n    Market {i}: {market.question[:50]}...")
                print(f"      Volume 24h: ${market.volume_24h:,.0f}")
                print(f"      Liquidity: ${market.liquidity:,.0f}")

                if market.tokens:
                    for token in market.tokens[:2]:
                        token_id = token.get('token_id')
                        outcome = token.get('outcome', 'Unknown')
                        if token_id:
                            price = await client.get_price(token_id)
                            if price:
                                print(f"      - {outcome}: {price:.2f}¢")
        except Exception as e:
            print(f"    ✗ Error fetching markets: {e}")
            return False

        # Test 2: Check balance
        print("\n[2] Checking balance...")
        try:
            balance = await client.get_balance()
            print(f"    ✓ USDC Balance: ${balance:,.2f}")

            if balance < TEST_AMOUNT_USD:
                print(f"    ⚠ Warning: Balance (${balance:.2f}) is less than test amount (${TEST_AMOUNT_USD:.2f})")
                print(f"      You need USDC on Polygon network")
                return False
        except Exception as e:
            print(f"    ✗ Error checking balance: {e}")
            print(f"      This might mean API key doesn't have trading permissions")
            return False

        # Test 3: Check existing positions
        print("\n[3] Checking positions...")
        try:
            positions = await client.get_positions()
            print(f"    ✓ Current positions: {len(positions)}")
            for pos in positions[:3]:
                print(f"      - {pos.outcome}: {pos.size} shares @ {pos.avg_price:.2f}¢")
        except Exception as e:
            print(f"    ✗ Error checking positions: {e}")

        print("\n" + "=" * 60)
        print("CONNECTION TEST PASSED")
        print("=" * 60)
        return True


async def find_best_opportunity():
    """Find a market with good liquidity for testing"""
    print("\n[4] Finding test market...")

    async with PolymarketClient() as client:
        markets = await client.get_markets(limit=20, active=True)

        # Find market with good liquidity and reasonable prices
        best_market = None
        best_token = None
        best_price = None

        for market in markets:
            if market.liquidity < 10000:  # Skip low liquidity
                continue

            if not market.tokens or len(market.tokens) < 2:
                continue

            for token in market.tokens:
                token_id = token.get('token_id')
                if not token_id:
                    continue

                price = await client.get_price(token_id)
                if price and 0.20 <= price <= 0.80:  # Reasonable price range
                    if best_market is None or market.liquidity > best_market.liquidity:
                        best_market = market
                        best_token = token
                        best_price = price

        if best_market:
            print(f"    ✓ Found test market:")
            print(f"      Question: {best_market.question[:60]}...")
            print(f"      Outcome: {best_token.get('outcome')}")
            print(f"      Price: {best_price:.2f}¢")
            print(f"      Liquidity: ${best_market.liquidity:,.0f}")
            return best_market, best_token, best_price
        else:
            print(f"    ✗ No suitable market found")
            return None, None, None


async def execute_test_trade(market, token, price):
    """Execute a small test trade"""
    print("\n" + "=" * 60)
    print("EXECUTING TEST TRADE")
    print("=" * 60)

    token_id = token.get('token_id')
    outcome = token.get('outcome', 'Unknown')

    # Calculate shares for $5
    shares = int(TEST_AMOUNT_USD / price)
    actual_cost = shares * price

    print(f"\n  Market: {market.question[:50]}...")
    print(f"  Outcome: {outcome}")
    print(f"  Action: BUY")
    print(f"  Price: {price:.2f}¢")
    print(f"  Shares: {shares}")
    print(f"  Cost: ${actual_cost:.2f}")

    # Confirmation
    print("\n" + "-" * 40)
    confirm = input("  Execute this trade? (yes/no): ").strip().lower()

    if confirm != 'yes':
        print("  Trade cancelled.")
        return False

    print("\n  Placing order...")

    async with PolymarketClient() as client:
        try:
            order = await client.create_order(
                token_id=token_id,
                side=OrderSide.BUY,
                price=price,
                size=shares,
                order_type=OrderType.GTC
            )

            if order:
                print(f"  ✓ Order placed successfully!")
                print(f"    Order ID: {order.id}")
                print(f"    Status: {order.status.value}")
                print(f"    Size: {order.size} shares")
                print(f"    Price: {order.price:.2f}¢")

                # Check new balance
                await asyncio.sleep(2)  # Wait for settlement
                new_balance = await client.get_balance()
                print(f"\n  New balance: ${new_balance:,.2f}")

                return True
            else:
                print(f"  ✗ Order failed")
                return False

        except Exception as e:
            print(f"  ✗ Error placing order: {e}")
            return False


async def main():
    """Main test flow"""
    print("\n" + "=" * 60)
    print("POLYMARKET LIVE TRADING TEST")
    print(f"Test Amount: ${TEST_AMOUNT_USD:.2f}")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    # Check credentials
    if not await check_credentials():
        return

    # Test connection
    if not await test_connection():
        print("\nConnection test failed. Please check your credentials.")
        return

    # Find opportunity
    market, token, price = await find_best_opportunity()
    if not market:
        print("\nNo suitable market found for testing.")
        return

    # Execute trade
    print("\n" + "-" * 60)
    print("Ready to execute test trade.")
    print(f"This will spend approximately ${TEST_AMOUNT_USD:.2f}")
    print("-" * 60)

    proceed = input("\nProceed with test trade? (yes/no): ").strip().lower()

    if proceed == 'yes':
        success = await execute_test_trade(market, token, price)
        if success:
            print("\n" + "=" * 60)
            print("TEST TRADE COMPLETED SUCCESSFULLY!")
            print("=" * 60)
        else:
            print("\nTest trade failed.")
    else:
        print("\nTest cancelled.")


if __name__ == "__main__":
    asyncio.run(main())
