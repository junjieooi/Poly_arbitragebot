"""
Telegram Alert System for Polymarket Trading Bot
Sends real-time notifications for trades, positions, and risk alerts
"""

import asyncio
import aiohttp
import html
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from logger import get_logger

logger = get_logger(__name__)


class AlertRateLimiter:
    """Rate limiter for Telegram alerts to avoid hitting API limits."""

    def __init__(
        self,
        max_messages_per_second: float = 2.0,
        cooldown_seconds: float = 1.0,
        alert_type_cooldown: float = 60.0
    ):
        """
        Initialize rate limiter.

        Args:
            max_messages_per_second: Maximum messages per second (Telegram limit is ~30/sec)
            cooldown_seconds: Minimum time between any two messages
            alert_type_cooldown: Minimum time between same alert type
        """
        self.max_messages_per_second = max_messages_per_second
        self.cooldown_seconds = cooldown_seconds
        self.alert_type_cooldown = alert_type_cooldown

        self._last_message_time = 0.0
        self._alert_type_times: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, alert_type: str = "default") -> bool:
        """
        Acquire permission to send a message.

        Args:
            alert_type: Type of alert for per-type rate limiting

        Returns:
            True if allowed to send, False if rate limited
        """
        async with self._lock:
            now = time.monotonic()

            # Check global cooldown (skip if this is the first message)
            if self._last_message_time > 0:
                time_since_last = now - self._last_message_time
                if time_since_last < self.cooldown_seconds:
                    wait_time = self.cooldown_seconds - time_since_last
                    logger.debug(f"Rate limit: waiting {wait_time:.2f}s (global cooldown)")
                    await asyncio.sleep(wait_time)
                    now = time.monotonic()

            # Check per-alert-type cooldown (skip if never seen this type)
            if alert_type in self._alert_type_times:
                time_since_type = now - self._alert_type_times[alert_type]
                if time_since_type < self.alert_type_cooldown:
                    logger.debug(
                        f"Rate limit: skipping {alert_type} (cooldown: "
                        f"{self.alert_type_cooldown - time_since_type:.1f}s remaining)"
                    )
                    return False

            # Update timestamps
            self._last_message_time = now
            self._alert_type_times[alert_type] = now

            return True

    def reset_cooldown(self, alert_type: str) -> None:
        """Reset cooldown for a specific alert type."""
        self._alert_type_times[alert_type] = 0.0


