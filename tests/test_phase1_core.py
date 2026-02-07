"""
Unit tests for Phase 1 core components (no web3 dependency).

Tests:
- WebSocket Feed Handler (order book management)
- Simple Arbitrage Detector (opportunity detection)
"""

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import components
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.websocket_feed import (
    OrderBook,
    OrderBookLevel,
    OrderBookChangeEvent,
    WebSocketFeedHandler,
    ConnectionState
)
from src.detection.simple_arbitrage import (
    ArbitrageOpportunity,
    MarketPair,
    ArbitrageDatabase,
    SimpleArbitrageDetector
)


# ============================================================================
# WebSocket Feed Handler Tests
# ============================================================================

class TestOrderBookLevel:
    """Tests for OrderBookLevel dataclass."""

    def test_creation(self):
        level = OrderBookLevel(price=0.55, size=100.0)
        assert level.price == 0.55
        assert level.size == 100.0

    def test_comparison(self):
        level1 = OrderBookLevel(price=0.50, size=50.0)
        level2 = OrderBookLevel(price=0.55, size=100.0)
        assert level1 < level2


class TestOrderBook:
    """Tests for OrderBook dataclass."""

    def test_empty_book(self):
        book = OrderBook(token_id="test_token")
        assert book.best_bid is None
        assert book.best_ask is None
        assert book.spread is None
        assert book.mid_price is None

    def test_with_levels(self):
        book = OrderBook(token_id="test_token")
        book.bids = [
            OrderBookLevel(price=0.50, size=100.0),
            OrderBookLevel(price=0.49, size=200.0)
        ]
        book.asks = [
            OrderBookLevel(price=0.52, size=150.0),
            OrderBookLevel(price=0.53, size=250.0)
        ]

        assert book.best_bid == 0.50
        assert book.best_ask == 0.52
        assert book.spread == pytest.approx(0.02)
        assert book.mid_price == 0.51

    def test_update_level_add(self):
        book = OrderBook(token_id="test_token")
        book.update_level("bid", 0.50, 100.0)

        assert len(book.bids) == 1
        assert book.bids[0].price == 0.50
        assert book.bids[0].size == 100.0

    def test_update_level_modify(self):
        book = OrderBook(token_id="test_token")
        book.update_level("bid", 0.50, 100.0)
        book.update_level("bid", 0.50, 200.0)

        assert len(book.bids) == 1
        assert book.bids[0].size == 200.0

    def test_update_level_remove(self):
        book = OrderBook(token_id="test_token")
        book.update_level("bid", 0.50, 100.0)
        book.update_level("bid", 0.50, 0.0)

        assert len(book.bids) == 0

    def test_set_snapshot(self):
        book = OrderBook(token_id="test_token")
        book.set_snapshot(
            bids=[(0.50, 100.0), (0.49, 200.0)],
            asks=[(0.52, 150.0), (0.53, 250.0)],
            timestamp=1234567890
        )

        assert len(book.bids) == 2
        assert len(book.asks) == 2
        assert book.timestamp == 1234567890
        # Bids should be descending
        assert book.bids[0].price > book.bids[1].price
        # Asks should be ascending
        assert book.asks[0].price < book.asks[1].price

    def test_to_dict(self):
        book = OrderBook(token_id="test_token")
        book.set_snapshot(
            bids=[(0.50, 100.0)],
            asks=[(0.52, 150.0)],
            timestamp=123
        )

        d = book.to_dict()
        assert d["bids"] == [(0.50, 100.0)]
        assert d["asks"] == [(0.52, 150.0)]
        assert d["timestamp"] == 123


class TestWebSocketFeedHandler:
    """Tests for WebSocketFeedHandler."""

    def test_initialization(self):
        handler = WebSocketFeedHandler()
        assert handler.state == ConnectionState.DISCONNECTED
        assert len(handler.order_books) == 0

    def test_get_order_book(self):
        handler = WebSocketFeedHandler()
        handler._order_books["token1"] = OrderBook(token_id="token1")

        book = handler.get_order_book("token1")
        assert book is not None
        assert book.token_id == "token1"

        assert handler.get_order_book("unknown") is None

    def test_callbacks_registered(self):
        handler = WebSocketFeedHandler()

        callback_called = []

        def my_callback(event):
            callback_called.append(event)

        handler.on_book_change(my_callback)

        assert len(handler._change_callbacks) == 1

    def test_significant_change_detection(self):
        handler = WebSocketFeedHandler()
        events = []

        def on_change(event):
            events.append(event)

        handler.on_book_change(on_change)
        handler._order_books["token1"] = OrderBook(token_id="token1")

        # 2% change should trigger
        handler._check_significant_change("token1", "bid", 0.50, 0.51)
        assert len(events) == 1
        assert events[0].change_pct == pytest.approx(0.02)

        # 1% change should not trigger
        handler._check_significant_change("token1", "bid", 0.50, 0.505)
        assert len(events) == 1  # No new event


