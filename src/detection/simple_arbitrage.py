"""
Simple Arbitrage Detector for Polymarket.

Continuously monitors order books and detects basic arbitrage opportunities:
1. Buy arbitrage: best_bid_yes + best_bid_no < 0.98 (buy both for < $1)
2. Sell arbitrage: best_ask_yes + best_ask_no > 1.02 (sell both for > $1)

Logs all opportunities to SQLite and displays real-time statistics.
"""

import asyncio
import logging
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from ..data.websocket_feed import OrderBook, WebSocketFeedHandler

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """
    Detected arbitrage opportunity.

    For a binary market with YES and NO outcomes:
    - Buy arbitrage: If we can buy BOTH for less than $1, guaranteed profit
    - Sell arbitrage: If we can sell BOTH for more than $1, guaranteed profit
    """
    id: Optional[int] = None  # Database ID
    timestamp: datetime = field(default_factory=datetime.utcnow)
    market_id: str = ""
    arb_type: str = ""  # "buy" or "sell"

    # Prices
    yes_price: float = 0.0  # Best ask (for buy arb) or best bid (for sell arb)
    no_price: float = 0.0

    # Sizes available at these prices
    yes_size: float = 0.0
    no_size: float = 0.0

    # Calculated fields
    combined_price: float = 0.0  # Sum of yes + no prices
    guaranteed_profit: float = 0.0  # Profit per $1 position
    max_position_size: float = 0.0  # Min of available sizes
    total_potential_profit: float = 0.0  # guaranteed_profit * max_position_size

    @property
    def is_valid(self) -> bool:
        """Check if this is a valid opportunity."""
        return (
            self.guaranteed_profit > 0.05 and  # Min $0.05 profit
            self.max_position_size > 0 and
            self.market_id != ""
        )

    def __str__(self) -> str:
        return (
            f"{self.arb_type.upper()} ARB: {self.market_id[:20]}... "
            f"YES={self.yes_price:.4f} NO={self.no_price:.4f} "
            f"Profit=${self.guaranteed_profit:.4f}/unit "
            f"Size=${self.max_position_size:.2f} "
            f"Total=${self.total_potential_profit:.2f}"
        )


@dataclass
class MarketPair:
    """
    A binary market with YES and NO tokens.

    Polymarket markets have two sides:
    - YES token: Pays $1 if condition is true
    - NO token: Pays $1 if condition is false
    """
    market_id: str
    question: str
    yes_token_id: str
    no_token_id: str

    # Current order book state
    yes_book: Optional[OrderBook] = None
    no_book: Optional[OrderBook] = None

    @property
    def is_ready(self) -> bool:
        """Check if both order books are loaded."""
        return (
            self.yes_book is not None and
            self.no_book is not None and
            self.yes_book.best_bid is not None and
            self.no_book.best_bid is not None
        )


