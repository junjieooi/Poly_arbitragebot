"""
Polymarket CLOB API Client
Async client for interacting with Polymarket's Central Limit Order Book API

Documentation: https://docs.polymarket.com/
"""

import asyncio
import aiohttp
import hashlib
import hmac
import json
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Callable
from enum import Enum
import websockets

from logger import get_logger, TradingLogger
from config import get_config

logger = get_logger(__name__)
trading_logger = TradingLogger(__name__)


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    LIMIT = "LMT"
    MARKET = "MKT"
    GTC = "GTC"  # Good Till Cancelled
    GTD = "GTD"  # Good Till Date
    FOK = "FOK"  # Fill or Kill


class OrderStatus(Enum):
    LIVE = "LIVE"
    MATCHED = "MATCHED"
    CANCELLED = "CANCELLED"


@dataclass
class Market:
    """Polymarket market data"""
    condition_id: str
    question_id: str
    question: str
    description: str
    tokens: List[Dict]
    end_date: Optional[datetime]
    active: bool
    closed: bool
    liquidity: float
    volume: float
    volume_24h: float

    @classmethod
    def from_api(cls, data: Dict) -> 'Market':
        """Create Market from API response"""
        return cls(
            condition_id=data.get('conditionId', ''),
            question_id=data.get('questionId', ''),
            question=data.get('question', ''),
            description=data.get('description', ''),
            tokens=data.get('tokens', []),
            end_date=datetime.fromisoformat(data['endDate']) if data.get('endDate') else None,
            active=data.get('active', False),
            closed=data.get('closed', False),
            liquidity=float(data.get('liquidity', 0)),
            volume=float(data.get('volume', 0)),
            volume_24h=float(data.get('volume24hr', 0)),
        )


@dataclass
class OrderBookEntry:
    """Single order book entry"""
    price: float
    size: float


@dataclass
class OrderBook:
    """Order book for a token"""
    token_id: str
    bids: List[OrderBookEntry]
    asks: List[OrderBookEntry]
    timestamp: datetime

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None


@dataclass
class Order:
    """Order details"""
    id: str
    token_id: str
    side: OrderSide
    price: float
    size: float
    remaining_size: float
    status: OrderStatus
    created_at: datetime
    order_type: OrderType = OrderType.LIMIT

    @classmethod
    def from_api(cls, data: Dict) -> 'Order':
        return cls(
            id=data.get('id', ''),
            token_id=data.get('asset_id', ''),
            side=OrderSide(data.get('side', 'BUY')),
            price=float(data.get('price', 0)),
            size=float(data.get('original_size', 0)),
            remaining_size=float(data.get('size_matched', 0)),
            status=OrderStatus(data.get('status', 'LIVE')),
            created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else datetime.now(),
            order_type=OrderType(data.get('type', 'LMT')),
        )


@dataclass
class Position:
    """User position in a market"""
    token_id: str
    outcome: str
    size: float
    avg_price: float
    realized_pnl: float
    unrealized_pnl: float


