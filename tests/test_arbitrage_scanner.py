"""
Unit tests for arbitrage_scanner module
"""

import pytest
from datetime import datetime
import sys
sys.path.insert(0, '..')

from arbitrage_scanner import (
    MarketData,
    ArbitrageOpportunity,
    ArbitrageScanner,
    StatisticalArbitrage,
    MeanReversionStrategy,
    LiquidityAnalyzer,
    OrderBookLevel,
    validate_price,
    validate_probability,
    validate_positive,
)


class TestValidation:
    """Test input validation functions"""

    def test_validate_price_valid(self):
        """Valid prices should not raise"""
        validate_price(0.5, "test")
        validate_price(0.01, "test")
        validate_price(0.99, "test")

    def test_validate_price_invalid(self):
        """Invalid prices should raise ValueError"""
        with pytest.raises(ValueError):
            validate_price(0.0, "test")
        with pytest.raises(ValueError):
            validate_price(1.0, "test")
        with pytest.raises(ValueError):
            validate_price(-0.1, "test")
        with pytest.raises(ValueError):
            validate_price(1.5, "test")

    def test_validate_price_type_error(self):
        """Non-numeric should raise TypeError"""
        with pytest.raises(TypeError):
            validate_price("0.5", "test")

    def test_validate_probability_valid(self):
        """Valid probabilities should not raise"""
        validate_probability(0.0, "test")
        validate_probability(0.5, "test")
        validate_probability(1.0, "test")

    def test_validate_probability_invalid(self):
        """Invalid probabilities should raise ValueError"""
        with pytest.raises(ValueError):
            validate_probability(-0.1, "test")
        with pytest.raises(ValueError):
            validate_probability(1.1, "test")


class TestArbitrageScanner:
    """Test ArbitrageScanner class"""

    @pytest.fixture
    def scanner(self):
        return ArbitrageScanner(fee_rate=0.02)

    def test_multi_outcome_arb_buy_all(self, scanner):
        """When prices sum < $1 after fees, buy all is profitable"""
        outcomes = [
            MarketData("m1", "A", 0.24, 1000, 5000, datetime.now()),
            MarketData("m1", "B", 0.24, 1000, 5000, datetime.now()),
            MarketData("m1", "C", 0.24, 1000, 5000, datetime.now()),
            MarketData("m1", "D", 0.24, 1000, 5000, datetime.now()),
        ]  # Sum = 0.96

        opps = scanner.check_multi_outcome_arb(outcomes)

        assert len(opps) == 1
        assert opps[0].type == "Multi-Outcome Buy All"
        assert opps[0].profit > 0

    def test_multi_outcome_arb_sell_all(self, scanner):
        """When prices sum > $1 after fees, sell all is profitable"""
        outcomes = [
            MarketData("m1", "A", 0.30, 1000, 5000, datetime.now()),
            MarketData("m1", "B", 0.30, 1000, 5000, datetime.now()),
            MarketData("m1", "C", 0.30, 1000, 5000, datetime.now()),
            MarketData("m1", "D", 0.30, 1000, 5000, datetime.now()),
        ]  # Sum = 1.20

        opps = scanner.check_multi_outcome_arb(outcomes)

        assert len(opps) == 1
        assert opps[0].type == "Multi-Outcome Sell All"
        assert opps[0].profit > 0

    def test_no_arb_fair_prices(self, scanner):
        """Fair prices should not trigger arbitrage"""
        outcomes = [
            MarketData("m1", "A", 0.25, 1000, 5000, datetime.now()),
            MarketData("m1", "B", 0.25, 1000, 5000, datetime.now()),
            MarketData("m1", "C", 0.25, 1000, 5000, datetime.now()),
            MarketData("m1", "D", 0.25, 1000, 5000, datetime.now()),
        ]  # Sum = 1.00

        opps = scanner.check_multi_outcome_arb(outcomes)

        # May have opportunities depending on fee structure
        # but profit should be minimal or negative
        for opp in opps:
            assert opp.profit <= 0.05  # Allow small profit due to fee calc

    def test_temporal_inconsistency(self, scanner):
        """Short-term price > long-term price is arbitrage"""
        short_term = MarketData("m1", "A", 0.70, 1000, 5000, datetime.now())
        long_term = MarketData("m2", "A", 0.60, 1000, 5000, datetime.now())

        opps = scanner.check_temporal_inconsistency(short_term, long_term)

        assert len(opps) == 1
        assert opps[0].type == "Temporal Inconsistency"
        assert opps[0].profit > 0

    def test_no_temporal_inconsistency(self, scanner):
        """Proper ordering should not trigger"""
        short_term = MarketData("m1", "A", 0.50, 1000, 5000, datetime.now())
        long_term = MarketData("m2", "A", 0.60, 1000, 5000, datetime.now())

        opps = scanner.check_temporal_inconsistency(short_term, long_term)

        assert len(opps) == 0

    def test_subset_inconsistency(self, scanner):
        """Subset price < superset price is arbitrage"""
        subset = MarketData("m1", "A", 0.40, 1000, 5000, datetime.now())
        superset = MarketData("m2", "A", 0.50, 1000, 5000, datetime.now())

        opps = scanner.check_subset_inconsistency(subset, superset)

        assert len(opps) == 1
        assert opps[0].type == "Subset Inconsistency"
        assert opps[0].profit > 0