class ArbitrageDatabase:
    """SQLite database for storing arbitrage opportunities."""

    def __init__(self, db_path: str = "arbitrage_opportunities.db"):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row

        cursor = self._conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                market_id TEXT NOT NULL,
                arb_type TEXT NOT NULL,
                yes_price REAL NOT NULL,
                no_price REAL NOT NULL,
                combined_price REAL NOT NULL,
                guaranteed_profit REAL NOT NULL,
                yes_size REAL NOT NULL,
                no_size REAL NOT NULL,
                max_position_size REAL NOT NULL,
                total_potential_profit REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON opportunities(timestamp)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_market ON opportunities(market_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_profit ON opportunities(guaranteed_profit)
        """)

        self._conn.commit()
        logger.info(f"Database initialized: {self.db_path}")

    def save_opportunity(self, opp: ArbitrageOpportunity) -> int:
        """
        Save an opportunity to the database.

        Returns:
            Database ID of the saved opportunity
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT INTO opportunities (
                timestamp, market_id, arb_type,
                yes_price, no_price, combined_price, guaranteed_profit,
                yes_size, no_size, max_position_size, total_potential_profit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            opp.timestamp.isoformat(),
            opp.market_id,
            opp.arb_type,
            opp.yes_price,
            opp.no_price,
            opp.combined_price,
            opp.guaranteed_profit,
            opp.yes_size,
            opp.no_size,
            opp.max_position_size,
            opp.total_potential_profit
        ))
        self._conn.commit()
        return cursor.lastrowid

    def get_opportunities(
        self,
        hours: float = 24,
        min_profit: float = 0.0,
        market_id: Optional[str] = None
    ) -> List[ArbitrageOpportunity]:
        """
        Get opportunities from the database.

        Args:
            hours: Look back period in hours
            min_profit: Minimum guaranteed profit filter
            market_id: Filter by market ID
        """
        cursor = self._conn.cursor()

        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

        query = """
            SELECT * FROM opportunities
            WHERE timestamp >= ? AND guaranteed_profit >= ?
        """
        params = [since, min_profit]

        if market_id:
            query += " AND market_id = ?"
            params.append(market_id)

        query += " ORDER BY timestamp DESC"

        cursor.execute(query, params)

        return [
            ArbitrageOpportunity(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                market_id=row["market_id"],
                arb_type=row["arb_type"],
                yes_price=row["yes_price"],
                no_price=row["no_price"],
                combined_price=row["combined_price"],
                guaranteed_profit=row["guaranteed_profit"],
                yes_size=row["yes_size"],
                no_size=row["no_size"],
                max_position_size=row["max_position_size"],
                total_potential_profit=row["total_potential_profit"]
            )
            for row in cursor.fetchall()
        ]

    def get_stats(self, hours: float = 24) -> Dict[str, Any]:
        """
        Get statistics for recent opportunities.

        Returns:
            Dict with count, median_profit, max_profit, liquidity stats
        """
        opps = self.get_opportunities(hours=hours)

        if not opps:
            return {
                "count": 0,
                "opportunities_per_hour": 0,
                "median_profit": 0,
                "max_profit": 0,
                "pct_above_100": 0,
                "total_potential_profit": 0
            }

        profits = [o.guaranteed_profit for o in opps]
        profits.sort()
        liquidity = [o.max_position_size for o in opps]

        return {
            "count": len(opps),
            "opportunities_per_hour": len(opps) / hours,
            "median_profit": profits[len(profits) // 2],
            "max_profit": max(profits),
            "min_profit": min(profits),
            "pct_above_100": sum(1 for l in liquidity if l > 100) / len(liquidity) * 100,
            "median_liquidity": sorted(liquidity)[len(liquidity) // 2],
            "total_potential_profit": sum(o.total_potential_profit for o in opps)
        }

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


class SimpleArbitrageDetector:
    """
    Simple arbitrage detector for binary Polymarket markets.

    Continuously monitors order books for two types of arbitrage:
    1. Buy arbitrage: best_ask_yes + best_ask_no < 0.98
       (Buy both YES and NO for less than $1, guaranteed $1 payout)

    2. Sell arbitrage: best_bid_yes + best_bid_no > 1.02
       (Sell both YES and NO for more than $1, no capital required)

    Usage:
        detector = SimpleArbitrageDetector(feed_handler)
        detector.add_market("market1", "yes_token", "no_token")
        detector.on_opportunity(my_callback)
        await detector.start()
    """

    # Thresholds
    BUY_THRESHOLD = 0.98  # Total cost must be below this for buy arb
    SELL_THRESHOLD = 1.02  # Total proceeds must be above this for sell arb
    MIN_PROFIT = 0.05  # Minimum guaranteed profit to flag

    def __init__(
        self,
        feed_handler: Optional[WebSocketFeedHandler] = None,
        db_path: str = "arbitrage_opportunities.db",
        check_interval: float = 0.5
    ):
        """
        Initialize arbitrage detector.

        Args:
            feed_handler: WebSocket feed handler for order book updates
            db_path: Path to SQLite database for logging opportunities
            check_interval: How often to check for arbitrage (seconds)
        """
        self.feed_handler = feed_handler or WebSocketFeedHandler()
        self.db = ArbitrageDatabase(db_path)
        self.check_interval = check_interval

        # Markets to monitor
        self._markets: Dict[str, MarketPair] = {}

        # Token ID to market ID mapping
        self._token_to_market: Dict[str, str] = {}

        # Callbacks
        self._callbacks: List[Callable[[ArbitrageOpportunity], None]] = []

        # Recent opportunities (for deduplication)
        self._recent_opps: Deque[str] = deque(maxlen=100)

        # Statistics
        self._checks = 0
        self._opportunities_found = 0
        self._running = False

        # Register for order book updates
        self.feed_handler.on_snapshot(self._on_book_update)

    def add_market(
        self,
        market_id: str,
        yes_token_id: str,
        no_token_id: str,
        question: str = ""
    ) -> None:
        """
        Add a market to monitor.

        Args:
            market_id: Unique market identifier
            yes_token_id: Token ID for YES outcome
            no_token_id: Token ID for NO outcome
            question: Market question text
        """
        market = MarketPair(
            market_id=market_id,
            question=question,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id
        )

        self._markets[market_id] = market
        self._token_to_market[yes_token_id] = market_id
        self._token_to_market[no_token_id] = market_id

        logger.info(f"Added market: {market_id} ({question[:50]}...)")

    def on_opportunity(
        self,
        callback: Callable[[ArbitrageOpportunity], None]
    ) -> None:
        """Register callback for detected opportunities."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Start the arbitrage detector."""
        if self._running:
            logger.warning("Detector already running")
            return

        self._running = True
        logger.info(f"Starting arbitrage detector for {len(self._markets)} markets")

        # Get token IDs to subscribe
        token_ids = []
        for market in self._markets.values():
            token_ids.extend([market.yes_token_id, market.no_token_id])

        # Start feed handler
        await self.feed_handler.start(token_ids)

        # Start detection loop
        try:
            while self._running:
                self._check_all_markets()
                await asyncio.sleep(self.check_interval)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the arbitrage detector."""
        self._running = False
        await self.feed_handler.stop()
        self.db.close()
        logger.info("Arbitrage detector stopped")

    def _on_book_update(self, token_id: str, book: OrderBook) -> None:
        """Handle order book update from feed handler."""
        market_id = self._token_to_market.get(token_id)
        if not market_id:
            return

        market = self._markets[market_id]

        if token_id == market.yes_token_id:
            market.yes_book = book
        elif token_id == market.no_token_id:
            market.no_book = book

    def _check_all_markets(self) -> None:
        """Check all markets for arbitrage opportunities."""
        self._checks += 1

        for market in self._markets.values():
            if not market.is_ready:
                continue

            # Check for buy arbitrage
            buy_opp = self._check_buy_arbitrage(market)
            if buy_opp and buy_opp.is_valid:
                self._handle_opportunity(buy_opp)

            # Check for sell arbitrage
            sell_opp = self._check_sell_arbitrage(market)
            if sell_opp and sell_opp.is_valid:
                self._handle_opportunity(sell_opp)

    def _check_buy_arbitrage(self, market: MarketPair) -> Optional[ArbitrageOpportunity]:
        """
        Check for buy arbitrage opportunity.

        If we can buy BOTH YES and NO for less than $1, we have a guaranteed
        profit because one of them will pay out $1.

        Buy YES at best_ask_yes, buy NO at best_ask_no.
        If best_ask_yes + best_ask_no < 0.98, profit exists.
        """
        yes_book = market.yes_book
        no_book = market.no_book

        yes_ask = yes_book.best_ask
        no_ask = no_book.best_ask

        if yes_ask is None or no_ask is None:
            return None

        combined = yes_ask + no_ask

        if combined >= self.BUY_THRESHOLD:
            return None

        # Calculate available liquidity
        yes_size = yes_book.asks[0].size if yes_book.asks else 0
        no_size = no_book.asks[0].size if no_book.asks else 0

        # Position size limited by smaller side
        max_size = min(yes_size, no_size)
        guaranteed_profit = 1.0 - combined

        return ArbitrageOpportunity(
            timestamp=datetime.utcnow(),
            market_id=market.market_id,
            arb_type="buy",
            yes_price=yes_ask,
            no_price=no_ask,
            yes_size=yes_size,
            no_size=no_size,
            combined_price=combined,
            guaranteed_profit=guaranteed_profit,
            max_position_size=max_size,
            total_potential_profit=guaranteed_profit * max_size
        )

    def _check_sell_arbitrage(self, market: MarketPair) -> Optional[ArbitrageOpportunity]:
        """
        Check for sell arbitrage opportunity.

        If we can sell BOTH YES and NO for more than $1, we have a guaranteed
        profit because we receive > $1 and only pay out $1 to the winner.

        Sell YES at best_bid_yes, sell NO at best_bid_no.
        If best_bid_yes + best_bid_no > 1.02, profit exists.
        """
        yes_book = market.yes_book
        no_book = market.no_book

        yes_bid = yes_book.best_bid
        no_bid = no_book.best_bid

        if yes_bid is None or no_bid is None:
            return None

        combined = yes_bid + no_bid

        if combined <= self.SELL_THRESHOLD:
            return None

        # Calculate available liquidity
        yes_size = yes_book.bids[0].size if yes_book.bids else 0
        no_size = no_book.bids[0].size if no_book.bids else 0

        # Position size limited by smaller side
        max_size = min(yes_size, no_size)
        guaranteed_profit = combined - 1.0

        return ArbitrageOpportunity(
            timestamp=datetime.utcnow(),
            market_id=market.market_id,
            arb_type="sell",
            yes_price=yes_bid,
            no_price=no_bid,
            yes_size=yes_size,
            no_size=no_size,
            combined_price=combined,
            guaranteed_profit=guaranteed_profit,
            max_position_size=max_size,
            total_potential_profit=guaranteed_profit * max_size
        )

    def _handle_opportunity(self, opp: ArbitrageOpportunity) -> None:
        """Handle a detected arbitrage opportunity."""
        # Deduplicate (same market within 1 second)
        key = f"{opp.market_id}:{opp.arb_type}:{int(opp.timestamp.timestamp())}"
        if key in self._recent_opps:
            return
        self._recent_opps.append(key)

        self._opportunities_found += 1

        # Log to database
        opp.id = self.db.save_opportunity(opp)

        # Log
        logger.info(str(opp))

        # Emit callbacks
        for callback in self._callbacks:
            try:
                callback(opp)
            except Exception as e:
                logger.error(f"Error in opportunity callback: {e}")

    def get_stats(self, hours: float = 24) -> Dict[str, Any]:
        """
        Get detector statistics.

        Returns combined stats from detector and database.
        """
        db_stats = self.db.get_stats(hours)

        return {
            "running": self._running,
            "markets_monitored": len(self._markets),
            "checks_performed": self._checks,
            "opportunities_this_session": self._opportunities_found,
            **db_stats
        }

    def print_stats(self, hours: float = 24) -> None:
        """Print formatted statistics to console."""
        stats = self.get_stats(hours)

        print("\n" + "=" * 60)
        print("ARBITRAGE DETECTOR STATISTICS")
        print("=" * 60)
        print(f"Time Period: Last {hours:.1f} hours")
        print(f"Markets Monitored: {stats['markets_monitored']}")
        print(f"Checks Performed: {stats['checks_performed']}")
        print("-" * 60)
        print(f"Opportunities Found: {stats['count']}")
        print(f"Opportunities/Hour: {stats['opportunities_per_hour']:.2f}")
        print(f"Median Profit Margin: ${stats['median_profit']:.4f}")
        print(f"Max Profit Margin: ${stats.get('max_profit', 0):.4f}")
        print(f"% with >$100 Liquidity: {stats['pct_above_100']:.1f}%")
        print(f"Median Liquidity: ${stats.get('median_liquidity', 0):.2f}")
        print(f"Total Potential Profit: ${stats['total_potential_profit']:.2f}")
        print("=" * 60 + "\n")


# Convenience function for testing
async def main():
    """Test the simple arbitrage detector."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Example usage - you would need real token IDs
    example_markets = [
        {
            "market_id": "example_market_1",
            "question": "Will Bitcoin exceed $100k by end of 2024?",
            "yes_token": "21742633143463906290569050155826241533067272736897614950488156847949938836455",
            "no_token": "48331043336612883890938759509493159234755048973500640148014422747788308965732"
        }
    ]

    if len(sys.argv) > 1:
        # Parse from command line: market_id:yes_token:no_token
        parts = sys.argv[1].split(":")
        if len(parts) == 3:
            example_markets = [{
                "market_id": parts[0],
                "question": "",
                "yes_token": parts[1],
                "no_token": parts[2]
            }]

    detector = SimpleArbitrageDetector(
        db_path="test_arbitrage.db"
    )

    # Register markets
    for market in example_markets:
        detector.add_market(
            market_id=market["market_id"],
            yes_token_id=market["yes_token"],
            no_token_id=market["no_token"],
            question=market["question"]
        )

    # Register callback
    def on_opp(opp: ArbitrageOpportunity):
        print(f"\n*** OPPORTUNITY: {opp} ***\n")

    detector.on_opportunity(on_opp)

    try:
        # Start detector
        await detector.start()

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await detector.stop()
        detector.print_stats()


if __name__ == "__main__":
    asyncio.run(main())