class RateLimiter:
    """Token bucket rate limiter"""

    def __init__(self, requests_per_second: int = 10, period: float = 1.0):
        self.requests_per_second = requests_per_second
        self.period = period
        self.tokens = requests_per_second
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Acquire a token, waiting if necessary"""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(
                self.requests_per_second,
                self.tokens + elapsed * (self.requests_per_second / self.period)
            )
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) * (self.period / self.requests_per_second)
                logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


class PolymarketClient:
    """
    Async client for Polymarket CLOB API

    Usage:
        async with PolymarketClient() as client:
            markets = await client.get_markets()
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        private_key: Optional[str] = None,
        base_url: Optional[str] = None,
        ws_url: Optional[str] = None,
    ):
        config = get_config()

        self.api_key = api_key or config.api.api_key
        self.api_secret = api_secret or config.api.api_secret
        self.private_key = private_key or config.api.private_key
        self.base_url = (base_url or config.api.base_url).rstrip('/')
        self.ws_url = ws_url or config.api.ws_url
        self.timeout = aiohttp.ClientTimeout(total=config.api.request_timeout)
        self.max_retries = config.api.max_retries

        self.rate_limiter = RateLimiter(
            config.api.rate_limit_requests,
            config.api.rate_limit_period
        )

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_connection: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_callbacks: Dict[str, List[Callable]] = {}
        self._ws_reconnect_attempts = 0
        self._ws_max_reconnect_attempts = 10
        self._ws_reconnect_delay = 1.0  # Start with 1 second
        self._ws_subscribed_tokens: List[str] = []
        self._ws_running = False

    def __repr__(self) -> str:
        """Safe string representation that masks credentials."""
        masked_key = f"{self.api_key[:4]}***" if self.api_key and len(self.api_key) > 4 else "***"
        return f"<PolymarketClient api_key={masked_key} base_url={self.base_url}>"

    def __str__(self) -> str:
        """Safe string representation that masks credentials."""
        return self.__repr__()

    @staticmethod
    def _mask_credential(credential: Optional[str], visible_chars: int = 4) -> str:
        """Mask a credential for safe logging."""
        if not credential:
            return "<not set>"
        if len(credential) <= visible_chars:
            return "***"
        return f"{credential[:visible_chars]}***"

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def connect(self):
        """Initialize HTTP session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        logger.info("Polymarket client connected")

    async def close(self):
        """Close all connections"""
        if self._ws_connection:
            await self._ws_connection.close()
            self._ws_connection = None

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        logger.info("Polymarket client disconnected")

    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = '') -> str:
        """Generate HMAC signature for authenticated requests"""
        if not self.api_secret:
            raise ValueError("API secret required for authenticated requests")

        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return signature

    def _get_auth_headers(self, method: str, path: str, body: str = '') -> Dict[str, str]:
        """Generate authentication headers"""
        if not self.api_key:
            return {}

        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(timestamp, method, path, body)

        return {
            'POLY_API_KEY': self.api_key,
            'POLY_TIMESTAMP': timestamp,
            'POLY_SIGNATURE': signature,
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        authenticated: bool = False
    ) -> Dict:
        """Make HTTP request with retry logic"""
        if self._session is None:
            await self.connect()

        await self.rate_limiter.acquire()

        url = f"{self.base_url}{endpoint}"
        body = json.dumps(data) if data else ''

        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers.update(self._get_auth_headers(method, endpoint, body))

        for attempt in range(self.max_retries):
            try:
                async with self._session.request(
                    method,
                    url,
                    params=params,
                    json=data,
                    headers=headers
                ) as response:
                    if response.status == 429:  # Rate limited
                        wait_time = int(response.headers.get('Retry-After', 5))
                        logger.warning(f"Rate limited, waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                        continue

                    response.raise_for_status()
                    return await response.json()

            except aiohttp.ClientError as e:
                logger.error(f"Request failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt == self.max_retries - 1:
                    trading_logger.api_error(endpoint, str(e), method=method)
                    raise
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        return {}

    # === Market Data Endpoints ===

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True
    ) -> List[Market]:
        """Get list of markets"""
        params = {
            'limit': limit,
            'offset': offset,
            'active': str(active).lower()
        }

        response = await self._request('GET', '/markets', params=params)
        markets = response.get('data', response) if isinstance(response, dict) else response

        if isinstance(markets, list):
            return [Market.from_api(m) for m in markets]
        return []

    async def get_market(self, condition_id: str) -> Optional[Market]:
        """Get single market by condition ID"""
        response = await self._request('GET', f'/markets/{condition_id}')
        if response:
            return Market.from_api(response)
        return None

    async def get_order_book(self, token_id: str) -> OrderBook:
        """Get order book for a token"""
        response = await self._request('GET', f'/book', params={'token_id': token_id})

        bids = [
            OrderBookEntry(float(b['price']), float(b['size']))
            for b in response.get('bids', [])
        ]
        asks = [
            OrderBookEntry(float(a['price']), float(a['size']))
            for a in response.get('asks', [])
        ]

        return OrderBook(
            token_id=token_id,
            bids=sorted(bids, key=lambda x: x.price, reverse=True),
            asks=sorted(asks, key=lambda x: x.price),
            timestamp=datetime.now()
        )

    async def get_price(self, token_id: str) -> Optional[float]:
        """Get current mid price for a token"""
        order_book = await self.get_order_book(token_id)
        return order_book.mid_price

    async def get_prices(self, token_ids: List[str]) -> Dict[str, float]:
        """Get prices for multiple tokens"""
        tasks = [self.get_price(tid) for tid in token_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        prices = {}
        for tid, price in zip(token_ids, results):
            if isinstance(price, float):
                prices[tid] = price
        return prices

    # === Trading Endpoints (Authenticated) ===

    async def create_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
        order_type: OrderType = OrderType.GTC
    ) -> Optional[Order]:
        """Create a new order"""
        if not self.api_key:
            raise ValueError("API key required for trading")

        data = {
            'tokenID': token_id,
            'side': side.value,
            'price': str(price),
            'size': str(size),
            'type': order_type.value,
        }

        response = await self._request('POST', '/order', data=data, authenticated=True)

        if response:
            order = Order.from_api(response)
            trading_logger.trade_executed(
                token_id,
                side.value,
                int(size),
                price,
                order_id=order.id
            )
            return order
        return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order"""
        if not self.api_key:
            raise ValueError("API key required for trading")

        try:
            await self._request('DELETE', f'/order/{order_id}', authenticated=True)
            logger.info(f"Order {order_id} cancelled")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def cancel_all_orders(self, token_id: Optional[str] = None) -> int:
        """Cancel all open orders, optionally filtered by token"""
        if not self.api_key:
            raise ValueError("API key required for trading")

        params = {}
        if token_id:
            params['token_id'] = token_id

        response = await self._request('DELETE', '/orders', params=params, authenticated=True)
        cancelled = response.get('cancelled', 0)
        logger.info(f"Cancelled {cancelled} orders")
        return cancelled

    async def get_orders(
        self,
        token_id: Optional[str] = None,
        status: Optional[OrderStatus] = None
    ) -> List[Order]:
        """Get user's orders"""
        if not self.api_key:
            raise ValueError("API key required")

        params = {}
        if token_id:
            params['token_id'] = token_id
        if status:
            params['status'] = status.value

        response = await self._request('GET', '/orders', params=params, authenticated=True)
        orders = response.get('data', response) if isinstance(response, dict) else response

        if isinstance(orders, list):
            return [Order.from_api(o) for o in orders]
        return []

    async def get_positions(self) -> List[Position]:
        """Get user's current positions"""
        if not self.api_key:
            raise ValueError("API key required")

        response = await self._request('GET', '/positions', authenticated=True)
        positions = response.get('data', response) if isinstance(response, dict) else response

        result = []
        if isinstance(positions, list):
            for p in positions:
                result.append(Position(
                    token_id=p.get('asset_id', ''),
                    outcome=p.get('outcome', ''),
                    size=float(p.get('size', 0)),
                    avg_price=float(p.get('avg_price', 0)),
                    realized_pnl=float(p.get('realized_pnl', 0)),
                    unrealized_pnl=float(p.get('unrealized_pnl', 0)),
                ))
        return result

    async def get_balance(self) -> float:
        """Get USDC balance"""
        if not self.api_key:
            raise ValueError("API key required")

        response = await self._request('GET', '/balance', authenticated=True)
        return float(response.get('balance', 0))

    # === WebSocket Streaming ===

    async def subscribe_order_book(
        self,
        token_ids: List[str],
        callback: Callable[[str, OrderBook], None]
    ):
        """Subscribe to order book updates via WebSocket"""
        if not self._ws_connection:
            if not await self._ws_connect():
                raise ConnectionError("Failed to establish WebSocket connection")

        # Track subscribed tokens for reconnection
        for tid in token_ids:
            if tid not in self._ws_subscribed_tokens:
                self._ws_subscribed_tokens.append(tid)

        # Subscribe to tokens
        subscribe_msg = {
            'type': 'subscribe',
            'channel': 'book',
            'assets': token_ids
        }
        await self._ws_connection.send(json.dumps(subscribe_msg))

        # Store callback
        for tid in token_ids:
            if tid not in self._ws_callbacks:
                self._ws_callbacks[tid] = []
            self._ws_callbacks[tid].append(callback)

        logger.info(f"Subscribed to order book updates for {len(token_ids)} tokens")

    async def _ws_connect(self) -> bool:
        """Establish WebSocket connection with retry logic."""
        try:
            self._ws_connection = await websockets.connect(
                self.ws_url,
                ping_interval=30,  # Send ping every 30 seconds
                ping_timeout=10,   # Wait 10 seconds for pong
            )
            self._ws_reconnect_attempts = 0
            self._ws_reconnect_delay = 1.0
            logger.info("WebSocket connected successfully")
            return True
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            return False

    async def _ws_reconnect(self) -> bool:
        """Attempt to reconnect WebSocket with exponential backoff."""
        if self._ws_reconnect_attempts >= self._ws_max_reconnect_attempts:
            logger.error(f"Max WebSocket reconnection attempts ({self._ws_max_reconnect_attempts}) reached")
            return False

        self._ws_reconnect_attempts += 1
        delay = min(self._ws_reconnect_delay * (2 ** (self._ws_reconnect_attempts - 1)), 300)  # Cap at 5 minutes

        logger.info(f"WebSocket reconnecting in {delay:.1f}s (attempt {self._ws_reconnect_attempts}/{self._ws_max_reconnect_attempts})")
        await asyncio.sleep(delay)

        if await self._ws_connect():
            # Re-subscribe to previously subscribed tokens
            if self._ws_subscribed_tokens:
                subscribe_msg = {
                    'type': 'subscribe',
                    'channel': 'book',
                    'assets': self._ws_subscribed_tokens
                }
                await self._ws_connection.send(json.dumps(subscribe_msg))
                logger.info(f"Re-subscribed to {len(self._ws_subscribed_tokens)} tokens")
            return True
        return False

    async def start_websocket_listener(self):
        """Start listening to WebSocket messages with automatic reconnection."""
        self._ws_running = True

        while self._ws_running:
            if not self._ws_connection:
                if not await self._ws_connect():
                    if not await self._ws_reconnect():
                        logger.error("WebSocket listener stopping due to connection failure")
                        break
                    continue

            try:
                async for message in self._ws_connection:
                    data = json.loads(message)

                    if data.get('type') == 'book':
                        token_id = data.get('asset_id')
                        if token_id in self._ws_callbacks:
                            order_book = OrderBook(
                                token_id=token_id,
                                bids=[
                                    OrderBookEntry(float(b['price']), float(b['size']))
                                    for b in data.get('bids', [])
                                ],
                                asks=[
                                    OrderBookEntry(float(a['price']), float(a['size']))
                                    for a in data.get('asks', [])
                                ],
                                timestamp=datetime.now()
                            )
                            for callback in self._ws_callbacks[token_id]:
                                try:
                                    await callback(token_id, order_book)
                                except Exception as e:
                                    logger.error(f"WebSocket callback error: {e}")

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                self._ws_connection = None
                if self._ws_running:
                    if not await self._ws_reconnect():
                        break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self._ws_connection = None
                if self._ws_running:
                    if not await self._ws_reconnect():
                        break

        self._ws_running = False
        logger.info("WebSocket listener stopped")

    async def stop_websocket_listener(self):
        """Stop the WebSocket listener gracefully."""
        self._ws_running = False
        if self._ws_connection:
            await self._ws_connection.close()
            self._ws_connection = None
        logger.info("WebSocket listener stop requested")

    # === Utility Methods ===

    def calculate_effective_price(
        self,
        order_book: OrderBook,
        size: float,
        side: OrderSide
    ) -> Optional[float]:
        """
        Calculate effective fill price accounting for slippage.
        Walks the order book to determine actual execution price.
        """
        entries = order_book.asks if side == OrderSide.BUY else order_book.bids
        if not entries:
            return None

        remaining_size = size
        total_cost = 0.0

        for entry in entries:
            fill_size = min(remaining_size, entry.size)
            total_cost += fill_size * entry.price
            remaining_size -= fill_size

            if remaining_size <= 0:
                break

        if remaining_size > 0:
            # Not enough liquidity
            logger.warning(f"Insufficient liquidity: need {size}, available {size - remaining_size}")
            return None

        return total_cost / size

    def check_liquidity(
        self,
        order_book: OrderBook,
        size: float,
        side: OrderSide,
        max_slippage: float = 0.02
    ) -> Dict:
        """
        Check if there's sufficient liquidity for an order.

        Args:
            order_book: Current order book
            size: Desired order size
            side: BUY or SELL
            max_slippage: Maximum acceptable slippage (default 2%)

        Returns:
            Dict with liquidity analysis
        """
        entries = order_book.asks if side == OrderSide.BUY else order_book.bids
        if not entries:
            return {'sufficient': False, 'reason': 'No liquidity'}

        best_price = entries[0].price
        effective_price = self.calculate_effective_price(order_book, size, side)

        if effective_price is None:
            return {'sufficient': False, 'reason': 'Insufficient depth'}

        slippage = abs(effective_price - best_price) / best_price

        return {
            'sufficient': slippage <= max_slippage,
            'best_price': best_price,
            'effective_price': effective_price,
            'slippage': slippage,
            'slippage_pct': slippage * 100,
            'max_slippage': max_slippage,
            'available_at_best': entries[0].size,
            'total_depth': sum(e.size for e in entries[:10])
        }


# Example usage and testing
async def main():
    """Example usage of PolymarketClient"""
    from logger import setup_logging
    setup_logging(level='DEBUG')

    async with PolymarketClient() as client:
        logger.info("Fetching markets...")
        markets = await client.get_markets(limit=5)

        for market in markets:
            logger.info(f"Market: {market.question[:50]}...")
            logger.info(f"  Condition ID: {market.condition_id}")
            logger.info(f"  Volume 24h: ${market.volume_24h:,.2f}")

            # Get order book for first token if available
            if market.tokens:
                token_id = market.tokens[0].get('token_id')
                if token_id:
                    order_book = await client.get_order_book(token_id)
                    logger.info(f"  Best Bid: {order_book.best_bid}")
                    logger.info(f"  Best Ask: {order_book.best_ask}")
                    logger.info(f"  Spread: {order_book.spread}")


if __name__ == "__main__":
    asyncio.run(main())