class AlertLevel(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    SUCCESS = "success"


@dataclass
class TelegramConfig:
    """Telegram configuration"""
    bot_token: str
    chat_id: str
    enabled: bool = True

    @property
    def api_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"


class TelegramNotifier:
    """
    Telegram notification system for trading alerts.

    Setup Instructions:
    1. Create a bot via @BotFather on Telegram
    2. Get your bot token from BotFather
    3. Start a chat with your bot
    4. Get your chat_id by visiting: https://api.telegram.org/bot<TOKEN>/getUpdates
    5. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to your .env file
    """

    def __init__(self, config: TelegramConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = AlertRateLimiter(
            max_messages_per_second=2.0,
            cooldown_seconds=0.5,
            alert_type_cooldown=60.0  # Same alert type has 60s cooldown
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close the aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_emoji(self, level: AlertLevel) -> str:
        """Get emoji for alert level"""
        emojis = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.CRITICAL: "🚨",
            AlertLevel.SUCCESS: "✅"
        }
        return emojis.get(level, "📢")

    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        alert_type: str = "default",
        bypass_rate_limit: bool = False
    ) -> bool:
        """
        Send a message via Telegram with rate limiting.

        Args:
            text: Message text (supports HTML formatting)
            parse_mode: Parse mode (HTML or Markdown)
            alert_type: Type of alert for rate limiting (same types have cooldown)
            bypass_rate_limit: If True, skip rate limiting (for critical alerts)

        Returns:
            True if sent successfully
        """
        if not self.config.enabled:
            logger.debug("Telegram alerts disabled, skipping")
            return False

        if not self.config.bot_token or not self.config.chat_id:
            logger.warning("Telegram not configured (missing token or chat_id)")
            return False

        # Apply rate limiting unless bypassed
        if not bypass_rate_limit:
            if not await self._rate_limiter.acquire(alert_type):
                logger.debug(f"Rate limited: skipping {alert_type} alert")
                return False

        try:
            session = await self._get_session()
            url = f"{self.config.api_url}/sendMessage"

            payload = {
                "chat_id": self.config.chat_id,
                "text": text,
                "parse_mode": parse_mode
            }

            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    logger.debug("Telegram message sent successfully")
                    return True
                elif response.status == 429:  # Too Many Requests
                    retry_after = int(response.headers.get('Retry-After', 30))
                    logger.warning(f"Telegram rate limited, retry after {retry_after}s")
                    return False
                else:
                    error_text = await response.text()
                    logger.error(f"Telegram API error: {response.status} - {error_text}")
                    return False

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    # === Trading Alert Methods ===

    async def alert_trade_executed(
        self,
        action: str,
        market_id: str,
        shares: int,
        price: float,
        estimated_profit: float,
        paper: bool = True
    ):
        """Send alert when trade is executed"""
        mode = "📝 PAPER" if paper else "💰 LIVE"
        action_emoji = "🟢 BUY" if action == "BUY" else "🔴 SELL"

        # Escape user-provided data to prevent HTML injection
        safe_market_id = html.escape(str(market_id)[:20])

        message = f"""
{mode} <b>Trade Executed</b>

{action_emoji} <code>{safe_market_id}...</code>
├ Shares: <b>{shares}</b>
├ Price: <b>${price:.4f}</b>
├ Cost: <b>${shares * price:.2f}</b>
└ Est. Profit: <b>${estimated_profit:.4f}</b>

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        await self.send_message(message.strip(), alert_type="trade_executed")

    async def alert_position_update(
        self,
        market_id: str,
        shares: int,
        entry_price: float,
        current_price: float,
        pnl: float,
        pnl_pct: float
    ):
        """Send alert for position P&L update"""
        pnl_emoji = "📈" if pnl >= 0 else "📉"

        # Escape user-provided data
        safe_market_id = html.escape(str(market_id)[:20])

        message = f"""
{pnl_emoji} <b>Position Update</b>

<code>{safe_market_id}...</code>
├ Shares: <b>{shares}</b>
├ Entry: <b>${entry_price:.4f}</b>
├ Current: <b>${current_price:.4f}</b>
├ P&L: <b>${pnl:.2f}</b>
└ P&L %: <b>{pnl_pct:+.2f}%</b>
"""
        await self.send_message(message.strip(), alert_type="position_update")

    async def alert_arbitrage_opportunity(
        self,
        opp_type: str,
        profit: float,
        profit_pct: float,
        markets: list,
        executed: bool = False
    ):
        """Send alert when arbitrage opportunity is found"""
        status = "✅ EXECUTED" if executed else "👀 DETECTED"

        # Escape user-provided data
        safe_opp_type = html.escape(str(opp_type))

        message = f"""
🎯 <b>Arbitrage Opportunity {status}</b>

Type: <b>{safe_opp_type}</b>
├ Profit: <b>${profit:.4f}</b>
├ Edge: <b>{profit_pct:.2f}%</b>
└ Markets: <code>{len(markets)} outcomes</code>

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        await self.send_message(message.strip(), alert_type="arbitrage_opportunity")

    async def alert_risk_warning(
        self,
        alert_type: str,
        message_text: str,
        current_value: float = None,
        limit_value: float = None
    ):
        """Send risk management alert"""
        details = ""
        if current_value is not None and limit_value is not None:
            details = f"\n├ Current: <b>{current_value:.2%}</b>\n└ Limit: <b>{limit_value:.2%}</b>"

        # Escape user-provided data
        safe_alert_type = html.escape(str(alert_type))
        safe_message_text = html.escape(str(message_text))

        message = f"""
⚠️ <b>RISK ALERT: {safe_alert_type}</b>

{safe_message_text}{details}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        # Risk warnings bypass rate limit - they're important
        await self.send_message(message.strip(), alert_type="risk_warning", bypass_rate_limit=True)

    async def alert_stop_loss_triggered(
        self,
        market_id: str,
        pnl_pct: float,
        shares: int
    ):
        """Send alert when stop loss is triggered"""
        # Escape user-provided data
        safe_market_id = html.escape(str(market_id)[:20])

        message = f"""
🛑 <b>STOP LOSS TRIGGERED</b>

<code>{safe_market_id}...</code>
├ Loss: <b>{pnl_pct:.2f}%</b>
└ Shares Closed: <b>{shares}</b>

Position closed to prevent further losses.

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        # Stop loss is critical - bypass rate limit
        await self.send_message(message.strip(), alert_type="stop_loss", bypass_rate_limit=True)

    async def alert_daily_limit_hit(
        self,
        daily_loss_pct: float,
        limit_pct: float
    ):
        """Send alert when daily loss limit is hit"""
        message = f"""
🚨 <b>DAILY LOSS LIMIT HIT</b>

Trading has been <b>HALTED</b> for today.

├ Daily Loss: <b>{daily_loss_pct:.2f}%</b>
└ Limit: <b>{limit_pct:.2f}%</b>

Trading will resume tomorrow.

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        # Critical alert - bypass rate limit
        await self.send_message(message.strip(), alert_type="daily_limit", bypass_rate_limit=True)

    async def alert_drawdown_limit_hit(
        self,
        drawdown_pct: float,
        limit_pct: float
    ):
        """Send alert when max drawdown limit is hit"""
        message = f"""
🚨 <b>MAX DRAWDOWN LIMIT HIT</b>

Trading has been <b>HALTED</b>.

├ Drawdown: <b>{drawdown_pct:.2f}%</b>
└ Limit: <b>{limit_pct:.2f}%</b>

Manual intervention required to resume.

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        # Critical alert - bypass rate limit
        await self.send_message(message.strip(), alert_type="drawdown_limit", bypass_rate_limit=True)

    async def alert_daily_summary(
        self,
        portfolio_value: float,
        daily_pnl: float,
        daily_pnl_pct: float,
        total_trades: int,
        winning_trades: int
    ):
        """Send daily trading summary"""
        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        message = f"""
📊 <b>Daily Trading Summary</b>

{pnl_emoji} Portfolio: <b>${portfolio_value:,.2f}</b>

├ Daily P&L: <b>${daily_pnl:+.2f}</b> ({daily_pnl_pct:+.2f}%)
├ Trades: <b>{total_trades}</b>
├ Winners: <b>{winning_trades}</b>
└ Win Rate: <b>{win_rate:.1f}%</b>

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        await self.send_message(message.strip(), alert_type="daily_summary")

    async def alert_bot_started(
        self,
        mode: str,
        bankroll: float,
        strategy: str
    ):
        """Send alert when bot starts"""
        # Escape user-provided data
        safe_mode = html.escape(str(mode))
        safe_strategy = html.escape(str(strategy))

        message = f"""
🤖 <b>Trading Bot Started</b>

├ Mode: <b>{safe_mode}</b>
├ Strategy: <b>{safe_strategy}</b>
└ Bankroll: <b>${bankroll:,.2f}</b>

Bot is now monitoring for opportunities.

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        # Bot start is important - bypass rate limit
        await self.send_message(message.strip(), alert_type="bot_started", bypass_rate_limit=True)

    async def alert_bot_stopped(self, reason: str = "Manual shutdown"):
        """Send alert when bot stops"""
        # Escape user-provided data
        safe_reason = html.escape(str(reason))

        message = f"""
🔴 <b>Trading Bot Stopped</b>

Reason: {safe_reason}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        # Bot stop is important - bypass rate limit
        await self.send_message(message.strip(), alert_type="bot_stopped", bypass_rate_limit=True)

    async def alert_error(self, error_type: str, error_message: str):
        """Send alert for errors"""
        # Escape user-provided data to prevent HTML injection
        safe_error_type = html.escape(str(error_type))
        safe_error_message = html.escape(str(error_message)[:500])

        message = f"""
❌ <b>Error: {safe_error_type}</b>

<code>{safe_error_message}</code>

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        await self.send_message(message.strip(), alert_type="error")


# === Factory function ===

def create_telegram_notifier_from_env() -> Optional[TelegramNotifier]:
    """Create TelegramNotifier from environment variables"""
    import os

    bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
    enabled = os.getenv('TELEGRAM_ENABLED', 'true').lower() in ('true', '1', 'yes')

    if not bot_token or not chat_id:
        logger.info("Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
        return None

    config = TelegramConfig(
        bot_token=bot_token,
        chat_id=chat_id,
        enabled=enabled
    )

    return TelegramNotifier(config)


# === Test function ===

async def test_telegram():
    """Test Telegram integration"""
    notifier = create_telegram_notifier_from_env()

    if notifier is None:
        print("Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return

    print("Sending test message...")

    # Test basic message
    success = await notifier.send_message("🧪 <b>Test Message</b>\n\nYour Polymarket bot alerts are working!")

    if success:
        print("Test message sent successfully!")

        # Send a sample trade alert
        await notifier.alert_bot_started(
            mode="Paper Trading",
            bankroll=100.0,
            strategy="Pure Arbitrage (Low Risk)"
        )
    else:
        print("Failed to send test message. Check your bot token and chat ID.")

    await notifier.close()


if __name__ == "__main__":
    print("=" * 50)
    print("Telegram Alert System Test")
    print("=" * 50)
    print()
    print("Setup Instructions:")
    print("1. Message @BotFather on Telegram")
    print("2. Send /newbot and follow instructions")
    print("3. Copy the bot token")
    print("4. Start a chat with your new bot")
    print("5. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates")
    print("6. Find your chat_id in the response")
    print("7. Add to .env:")
    print("   TELEGRAM_BOT_TOKEN=your_token_here")
    print("   TELEGRAM_CHAT_ID=your_chat_id_here")
    print()

    asyncio.run(test_telegram())
