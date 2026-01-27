"""
Generate Polymarket API Credentials
====================================

This script generates your API key, secret, and passphrase from your wallet private key.

Prerequisites:
1. Install the Polymarket client: pip install py-clob-client
2. Add your wallet private key to .env as POLYMARKET_PRIVATE_KEY

Usage: python generate_api_keys.py
"""

import os
import sys

def main():
    print("=" * 60)
    print("POLYMARKET API KEY GENERATOR")
    print("=" * 60)

    # Load private key from .env
    from dotenv import load_dotenv
    load_dotenv()

    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")

    if not private_key:
        print("\n❌ ERROR: POLYMARKET_PRIVATE_KEY not found in .env file")
        print("\nPlease add your wallet private key to .env:")
        print("  POLYMARKET_PRIVATE_KEY=your_private_key_here")
        print("\nTo get your private key from MetaMask:")
        print("  1. Click the 3 dots menu")
        print("  2. Account Details")
        print("  3. Export Private Key")
        return

    # Remove 0x prefix if present
    if private_key.startswith("0x"):
        private_key = private_key[2:]

    print(f"\n✓ Private key found (length: {len(private_key)} chars)")

    # Try to import the Polymarket client
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
    except ImportError:
        print("\n❌ ERROR: py-clob-client not installed")
        print("\nInstall it with:")
        print("  pip install py-clob-client")
        return

    print("\n⏳ Connecting to Polymarket...")

    try:
        # Initialize client with private key
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,  # Polygon mainnet
            key=private_key
        )

        print("✓ Connected to Polymarket CLOB")

        # Generate or derive API credentials
        print("\n⏳ Generating API credentials...")
        api_creds = client.create_or_derive_api_creds()

        print("\n" + "=" * 60)
        print("✓ API CREDENTIALS GENERATED SUCCESSFULLY!")
        print("=" * 60)

        print(f"\nAPI Key:     {api_creds.api_key}")
        print(f"API Secret:  {api_creds.api_secret}")
        print(f"Passphrase:  {api_creds.api_passphrase}")

        # Save to .env
        print("\n" + "-" * 60)
        save = input("Save these credentials to .env file? (yes/no): ").strip().lower()

        if save == 'yes':
            # Read current .env
            env_path = ".env"
            with open(env_path, 'r') as f:
                env_content = f.read()

            # Update or add credentials
            updates = {
                'POLYMARKET_API_KEY': api_creds.api_key,
                'POLYMARKET_API_SECRET': api_creds.api_secret,
                'POLYMARKET_PASSPHRASE': api_creds.api_passphrase,
            }

            for key, value in updates.items():
                if f"{key}=" in env_content:
                    # Replace existing
                    import re
                    env_content = re.sub(
                        f"#?\\s*{key}=.*",
                        f"{key}={value}",
                        env_content
                    )
                else:
                    # Add new
                    env_content += f"\n{key}={value}"

            with open(env_path, 'w') as f:
                f.write(env_content)

            print(f"\n✓ Credentials saved to {env_path}")
            print("\nYou can now run: python test_live_trade.py")
        else:
            print("\nCredentials NOT saved. Copy them manually to .env:")
            print(f"\n  POLYMARKET_API_KEY={api_creds.api_key}")
            print(f"  POLYMARKET_API_SECRET={api_creds.api_secret}")
            print(f"  POLYMARKET_PASSPHRASE={api_creds.api_passphrase}")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print("\nPossible causes:")
        print("  1. Invalid private key format")
        print("  2. Network connection issue")
        print("  3. Polymarket API is down")


if __name__ == "__main__":
    main()