# ============================================================================
# Simple Arbitrage Detector Tests
# ============================================================================

class TestArbitrageOpportunity:
    """Tests for ArbitrageOpportunity dataclass."""

    def test_valid_opportunity(self):
        opp = ArbitrageOpportunity(
            market_id="market1",
            arb_type="buy",
            yes_price=0.45,
            no_price=0.48,
            yes_size=100.0,
            no_size=150.0,
            combined_price=0.93,
            guaranteed_profit=0.07,
            max_position_size=100.0,
            total_potential_profit=7.0
        )

        assert opp.is_valid is True

    def test_invalid_low_profit(self):
        opp = ArbitrageOpportunity(
            market_id="market1",
            arb_type="buy",
            yes_price=0.49,
            no_price=0.49,
            yes_size=100.0,
            no_size=150.0,
            combined_price=0.98,
            guaranteed_profit=0.02,  # Below $0.05 threshold
            max_position_size=100.0,
            total_potential_profit=2.0
        )

        assert opp.is_valid is False

    def test_str_representation(self):
        opp = ArbitrageOpportunity(
            market_id="market1",
            arb_type="buy",
            yes_price=0.45,
            no_price=0.48,
            yes_size=100.0,
            no_size=150.0,
            combined_price=0.93,
            guaranteed_profit=0.07,
            max_position_size=100.0,
            total_potential_profit=7.0
        )

        s = str(opp)
        assert "BUY ARB" in s
        assert "YES=0.4500" in s
        assert "NO=0.4800" in s


class TestMarketPair:
    """Tests for MarketPair."""

    def test_not_ready_without_books(self):
        market = MarketPair(
            market_id="m1",
            question="Test?",
            yes_token_id="yes",
            no_token_id="no"
        )

        assert market.is_ready is False

    def test_ready_with_books(self):
        market = MarketPair(
            market_id="m1",
            question="Test?",
            yes_token_id="yes",
            no_token_id="no"
        )

        # Create order books with bids
        yes_book = OrderBook(token_id="yes")
        yes_book.bids = [OrderBookLevel(0.50, 100.0)]
        yes_book.asks = [OrderBookLevel(0.52, 100.0)]

        no_book = OrderBook(token_id="no")
        no_book.bids = [OrderBookLevel(0.48, 100.0)]
        no_book.asks = [OrderBookLevel(0.50, 100.0)]

        market.yes_book = yes_book
        market.no_book = no_book

        assert market.is_ready is True


