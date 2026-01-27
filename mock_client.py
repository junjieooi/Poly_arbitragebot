"""
Mock Polymarket Client for Testing
Generates realistic fake market data without API access
"""

import asyncio
import random
from datetime import datetime, timedelta
from typing import List, Optional, Dict
from dataclasses import dataclass

from polymarket_client import (
    Market, OrderBook, OrderBookEntry, Order, Position,
    OrderSide, OrderType, OrderStatus
)
from logger import get_logger

logger = get_logger(__name__)


# Sample market questions for realistic testing
SAMPLE_MARKETS = [
    {
        "question": "Will Bitcoin exceed $100,000 by end of 2025?",
        "outcomes": ["Yes", "No"],
        "base_prices": [0.45, 0.55],
    },
    {
        "question": "Will the Fed cut rates in Q1 2025?",
        "outcomes": ["Yes", "No"],
        "base_prices": [0.62, 0.38],
    },
    {
        "question": "Who will win the 2024 Presidential Election?",
        "outcomes": ["Democrat", "Republican", "Other"],
        "base_prices": [0.48, 0.50, 0.02],
    },
    {
        "question": "Will Ethereum merge to PoS successfully?",
        "outcomes": ["Yes", "No"],
        "base_prices": [0.85, 0.15],
    },
    {
        "question": "Will SpaceX land humans on Mars by 2030?",
        "outcomes": ["Yes", "No"],
        "base_prices": [0.15, 0.85],
    },
    {
        "question": "Which team will win the Super Bowl?",
        "outcomes": ["Team A", "Team B", "Team C", "Team D"],
        "base_prices": [0.30, 0.28, 0.25, 0.17],
    },
    {
        "question": "Will inflation fall below 3% by mid-2025?",
        "outcomes": ["Yes", "No"],
        "base_prices": [0.55, 0.45],
    },
    {
        "question": "Will AI pass the Turing test by 2026?",
        "outcomes": ["Yes", "No"],
        "base_prices": [0.35, 0.65],
    },
]