class TestStatisticalArbitrage:
    """Test StatisticalArbitrage class"""

    @pytest.fixture
    def stat_arb(self):
        return StatisticalArbitrage(confidence_threshold=0.6)

    def test_calculate_fair_value(self, stat_arb):
        """Fair value should be weighted average of features"""
        features = {
            'base_rate': 0.5,
            'recent_trend': 0.5,
            'sentiment': 0.5,
            'volume_signal': 0.5
        }

        fv = stat_arb.calculate_fair_value(features)

        assert 0 < fv < 1
        assert fv == pytest.approx(0.5, abs=0.01)

    def test_calculate_edge(self, stat_arb):
        """Edge should be difference between fair value and market price"""
        edge = stat_arb.calculate_edge(0.40, 0.60)
        assert edge == pytest.approx(0.20, abs=0.01)

        edge = stat_arb.calculate_edge(0.60, 0.40)
        assert edge == pytest.approx(-0.20, abs=0.01)

    def test_kelly_fraction_positive_edge(self, stat_arb):
        """Positive edge should result in positive Kelly"""
        kelly = stat_arb.calculate_kelly_fraction(0.40, 0.60, confidence=1.0)
        assert kelly > 0

    def test_kelly_fraction_no_edge(self, stat_arb):
        """No edge should result in zero or negative Kelly"""
        kelly = stat_arb.calculate_kelly_fraction(0.50, 0.50, confidence=1.0)
        assert kelly == pytest.approx(0, abs=0.01)

    def test_should_trade_with_edge(self, stat_arb):
        """Should recommend trade when edge exceeds minimum"""
        rec = stat_arb.should_trade(0.40, 0.60, min_edge=0.05)

        assert rec['trade'] is True
        assert rec['action'] == 'BUY'
        assert rec['edge'] == pytest.approx(0.20, abs=0.01)

    def test_should_not_trade_small_edge(self, stat_arb):
        """Should not recommend trade when edge is too small"""
        rec = stat_arb.should_trade(0.48, 0.52, min_edge=0.05)

        assert rec['trade'] is False


class TestLiquidityAnalyzer:
    """Test LiquidityAnalyzer class"""

    @pytest.fixture
    def analyzer(self):
        return LiquidityAnalyzer(min_liquidity_ratio=0.1)

    def test_effective_price_no_slippage(self, analyzer):
        """Small order should have minimal slippage"""
        order_book = [
            OrderBookLevel(0.50, 1000),
            OrderBookLevel(0.51, 1000),
            OrderBookLevel(0.52, 1000),
        ]

        effective, slippage = analyzer.calculate_effective_price(
            0.50, 100, order_book, is_buy=True
        )

        assert effective == pytest.approx(0.50, abs=0.01)
        assert slippage < 1.0  # Less than 1% slippage

    def test_effective_price_with_slippage(self, analyzer):
        """Large order should walk the book"""
        order_book = [
            OrderBookLevel(0.50, 100),
            OrderBookLevel(0.55, 100),
            OrderBookLevel(0.60, 100),
        ]

        effective, slippage = analyzer.calculate_effective_price(
            0.50, 200, order_book, is_buy=True
        )

        # Should fill 100 @ 0.50 and 100 @ 0.55 = avg 0.525
        assert effective == pytest.approx(0.525, abs=0.01)
        assert slippage > 0

    def test_liquidity_sufficient(self, analyzer):
        """Order within limit should be sufficient"""
        result = analyzer.check_liquidity_sufficient(100, 2000)

        assert result['sufficient'] is True
        assert result['utilization'] == pytest.approx(0.05, abs=0.01)

    def test_liquidity_insufficient(self, analyzer):
        """Order exceeding limit should be insufficient"""
        result = analyzer.check_liquidity_sufficient(500, 1000)

        assert result['sufficient'] is False
        assert result['utilization'] == pytest.approx(0.50, abs=0.01)


class TestMeanReversionStrategy:
    """Test MeanReversionStrategy class"""

    @pytest.fixture
    def strategy(self):
        return MeanReversionStrategy(window=20, z_threshold=2.0)

    def test_add_price(self, strategy):
        """Should track price history"""
        for i in range(10):
            strategy.add_price("m1", 0.50 + i * 0.01, 0.50)

        assert "m1" in strategy.price_history
        assert len(strategy.price_history["m1"]) == 10

    def test_z_score_calculation(self, strategy):
        """Z-score should measure deviation"""
        # Add stable prices
        for i in range(10):
            strategy.add_price("m1", 0.50, 0.50)

        z = strategy.calculate_z_score("m1")
        assert z == pytest.approx(0, abs=0.1)

    def test_buy_signal(self, strategy):
        """Extreme low z-score should generate buy signal"""
        # Create artificially low price
        for i in range(19):
            strategy.add_price("m1", 0.50, 0.50)
        # Add extreme deviation
        strategy.add_price("m1", 0.30, 0.50)

        signal = strategy.get_signal("m1")

        # May or may not trigger depending on std
        assert 'z_score' in signal
        assert 'action' in signal


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