class TestArbitrageDatabase:
    """Tests for ArbitrageDatabase."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test.db"
        return ArbitrageDatabase(str(db_path))

    def test_save_and_retrieve(self, db):
        opp = ArbitrageOpportunity(
            market_id="market1",
            arb_type="buy",
            yes_price=0.45,
            no_price=0.48,
            combined_price=0.93,
            guaranteed_profit=0.07,
            yes_size=100.0,
            no_size=150.0,
            max_position_size=100.0,
            total_potential_profit=7.0
        )

        # Save
        opp_id = db.save_opportunity(opp)
        assert opp_id > 0

        # Retrieve
        opps = db.get_opportunities(hours=1)
        assert len(opps) == 1
        assert opps[0].market_id == "market1"
        assert opps[0].guaranteed_profit == 0.07

    def test_filter_by_profit(self, db):
        # Save two opportunities
        opp1 = ArbitrageOpportunity(
            market_id="m1",
            arb_type="buy",
            yes_price=0.45,
            no_price=0.48,
            combined_price=0.93,
            guaranteed_profit=0.07,
            yes_size=100.0,
            no_size=100.0,
            max_position_size=100.0,
            total_potential_profit=7.0
        )
        opp2 = ArbitrageOpportunity(
            market_id="m2",
            arb_type="sell",
            yes_price=0.55,
            no_price=0.55,
            combined_price=1.10,
            guaranteed_profit=0.10,
            yes_size=100.0,
            no_size=100.0,
            max_position_size=100.0,
            total_potential_profit=10.0
        )

        db.save_opportunity(opp1)
        db.save_opportunity(opp2)

        # Filter by min profit
        opps = db.get_opportunities(hours=1, min_profit=0.08)
        assert len(opps) == 1
        assert opps[0].market_id == "m2"

    def test_stats(self, db):
        # Save some opportunities
        for profit in [0.05, 0.07, 0.10, 0.15, 0.20]:
            opp = ArbitrageOpportunity(
                market_id=f"m{profit}",
                arb_type="buy",
                yes_price=0.45,
                no_price=(1.0 - profit - 0.45),
                combined_price=1.0 - profit,
                guaranteed_profit=profit,
                yes_size=100.0,
                no_size=100.0,
                max_position_size=100.0,
                total_potential_profit=profit * 100
            )
            db.save_opportunity(opp)

        stats = db.get_stats(hours=1)
        assert stats["count"] == 5
        assert stats["median_profit"] == 0.10
        assert stats["max_profit"] == 0.20


class TestSimpleArbitrageDetector:
    """Tests for SimpleArbitrageDetector."""

    @pytest.fixture
    def detector(self, tmp_path):
        """Create detector with temporary database."""
        db_path = tmp_path / "test.db"
        # Create a mock feed handler
        mock_feed = MagicMock()
        mock_feed.on_snapshot = MagicMock()
        mock_feed._order_books = {}
        return SimpleArbitrageDetector(
            feed_handler=mock_feed,
            db_path=str(db_path)
        )

    def test_add_market(self, detector):
        detector.add_market(
            market_id="m1",
            yes_token_id="yes1",
            no_token_id="no1",
            question="Test question?"
        )

        assert "m1" in detector._markets
        assert "yes1" in detector._token_to_market
        assert "no1" in detector._token_to_market

    def test_check_buy_arbitrage_positive(self, detector):
        """Test detection of buy arbitrage."""
        market = MarketPair(
            market_id="m1",
            question="Test?",
            yes_token_id="yes",
            no_token_id="no"
        )

        # Create books where buying both costs < $1
        yes_book = OrderBook(token_id="yes")
        yes_book.asks = [OrderBookLevel(0.45, 100.0)]

        no_book = OrderBook(token_id="no")
        no_book.asks = [OrderBookLevel(0.48, 150.0)]

        market.yes_book = yes_book
        market.no_book = no_book

        opp = detector._check_buy_arbitrage(market)

        assert opp is not None
        assert opp.arb_type == "buy"
        assert opp.combined_price == 0.93
        assert opp.guaranteed_profit == pytest.approx(0.07)
        assert opp.max_position_size == 100.0  # Min of 100 and 150

    def test_check_buy_arbitrage_negative(self, detector):
        """Test no detection when no arbitrage."""
        market = MarketPair(
            market_id="m1",
            question="Test?",
            yes_token_id="yes",
            no_token_id="no"
        )

        # Create books where buying both costs >= $1
        yes_book = OrderBook(token_id="yes")
        yes_book.asks = [OrderBookLevel(0.50, 100.0)]

        no_book = OrderBook(token_id="no")
        no_book.asks = [OrderBookLevel(0.50, 150.0)]

        market.yes_book = yes_book
        market.no_book = no_book

        opp = detector._check_buy_arbitrage(market)
        assert opp is None  # No opportunity

    def test_check_sell_arbitrage_positive(self, detector):
        """Test detection of sell arbitrage."""
        market = MarketPair(
            market_id="m1",
            question="Test?",
            yes_token_id="yes",
            no_token_id="no"
        )

        # Create books where selling both gives > $1
        yes_book = OrderBook(token_id="yes")
        yes_book.bids = [OrderBookLevel(0.55, 100.0)]

        no_book = OrderBook(token_id="no")
        no_book.bids = [OrderBookLevel(0.52, 150.0)]

        market.yes_book = yes_book
        market.no_book = no_book

        opp = detector._check_sell_arbitrage(market)

        assert opp is not None
        assert opp.arb_type == "sell"
        assert opp.combined_price == 1.07
        assert opp.guaranteed_profit == pytest.approx(0.07)

    def test_get_stats(self, detector):
        stats = detector.get_stats()

        assert "running" in stats
        assert "markets_monitored" in stats
        assert "count" in stats


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests for Phase 1 components."""

    @pytest.fixture
    def mock_feed_handler(self):
        """Create mock feed handler."""
        handler = MagicMock(spec=WebSocketFeedHandler)
        handler._order_books = {}
        handler.on_snapshot = MagicMock()
        handler.start = AsyncMock()
        handler.stop = AsyncMock()
        return handler

    def test_detector_with_mock_feed(self, mock_feed_handler, tmp_path):
        """Test detector receives updates from feed handler."""
        detector = SimpleArbitrageDetector(
            feed_handler=mock_feed_handler,
            db_path=str(tmp_path / "test.db")
        )

        detector.add_market("m1", "yes1", "no1", "Test?")

        # Simulate order book updates
        yes_book = OrderBook(token_id="yes1")
        yes_book.asks = [OrderBookLevel(0.45, 100.0)]
        yes_book.bids = [OrderBookLevel(0.44, 100.0)]

        no_book = OrderBook(token_id="no1")
        no_book.asks = [OrderBookLevel(0.48, 100.0)]
        no_book.bids = [OrderBookLevel(0.47, 100.0)]

        # Manually update the market pair's books
        detector._markets["m1"].yes_book = yes_book
        detector._markets["m1"].no_book = no_book

        # Run check
        opportunities = []
        detector.on_opportunity(lambda opp: opportunities.append(opp))
        detector._check_all_markets()

        # Should find buy arbitrage (0.45 + 0.48 = 0.93)
        assert len(opportunities) == 1
        assert opportunities[0].arb_type == "buy"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