class MockPolymarketClient:
    """
    Mock client that generates realistic fake market data.
    Use this for testing without API access.
    """

    def __init__(self, volatility: float = 0.02, create_arbitrage: bool = True):
        """
        Args:
            volatility: Price volatility for random movements
            create_arbitrage: Whether to occasionally create arbitrage opportunities
        """
        self.volatility = volatility
        self.create_arbitrage = create_arbitrage
        self._markets: Dict[str, dict] = {}
        self._orders: Dict[str, Order] = {}
        self._positions: Dict[str, Position] = {}
        self._balance = 10000.0
        self._initialized = False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def connect(self):
        """Initialize mock markets"""
        if not self._initialized:
            self._generate_markets()
            self._initialized = True
        logger.info("Mock Polymarket client connected")

    async def close(self):
        """Close mock client"""
        logger.info("Mock Polymarket client disconnected")

    def _generate_markets(self):
        """Generate mock markets from templates"""
        for i, template in enumerate(SAMPLE_MARKETS):
            market_id = f"mock_market_{i}"
            tokens = []

            for j, (outcome, base_price) in enumerate(
                zip(template["outcomes"], template["base_prices"])
            ):
                token_id = f"{market_id}_token_{j}"
                tokens.append({
                    "token_id": token_id,
                    "outcome": outcome,
                    "price": base_price,
                })

            self._markets[market_id] = {
                "condition_id": market_id,
                "question_id": f"q_{i}",
                "question": template["question"],
                "description": f"Mock market for testing: {template['question']}",
                "tokens": tokens,
                "end_date": (datetime.now() + timedelta(days=random.randint(30, 365))).isoformat(),
                "active": True,
                "closed": False,
                "liquidity": random.uniform(10000, 100000),
                "volume": random.uniform(50000, 500000),
                "volume24hr": random.uniform(1000, 50000),
            }

    def _add_noise(self, price: float) -> float:
        """Add random noise to price"""
        noise = random.gauss(0, self.volatility)
        return max(0.01, min(0.99, price + noise))

    def _maybe_create_arbitrage(self, tokens: List[dict]) -> List[dict]:
        """Occasionally create arbitrage opportunities for testing"""
        if not self.create_arbitrage or random.random() > 0.1:  # 10% chance
            return tokens

        # Create buy-all opportunity (prices sum < 1)
        if random.random() > 0.5:
            total = sum(t["price"] for t in tokens)
            if total > 0.90:
                # Reduce prices slightly
                factor = 0.92 / total
                for t in tokens:
                    t["price"] = t["price"] * factor
                logger.debug(f"Created buy-all arbitrage opportunity")
        else:
            # Create sell-all opportunity (prices sum > 1)
            total = sum(t["price"] for t in tokens)
            if total < 1.10:
                factor = 1.08 / total
                for t in tokens:
                    t["price"] = min(0.95, t["price"] * factor)
                logger.debug(f"Created sell-all arbitrage opportunity")

        return tokens

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True
    ) -> List[Market]:
        """Get mock markets"""
        await asyncio.sleep(0.1)  # Simulate network delay

        markets = []
        for market_id, data in list(self._markets.items())[offset:offset + limit]:
            if active and not data["active"]:
                continue

            # Update prices with noise
            tokens = []
            for token in data["tokens"]:
                token_copy = token.copy()
                token_copy["price"] = self._add_noise(token["price"])
                tokens.append(token_copy)

            # Maybe create arbitrage
            tokens = self._maybe_create_arbitrage(tokens)

            markets.append(Market(
                condition_id=data["condition_id"],
                question_id=data["question_id"],
                question=data["question"],
                description=data["description"],
                tokens=tokens,
                end_date=datetime.fromisoformat(data["end_date"]),
                active=data["active"],
                closed=data["closed"],
                liquidity=data["liquidity"],
                volume=data["volume"],
                volume_24h=data["volume24hr"],
            ))

        return markets

    async def get_market(self, condition_id: str) -> Optional[Market]:
        """Get single mock market"""
        markets = await self.get_markets()
        for market in markets:
            if market.condition_id == condition_id:
                return market
        return None

    async def get_order_book(self, token_id: str) -> OrderBook:
        """Generate mock order book"""
        await asyncio.sleep(0.05)

        # Find base price for this token
        base_price = 0.50
        for market_data in self._markets.values():
            for token in market_data["tokens"]:
                if token["token_id"] == token_id:
                    base_price = token["price"]
                    break

        # Generate order book around base price
        bids = []
        asks = []

        for i in range(5):
            bid_price = base_price - 0.01 * (i + 1)
            ask_price = base_price + 0.01 * (i + 1)
            size = random.uniform(100, 1000) * (5 - i)  # More liquidity near spread

            if bid_price > 0:
                bids.append(OrderBookEntry(price=bid_price, size=size))
            if ask_price < 1:
                asks.append(OrderBookEntry(price=ask_price, size=size))

        return OrderBook(
            token_id=token_id,
            bids=sorted(bids, key=lambda x: x.price, reverse=True),
            asks=sorted(asks, key=lambda x: x.price),
            timestamp=datetime.now()
        )

    async def get_price(self, token_id: str) -> Optional[float]:
        """Get mock price"""
        order_book = await self.get_order_book(token_id)
        return order_book.mid_price

    async def create_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
        order_type: OrderType = OrderType.GTC
    ) -> Optional[Order]:
        """Create mock order (always succeeds in mock)"""
        await asyncio.sleep(0.1)

        order_id = f"mock_order_{len(self._orders)}"
        order = Order(
            id=order_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            remaining_size=0,  # Assume filled
            status=OrderStatus.MATCHED,
            created_at=datetime.now(),
            order_type=order_type
        )

        self._orders[order_id] = order

        # Update balance
        if side == OrderSide.BUY:
            self._balance -= price * size
        else:
            self._balance += price * size

        logger.info(f"Mock order executed: {side.value} {size} @ {price}")
        return order

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel mock order"""
        if order_id in self._orders:
            self._orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False

    async def get_orders(
        self,
        token_id: Optional[str] = None,
        status: Optional[OrderStatus] = None
    ) -> List[Order]:
        """Get mock orders"""
        orders = list(self._orders.values())
        if token_id:
            orders = [o for o in orders if o.token_id == token_id]
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    async def get_positions(self) -> List[Position]:
        """Get mock positions"""
        return list(self._positions.values())

    async def get_balance(self) -> float:
        """Get mock balance"""
        return self._balance


async def demo():
    """Demo the mock client"""
    from logger import setup_logging
    setup_logging(level='INFO')

    print("=" * 60)
    print("MOCK CLIENT DEMO")
    print("=" * 60)

    async with MockPolymarketClient(volatility=0.02, create_arbitrage=True) as client:
        # Get markets
        markets = await client.get_markets(limit=5)
        print(f"\nFound {len(markets)} markets:")

        for market in markets:
            print(f"\n  {market.question}")
            total_price = 0
            for token in market.tokens:
                price = token.get('price', 0)
                total_price += price
                print(f"    - {token['outcome']}: {price:.2f}")
            print(f"    Total: {total_price:.2f}")

            if total_price < 0.98:
                print(f"    >>> BUY-ALL ARBITRAGE OPPORTUNITY!")
            elif total_price > 1.02:
                print(f"    >>> SELL-ALL ARBITRAGE OPPORTUNITY!")

        # Get order book
        if markets and markets[0].tokens:
            token_id = markets[0].tokens[0].get('token_id')
            if token_id:
                order_book = await client.get_order_book(token_id)
                print(f"\nOrder book for {token_id}:")
                print(f"  Best bid: {order_book.best_bid:.2f}")
                print(f"  Best ask: {order_book.best_ask:.2f}")
                print(f"  Spread: {order_book.spread:.4f}")

        # Place mock order
        print(f"\nPlacing mock order...")
        order = await client.create_order(
            token_id="mock_market_0_token_0",
            side=OrderSide.BUY,
            price=0.50,
            size=100
        )
        print(f"  Order ID: {order.id}")
        print(f"  Status: {order.status.value}")

        print(f"\nBalance: ${client._balance:,.2f}")


if __name__ == "__main__":
    asyncio.run(demo())
