"""
Unit tests for polymarket_client module
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import sys
sys.path.insert(0, '..')

from polymarket_client import (
    PolymarketClient,
    Market,
    OrderBook,
    OrderBookEntry,
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
    Position,
    RateLimiter,
)


class TestRateLimiter:
    """Test RateLimiter class"""

    @pytest.mark.asyncio
    async def test_acquire_immediate(self):
        """First acquire should be immediate"""
        limiter = RateLimiter(requests_per_second=10)
        start = asyncio.get_event_loop().time()
        await limiter.acquire()
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.1  # Should be nearly instant

    @pytest.mark.asyncio
    async def test_acquire_rate_limited(self):
        """Rapid acquires should be rate limited"""
        limiter = RateLimiter(requests_per_second=5, period=1.0)

        # Exhaust tokens
        for _ in range(5):
            await limiter.acquire()

        # Next acquire should wait
        start = asyncio.get_event_loop().time()
        await limiter.acquire()
        elapsed = asyncio.get_event_loop().time() - start

        # Should have waited some time
        assert elapsed >= 0.1  # At least some delay


class TestMarket:
    """Test Market dataclass"""

    def test_from_api_minimal(self):
        """Should create Market from minimal API data"""
        data = {
            'conditionId': 'abc123',
            'questionId': 'q123',
            'question': 'Will X happen?',
            'description': 'Test description',
            'tokens': [],
            'active': True,
            'closed': False,
            'liquidity': '1000',
            'volume': '5000',
            'volume24hr': '200',
        }

        market = Market.from_api(data)

        assert market.condition_id == 'abc123'
        assert market.question_id == 'q123'
        assert market.question == 'Will X happen?'
        assert market.active is True
        assert market.closed is False
        assert market.liquidity == 1000.0
        assert market.volume == 5000.0
        assert market.volume_24h == 200.0

    def test_from_api_with_end_date(self):
        """Should parse end date if provided"""
        data = {
            'conditionId': 'abc123',
            'questionId': 'q123',
            'question': 'Test?',
            'description': '',
            'tokens': [],
            'endDate': '2025-12-31T23:59:59',
            'active': True,
            'closed': False,
            'liquidity': '0',
            'volume': '0',
            'volume24hr': '0',
        }

        market = Market.from_api(data)
        assert market.end_date is not None
        assert market.end_date.year == 2025

    def test_from_api_missing_fields(self):
        """Should handle missing fields gracefully"""
        data = {}
        market = Market.from_api(data)

        assert market.condition_id == ''
        assert market.question == ''
        assert market.tokens == []
        assert market.active is False
        assert market.liquidity == 0.0


class TestOrderBook:
    """Test OrderBook dataclass"""

    def test_best_bid_ask(self):
        """Should return best bid and ask"""
        book = OrderBook(
            token_id='token123',
            bids=[
                OrderBookEntry(0.45, 100),
                OrderBookEntry(0.44, 200),
            ],
            asks=[
                OrderBookEntry(0.46, 150),
                OrderBookEntry(0.47, 250),
            ],
            timestamp=datetime.now()
        )

        assert book.best_bid == 0.45
        assert book.best_ask == 0.46

    def test_spread(self):
        """Should calculate spread correctly"""
        book = OrderBook(
            token_id='token123',
            bids=[OrderBookEntry(0.45, 100)],
            asks=[OrderBookEntry(0.47, 100)],
            timestamp=datetime.now()
        )

        assert book.spread == pytest.approx(0.02)

    def test_mid_price(self):
        """Should calculate mid price correctly"""
        book = OrderBook(
            token_id='token123',
            bids=[OrderBookEntry(0.44, 100)],
            asks=[OrderBookEntry(0.46, 100)],
            timestamp=datetime.now()
        )

        assert book.mid_price == pytest.approx(0.45)

    def test_empty_book(self):
        """Should handle empty order book"""
        book = OrderBook(
            token_id='token123',
            bids=[],
            asks=[],
            timestamp=datetime.now()
        )

        assert book.best_bid is None
        assert book.best_ask is None
        assert book.spread is None
        assert book.mid_price is None


class TestPolymarketClient:
    """Test PolymarketClient class"""

    def test_repr_masks_credentials(self):
        """__repr__ should mask API key"""
        client = PolymarketClient(
            api_key='my_secret_key_12345',
            api_secret='my_secret',
        )

        repr_str = repr(client)
        assert 'my_secret_key_12345' not in repr_str
        assert 'my_s***' in repr_str or '***' in repr_str

    def test_str_masks_credentials(self):
        """__str__ should mask API key"""
        client = PolymarketClient(api_key='secret_api_key')

        str_output = str(client)
        assert 'secret_api_key' not in str_output

    def test_mask_credential(self):
        """_mask_credential should properly mask credentials"""
        assert PolymarketClient._mask_credential('abcdefghij', 4) == 'abcd***'
        assert PolymarketClient._mask_credential('abc', 4) == '***'
        assert PolymarketClient._mask_credential(None) == '<not set>'
        assert PolymarketClient._mask_credential('') == '<not set>'

    @pytest.mark.asyncio
    async def test_connect_creates_session(self):
        """connect() should create aiohttp session"""
        client = PolymarketClient()
        await client.connect()

        assert client._session is not None
        assert not client._session.closed

        await client.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        """close() should clean up resources"""
        client = PolymarketClient()
        await client.connect()
        await client.close()

        assert client._session is None or client._session.closed

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Should work as async context manager"""
        async with PolymarketClient() as client:
            assert client._session is not None

    def test_calculate_effective_price_sufficient_liquidity(self):
        """Should calculate effective price correctly"""
        client = PolymarketClient()
        book = OrderBook(
            token_id='token123',
            bids=[],
            asks=[
                OrderBookEntry(0.50, 100),
                OrderBookEntry(0.51, 100),
                OrderBookEntry(0.52, 100),
            ],
            timestamp=datetime.now()
        )

        # Buy 50 shares, should fill at 0.50
        price = client.calculate_effective_price(book, 50, OrderSide.BUY)
        assert price == pytest.approx(0.50)

        # Buy 150 shares, should fill 100 @ 0.50 + 50 @ 0.51 = avg 0.503
        price = client.calculate_effective_price(book, 150, OrderSide.BUY)
        expected = (100 * 0.50 + 50 * 0.51) / 150
        assert price == pytest.approx(expected)

    def test_calculate_effective_price_insufficient_liquidity(self):
        """Should return None if insufficient liquidity"""
        client = PolymarketClient()
        book = OrderBook(
            token_id='token123',
            bids=[],
            asks=[OrderBookEntry(0.50, 50)],
            timestamp=datetime.now()
        )

        # Try to buy more than available
        price = client.calculate_effective_price(book, 100, OrderSide.BUY)
        assert price is None

    def test_check_liquidity_sufficient(self):
        """Should report sufficient liquidity correctly"""
        client = PolymarketClient()
        book = OrderBook(
            token_id='token123',
            bids=[],
            asks=[
                OrderBookEntry(0.50, 1000),
                OrderBookEntry(0.51, 1000),
            ],
            timestamp=datetime.now()
        )

        result = client.check_liquidity(book, 100, OrderSide.BUY, max_slippage=0.02)

        assert result['sufficient'] is True
        assert result['best_price'] == 0.50
        assert result['slippage'] < 0.02

    def test_check_liquidity_insufficient_depth(self):
        """Should report insufficient depth"""
        client = PolymarketClient()
        book = OrderBook(
            token_id='token123',
            bids=[],
            asks=[OrderBookEntry(0.50, 10)],
            timestamp=datetime.now()
        )

        result = client.check_liquidity(book, 1000, OrderSide.BUY)

        assert result['sufficient'] is False
        assert result['reason'] == 'Insufficient depth'

    def test_check_liquidity_empty_book(self):
        """Should handle empty order book"""
        client = PolymarketClient()
        book = OrderBook(
            token_id='token123',
            bids=[],
            asks=[],
            timestamp=datetime.now()
        )

        result = client.check_liquidity(book, 100, OrderSide.BUY)

        assert result['sufficient'] is False
        assert result['reason'] == 'No liquidity'


