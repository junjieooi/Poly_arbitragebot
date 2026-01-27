#!/usr/bin/env python3
"""
Telegram Chat ID Fetcher

Utility script to fetch chat IDs for:
- Your personal chat
- Groups
- Channels

Usage:
    python fetch_telegram_id.py                    # Uses token from .env
    python fetch_telegram_id.py --token YOUR_TOKEN # Uses provided token
    python fetch_telegram_id.py --listen           # Continuously listen for new messages
"""

import os
import sys
import argparse
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path


def load_env():
    """Load environment variables from .env file"""
    env_file = Path('.env')
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())


class TelegramIDFetcher:
    """Fetch Telegram chat IDs using Bot API"""

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.api_url = f"https://api.telegram.org/bot{bot_token}"

    async def get_updates(self, offset: int = None, timeout: int = 30) -> dict:
        """Get updates from Telegram API"""
        params = {"timeout": timeout}
        if offset:
            params["offset"] = offset

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.api_url}/getUpdates", params=params) as resp:
                return await resp.json()

    async def get_me(self) -> dict:
        """Get bot information"""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.api_url}/getMe") as resp:
                return await resp.json()

    async def send_message(self, chat_id: int, text: str) -> dict:
        """Send a message to a chat"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.api_url}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            ) as resp:
                return await resp.json()

    def format_chat_info(self, chat: dict) -> str:
        """Format chat information for display"""
        chat_type = chat.get('type', 'unknown')
        chat_id = chat.get('id')

        if chat_type == 'private':
            name = f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip()
            username = chat.get('username', '')
            return f"  Type: Private Chat\n  Name: {name}\n  Username: @{username}\n  Chat ID: {chat_id}"
        elif chat_type == 'group':
            title = chat.get('title', 'Unknown Group')
            return f"  Type: Group\n  Title: {title}\n  Chat ID: {chat_id}"
        elif chat_type == 'supergroup':
            title = chat.get('title', 'Unknown Supergroup')
            username = chat.get('username', '')
            return f"  Type: Supergroup\n  Title: {title}\n  Username: @{username}\n  Chat ID: {chat_id}"
        elif chat_type == 'channel':
            title = chat.get('title', 'Unknown Channel')
            username = chat.get('username', '')
            return f"  Type: Channel\n  Title: {title}\n  Username: @{username}\n  Chat ID: {chat_id}"
        else:
            return f"  Type: {chat_type}\n  Chat ID: {chat_id}"

    async def fetch_all_chats(self) -> list:
        """Fetch all chats that have interacted with the bot"""
        print("Fetching recent updates...")

        result = await self.get_updates()

        if not result.get('ok'):
            print(f"Error: {result.get('description', 'Unknown error')}")
            return []

        updates = result.get('result', [])

        if not updates:
            print("\nNo recent messages found.")
            print("\nTo get chat IDs:")
            print("  1. Send a message to your bot")
            print("  2. Add the bot to a group/channel and send a message")
            print("  3. Run this script again")
            return []

        # Extract unique chats
        chats = {}
        for update in updates:
            message = update.get('message') or update.get('channel_post') or update.get('my_chat_member', {}).get('chat')
            if message:
                chat = message.get('chat') if isinstance(message, dict) and 'chat' in message else message
                if chat and 'id' in chat:
                    chat_id = chat['id']
                    if chat_id not in chats:
                        chats[chat_id] = chat

        return list(chats.values())

    async def listen_for_messages(self):
        """Continuously listen for new messages and display chat IDs"""
        print("Listening for new messages...")
        print("Send a message to any chat where the bot is added.")
        print("Press Ctrl+C to stop.\n")

        offset = None
        seen_chats = set()

        try:
            while True:
                result = await self.get_updates(offset=offset, timeout=30)

                if not result.get('ok'):
                    print(f"Error: {result.get('description', 'Unknown error')}")
                    await asyncio.sleep(5)
                    continue

                updates = result.get('result', [])

                for update in updates:
                    offset = update['update_id'] + 1

                    message = update.get('message') or update.get('channel_post')
                    if message:
                        chat = message.get('chat', {})
                        chat_id = chat.get('id')

                        if chat_id and chat_id not in seen_chats:
                            seen_chats.add(chat_id)
                            timestamp = datetime.now().strftime('%H:%M:%S')
                            print(f"\n[{timestamp}] New chat detected:")
                            print(self.format_chat_info(chat))
                            print("-" * 40)

        except KeyboardInterrupt:
            print("\n\nStopped listening.")


async def main():
    parser = argparse.ArgumentParser(
        description="Fetch Telegram chat IDs for your bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python fetch_telegram_id.py                    # Fetch all recent chat IDs
    python fetch_telegram_id.py --listen           # Listen for new messages
    python fetch_telegram_id.py --token YOUR_TOKEN # Use specific bot token

Steps to get chat IDs:
    1. Send /start to your bot in a private chat
    2. Add the bot to a group and send a message
    3. Add the bot as admin to a channel and post something
    4. Run this script to see all chat IDs
        """
    )

    parser.add_argument(
        '--token',
        help='Bot token (defaults to TELEGRAM_BOT_TOKEN from .env)'
    )
    parser.add_argument(
        '--listen',
        action='store_true',
        help='Continuously listen for new messages'
    )

    args = parser.parse_args()

    # Load .env file
    load_env()

    # Get bot token
    bot_token = args.token or os.getenv('TELEGRAM_BOT_TOKEN')

    if not bot_token:
        print("Error: No bot token provided.")
        print("Either:")
        print("  1. Set TELEGRAM_BOT_TOKEN in your .env file")
        print("  2. Use --token YOUR_TOKEN argument")
        sys.exit(1)

    fetcher = TelegramIDFetcher(bot_token)

    # Verify bot token
    print("Verifying bot token...")
    bot_info = await fetcher.get_me()

    if not bot_info.get('ok'):
        print(f"Error: Invalid bot token - {bot_info.get('description', 'Unknown error')}")
        sys.exit(1)

    bot = bot_info.get('result', {})
    print(f"Bot: @{bot.get('username')} ({bot.get('first_name')})")
    print("=" * 50)

    if args.listen:
        await fetcher.listen_for_messages()
    else:
        chats = await fetcher.fetch_all_chats()

        if chats:
            print(f"\nFound {len(chats)} chat(s):\n")
            print("=" * 50)

            for i, chat in enumerate(chats, 1):
                print(f"\n{i}. Chat Information:")
                print(fetcher.format_chat_info(chat))
                print("-" * 50)

            print("\n\nTo use a chat ID in your .env file:")
            print("TELEGRAM_CHAT_ID=<chat_id>")

            # Show copy-paste ready format
            print("\n\nQuick copy-paste (Chat IDs only):")
            for chat in chats:
                chat_type = chat.get('type', 'unknown')
                chat_id = chat.get('id')
                name = chat.get('title') or chat.get('first_name') or chat.get('username') or 'Unknown'
                print(f"  {chat_id}  # {chat_type}: {name}")


if __name__ == "__main__":
    asyncio.run(main())
