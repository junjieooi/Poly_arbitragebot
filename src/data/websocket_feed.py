"""
WebSocket Feed Handler for Polymarket CLOB API.

Connects to Polymarket's WebSocket feed to receive real-time order book updates.
Maintains in-memory order book state and emits events on significant changes.

WebSocket endpoint: wss://ws-subscriptions-clob.polymarket.com/ws/market

Protocol:
- Subscribe with: {"type": "subscribe", "channel": "book", "assets_id": "<token_id>"}
- Receive book snapshots and delta updates
- Heartbeat ping/pong every 30 seconds
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

# Configure logging
logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """WebSocket connection state."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class OrderBookLevel:
    """Single price level in the order book."""
    price: float
    size: float

    def __lt__(self, other: "OrderBookLevel") -> bool:
        return self.price < other.price


@dataclass
class OrderBook:
    """
    Order book for a single token/condition.

    Bids are sorted descending by price (best bid first).
    Asks are sorted ascending by price (best ask first).
    """
    token_id: str
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    timestamp: int = 0
    last_update_time: datetime = field(default_factory=datetime.utcnow)

    @property
    def best_bid(self) -> Optional[float]:
        """Return best bid price or None if no bids."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        """Return best ask price or None if no asks."""
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        """Return bid-ask spread or None if incomplete book."""
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Return mid price or None if incomplete book."""
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    def update_level(self, side: str, price: float, size: float) -> None:
        """
        Update a price level in the order book.

        If size is 0, remove the level. Otherwise update or insert.
        """
        levels = self.bids if side == "bid" else self.asks

        # Find existing level
        for i, level in enumerate(levels):
            if abs(level.price - price) < 1e-10:
                if size <= 0:
                    levels.pop(i)
                else:
                    level.size = size
                self._sort_levels(side)
                return

        # Insert new level if size > 0
        if size > 0:
            levels.append(OrderBookLevel(price=price, size=size))
            self._sort_levels(side)

    def _sort_levels(self, side: str) -> None:
        """Sort levels: bids descending, asks ascending."""
        if side == "bid":
            self.bids.sort(key=lambda x: x.price, reverse=True)
        else:
            self.asks.sort(key=lambda x: x.price)

    def set_snapshot(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        timestamp: int
    ) -> None:
        """Replace entire order book with snapshot."""
        self.bids = [OrderBookLevel(price=p, size=s) for p, s in bids if s > 0]
        self.asks = [OrderBookLevel(price=p, size=s) for p, s in asks if s > 0]
        self.timestamp = timestamp
        self.last_update_time = datetime.utcnow()
        self._sort_levels("bid")
        self._sort_levels("ask")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for storage."""
        return {
            "bids": [(level.price, level.size) for level in self.bids],
            "asks": [(level.price, level.size) for level in self.asks],
            "timestamp": self.timestamp
        }


@dataclass
class OrderBookChangeEvent:
    """Event emitted when order book changes significantly."""
    token_id: str
    side: str  # "bid" or "ask"
    old_price: Optional[float]
    new_price: Optional[float]
    change_pct: float
    timestamp: datetime


class WebSocketFeedHandler:
    """
    Real-time WebSocket feed handler for Polymarket CLOB.

    Features:
    - Automatic reconnection with exponential backoff
    - Order book state management
    - Significant change detection (>2% threshold)
    - Heartbeat monitoring

    Usage:
        handler = WebSocketFeedHandler()
        handler.on_book_change(my_callback)
        await handler.start(["token_id_1", "token_id_2"])
    """

    WEBSOCKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    HEARTBEAT_INTERVAL = 30  # seconds
    CHANGE_THRESHOLD = 0.02  # 2% change triggers event

    def __init__(
        self,
        max_reconnect_attempts: int = 10,
        base_reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 60.0
    ):
        """
        Initialize WebSocket feed handler.

        Args:
            max_reconnect_attempts: Maximum reconnection attempts before giving up
            base_reconnect_delay: Initial delay between reconnections (seconds)
            max_reconnect_delay: Maximum delay between reconnections (seconds)
        """
        self.max_reconnect_attempts = max_reconnect_attempts
        self.base_reconnect_delay = base_reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay

        self._state = ConnectionState.DISCONNECTED
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._subscribed_tokens: List[str] = []
        self._order_books: Dict[str, OrderBook] = {}
        self._change_callbacks: List[Callable[[OrderBookChangeEvent], None]] = []
        self._snapshot_callbacks: List[Callable[[str, OrderBook], None]] = []
        self._reconnect_count = 0
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._last_message_time: float = 0

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def order_books(self) -> Dict[str, OrderBook]:
        """Current order book state for all subscribed tokens."""
        return self._order_books.copy()

    def get_order_book(self, token_id: str) -> Optional[OrderBook]:
        """Get order book for specific token."""
        return self._order_books.get(token_id)

    def on_book_change(self, callback: Callable[[OrderBookChangeEvent], None]) -> None:
        """Register callback for significant order book changes (>2%)."""
        self._change_callbacks.append(callback)

    def on_snapshot(self, callback: Callable[[str, OrderBook], None]) -> None:
        """Register callback for order book snapshots."""
        self._snapshot_callbacks.append(callback)

    async def start(self, token_ids: List[str]) -> None:
        """
        Start the WebSocket connection and subscribe to tokens.

        Args:
            token_ids: List of token IDs to subscribe to
        """
        if self._running:
            logger.warning("WebSocket feed already running")
            return

        self._subscribed_tokens = token_ids.copy()
        self._running = True

        # Initialize order books
        for token_id in token_ids:
            if token_id not in self._order_books:
                self._order_books[token_id] = OrderBook(token_id=token_id)

        # Start main loop
        self._tasks.append(asyncio.create_task(self._connection_loop()))

        logger.info(f"WebSocket feed started for {len(token_ids)} tokens")

    async def stop(self) -> None:
        """Stop the WebSocket connection gracefully."""
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()

        # Close WebSocket
        if self._websocket:
            await self._websocket.close()
            self._websocket = None

        self._state = ConnectionState.DISCONNECTED
        logger.info("WebSocket feed stopped")

    async def subscribe(self, token_id: str) -> None:
        """Subscribe to a new token's order book."""
        if token_id in self._subscribed_tokens:
            return

        self._subscribed_tokens.append(token_id)
        self._order_books[token_id] = OrderBook(token_id=token_id)

        if self._websocket and self._state == ConnectionState.CONNECTED:
            await self._send_subscription(token_id)

    async def unsubscribe(self, token_id: str) -> None:
        """Unsubscribe from a token's order book."""
        if token_id not in self._subscribed_tokens:
            return

        self._subscribed_tokens.remove(token_id)

        if self._websocket and self._state == ConnectionState.CONNECTED:
            await self._send_unsubscription(token_id)

    async def _connection_loop(self) -> None:
        """Main connection loop with reconnection logic."""
        while self._running:
            try:
                self._state = ConnectionState.CONNECTING

                async with websockets.connect(
                    self.WEBSOCKET_URL,
                    ping_interval=self.HEARTBEAT_INTERVAL,
                    ping_timeout=10,
                    close_timeout=5
                ) as websocket:
                    self._websocket = websocket
                    self._state = ConnectionState.CONNECTED
                    self._reconnect_count = 0

                    logger.info("WebSocket connected successfully")

                    # Subscribe to all tokens
                    await self._subscribe_all()

                    # Start message handler
                    await self._message_loop()

            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
            except WebSocketException as e:
                logger.error(f"WebSocket error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error in WebSocket loop: {e}")

            if not self._running:
                break

            # Reconnection logic with exponential backoff
            self._state = ConnectionState.RECONNECTING
            self._reconnect_count += 1

            if self._reconnect_count > self.max_reconnect_attempts:
                logger.error(
                    f"Max reconnection attempts ({self.max_reconnect_attempts}) reached"
                )
                self._running = False
                break

            delay = min(
                self.base_reconnect_delay * (2 ** (self._reconnect_count - 1)),
                self.max_reconnect_delay
            )

            logger.info(
                f"Reconnecting in {delay:.1f}s "
                f"(attempt {self._reconnect_count}/{self.max_reconnect_attempts})"
            )

            await asyncio.sleep(delay)

        self._state = ConnectionState.DISCONNECTED

    async def _message_loop(self) -> None:
        """Process incoming WebSocket messages."""
        async for message in self._websocket:
            self._last_message_time = time.time()

            try:
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse message: {e}")
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """
        Handle incoming WebSocket message.

        Message types:
        - book: Full order book snapshot
        - book_update: Delta update to order book
        - heartbeat: Keep-alive message
        """
        msg_type = data.get("type") or data.get("event_type")

        if msg_type == "book":
            await self._handle_book_snapshot(data)
        elif msg_type == "book_update" or msg_type == "price_change":
            await self._handle_book_update(data)
        elif msg_type == "heartbeat":
            logger.debug("Received heartbeat")
        elif msg_type == "subscribed":
            logger.info(f"Subscribed to {data.get('asset_id', 'unknown')}")
        elif msg_type == "error":
            logger.error(f"WebSocket error: {data.get('message', 'Unknown error')}")
        else:
            logger.debug(f"Unknown message type: {msg_type}")

    async def _handle_book_snapshot(self, data: Dict[str, Any]) -> None:
        """Handle full order book snapshot."""
        token_id = data.get("asset_id") or data.get("market")

        if not token_id or token_id not in self._order_books:
            return

        book = self._order_books[token_id]
        old_best_bid = book.best_bid
        old_best_ask = book.best_ask

        # Parse bids and asks
        bids = []
        asks = []

        for bid in data.get("bids", []):
            price = float(bid.get("price", bid.get("p", 0)))
            size = float(bid.get("size", bid.get("s", 0)))
            bids.append((price, size))

        for ask in data.get("asks", []):
            price = float(ask.get("price", ask.get("p", 0)))
            size = float(ask.get("size", ask.get("s", 0)))
            asks.append((price, size))

        timestamp = data.get("timestamp", int(time.time() * 1000))
        book.set_snapshot(bids, asks, timestamp)

        logger.debug(
            f"Book snapshot for {token_id}: "
            f"bid={book.best_bid}, ask={book.best_ask}, levels={len(book.bids)}/{len(book.asks)}"
        )

        # Emit snapshot callbacks
        for callback in self._snapshot_callbacks:
            try:
                callback(token_id, book)
            except Exception as e:
                logger.error(f"Error in snapshot callback: {e}")

        # Check for significant change
        self._check_significant_change(token_id, "bid", old_best_bid, book.best_bid)
        self._check_significant_change(token_id, "ask", old_best_ask, book.best_ask)

    async def _handle_book_update(self, data: Dict[str, Any]) -> None:
        """Handle incremental order book update."""
        token_id = data.get("asset_id") or data.get("market")

        if not token_id or token_id not in self._order_books:
            return

        book = self._order_books[token_id]
        old_best_bid = book.best_bid
        old_best_ask = book.best_ask

        # Process changes
        changes = data.get("changes", [])
        for change in changes:
            side = change.get("side", "").lower()
            if side not in ("bid", "ask", "buy", "sell"):
                continue

            # Normalize side
            if side == "buy":
                side = "bid"
            elif side == "sell":
                side = "ask"

            price = float(change.get("price", change.get("p", 0)))
            size = float(change.get("size", change.get("s", 0)))

            book.update_level(side, price, size)

        book.timestamp = data.get("timestamp", int(time.time() * 1000))
        book.last_update_time = datetime.utcnow()

        # Check for significant change
        self._check_significant_change(token_id, "bid", old_best_bid, book.best_bid)
        self._check_significant_change(token_id, "ask", old_best_ask, book.best_ask)

    def _check_significant_change(
        self,
        token_id: str,
        side: str,
        old_price: Optional[float],
        new_price: Optional[float]
    ) -> None:
        """Check if price change exceeds threshold and emit event."""
        if old_price is None or new_price is None:
            return

        if old_price == 0:
            return

        change_pct = abs(new_price - old_price) / old_price

        if change_pct >= self.CHANGE_THRESHOLD:
            event = OrderBookChangeEvent(
                token_id=token_id,
                side=side,
                old_price=old_price,
                new_price=new_price,
                change_pct=change_pct,
                timestamp=datetime.utcnow()
            )

            logger.info(
                f"Significant {side} change for {token_id}: "
                f"{old_price:.4f} -> {new_price:.4f} ({change_pct*100:.1f}%)"
            )

            for callback in self._change_callbacks:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"Error in change callback: {e}")

    async def _subscribe_all(self) -> None:
        """Subscribe to all configured tokens."""
        for token_id in self._subscribed_tokens:
            await self._send_subscription(token_id)
            await asyncio.sleep(0.1)  # Rate limit

    async def _send_subscription(self, token_id: str) -> None:
        """Send subscription message for a token."""
        message = {
            "type": "subscribe",
            "channel": "book",
            "assets_id": token_id
        }

        try:
            await self._websocket.send(json.dumps(message))
            logger.debug(f"Subscribed to {token_id}")
        except Exception as e:
            logger.error(f"Failed to subscribe to {token_id}: {e}")

    async def _send_unsubscription(self, token_id: str) -> None:
        """Send unsubscription message for a token."""
        message = {
            "type": "unsubscribe",
            "channel": "book",
            "assets_id": token_id
        }

        try:
            await self._websocket.send(json.dumps(message))
            logger.debug(f"Unsubscribed from {token_id}")
        except Exception as e:
            logger.error(f"Failed to unsubscribe from {token_id}: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get handler statistics."""
        return {
            "state": self._state.value,
            "subscribed_tokens": len(self._subscribed_tokens),
            "reconnect_count": self._reconnect_count,
            "last_message_age": time.time() - self._last_message_time if self._last_message_time else None,
            "order_books": {
                token_id: {
                    "best_bid": book.best_bid,
                    "best_ask": book.best_ask,
                    "spread": book.spread,
                    "bid_levels": len(book.bids),
                    "ask_levels": len(book.asks),
                    "last_update": book.last_update_time.isoformat()
                }
                for token_id, book in self._order_books.items()
            }
        }


# Convenience function for testing
async def main():
    """Test the WebSocket feed handler."""
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Example token IDs (replace with real ones)
    # These are example condition token IDs from Polymarket
    test_tokens = [
        "21742633143463906290569050155826241533067272736897614950488156847949938836455",  # Example
    ]

    if len(sys.argv) > 1:
        test_tokens = sys.argv[1:]

    handler = WebSocketFeedHandler()

    # Register callbacks
    def on_change(event: OrderBookChangeEvent):
        print(f"CHANGE: {event.token_id[:20]}... {event.side} {event.change_pct*100:.1f}%")

    def on_snapshot(token_id: str, book: OrderBook):
        print(f"SNAPSHOT: {token_id[:20]}... bid={book.best_bid} ask={book.best_ask}")

    handler.on_book_change(on_change)
    handler.on_snapshot(on_snapshot)

    try:
        await handler.start(test_tokens)

        # Run for 60 seconds
        for i in range(60):
            await asyncio.sleep(1)
            if i % 10 == 0:
                print(f"\nStats: {handler.get_stats()}\n")

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await handler.stop()


if __name__ == "__main__":
    asyncio.run(main())
