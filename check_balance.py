"""Check wallet balance and allowances"""
import os
import sys
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

api_key = os.getenv("POLYMARKET_API_KEY")
api_secret = os.getenv("POLYMARKET_API_SECRET")
passphrase = os.getenv("POLYMARKET_PASSPHRASE")
private_key = os.getenv("POLYMARKET_PRIVATE_KEY")

pk = private_key
if pk and pk.startswith("0x"):
    pk = pk[2:]

creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase)
client = ClobClient(host="https://clob.polymarket.com", chain_id=137, key=pk, creds=creds)

print("=" * 60)
print("WALLET STATUS CHECK")
print("=" * 60)

# Get wallet address
from eth_account import Account
acct = Account.from_key(pk)
wallet_address = acct.address
print(f"\nWallet Address: {wallet_address}")

# Check balance using py-clob-client
print("\n[1] Checking USDC balance...")
try:
    # The client should have methods to check balance
    # Try to get collateral balance
    balance_info = client.get_balance_allowance()
    print(f"  Balance info: {balance_info}")
except Exception as e:
    print(f"  Error getting balance via client: {e}")

# Check if we can get orders (this works if auth is correct)
print("\n[2] Checking API authentication...")
try:
    orders = client.get_orders()
    print(f"  ✓ API auth working - {len(orders) if orders else 0} open orders")
except Exception as e:
    print(f"  ✗ API auth error: {e}")

# Check positions
print("\n[3] Checking positions...")
try:
    # Get all positions
    positions = client.get_positions()
    if positions:
        print(f"  Found {len(positions)} positions:")
        for p in positions[:5]:
            print(f"    - {p}")
    else:
        print("  No positions found")
except Exception as e:
    print(f"  Error: {e}")

print("\n" + "=" * 60)
print("NEXT STEPS")
print("=" * 60)
print("""
The error "not enough balance / allowance" means:

1. BALANCE: Your wallet needs USDC on Polygon network
   - Check if you have USDC at: https://polygonscan.com/address/{}
   - You need USDC.e on Polygon (not USDC on Ethereum)

2. ALLOWANCE: You need to approve Polymarket to spend USDC
   - This is done through the Polymarket website
   - Go to polymarket.com, connect your wallet, and deposit USDC
   - The deposit process sets up the allowance

3. PROXY WALLET: Polymarket uses a proxy wallet system
   - You might need to deposit USDC into Polymarket first
   - Use the Polymarket UI to "Deposit" funds

Quick fix:
   1. Go to https://polymarket.com
   2. Connect your wallet (the one with private key in .env)
   3. Click "Deposit" and follow the steps
   4. This will:
      - Transfer USDC to your Polymarket proxy wallet
      - Set up the allowances needed for trading

After depositing, run this script again to verify balance.
""".format(wallet_address))