class TestOrder:
    """Test Order dataclass"""

    def test_from_api(self):
        """Should create Order from API response"""
        data = {
            'id': 'order123',
            'asset_id': 'token456',
            'side': 'BUY',
            'price': '0.55',
            'original_size': '100',
            'size_matched': '50',
            'status': 'LIVE',
            'type': 'GTC',
            'created_at': '2025-01-15T10:30:00',
        }

        order = Order.from_api(data)

        assert order.id == 'order123'
        assert order.token_id == 'token456'
        assert order.side == OrderSide.BUY
        assert order.price == 0.55
        assert order.size == 100.0
        assert order.status == OrderStatus.LIVE


class TestPosition:
    """Test Position dataclass"""

    def test_position_fields(self):
        """Should store position fields correctly"""
        pos = Position(
            token_id='token123',
            outcome='YES',
            size=100.0,
            avg_price=0.45,
            realized_pnl=5.0,
            unrealized_pnl=10.0,
        )

        assert pos.token_id == 'token123'
        assert pos.outcome == 'YES'
        assert pos.size == 100.0
        assert pos.avg_price == 0.45
        assert pos.realized_pnl == 5.0
        assert pos.unrealized_pnl == 10.0


class TestAuthenticationHeaders:
    """Test authentication header generation"""

    def test_get_auth_headers_no_key(self):
        """Should return empty headers if no API key"""
        client = PolymarketClient(api_key=None)
        headers = client._get_auth_headers('GET', '/test')
        assert headers == {}

    def test_get_auth_headers_with_key(self):
        """Should generate auth headers with API key"""
        client = PolymarketClient(
            api_key='test_key',
            api_secret='test_secret'
        )
        headers = client._get_auth_headers('GET', '/test')

        assert 'POLY_API_KEY' in headers
        assert 'POLY_TIMESTAMP' in headers
        assert 'POLY_SIGNATURE' in headers
        assert headers['POLY_API_KEY'] == 'test_key'

    def test_generate_signature(self):
        """Should generate HMAC signature"""
        client = PolymarketClient(
            api_key='test_key',
            api_secret='test_secret'
        )

        signature = client._generate_signature(
            timestamp='1234567890',
            method='GET',
            path='/test',
            body=''
        )

        assert isinstance(signature, str)
        assert len(signature) == 64  # SHA256 hex digest length

    def test_generate_signature_requires_secret(self):
        """Should raise if API secret not set"""
        client = PolymarketClient(api_key='test_key', api_secret=None)

        with pytest.raises(ValueError, match="API secret required"):
            client._generate_signature('123', 'GET', '/test', '')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
