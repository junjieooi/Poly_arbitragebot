"""
Unit tests for risk_management module
"""

import pytest
import sys
sys.path.insert(0, '..')

from risk_management import (
    Position,
    KellyCriterion,
    RiskManager,
    PortfolioOptimizer,
    BettingSimulator,
    validate_probability,
    validate_positive,
    validate_price,
)


class TestValidation:
    """Test input validation functions"""

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

    def test_validate_positive_valid(self):
        """Positive values should not raise"""
        validate_positive(0.01, "test")
        validate_positive(100, "test")

    def test_validate_positive_invalid(self):
        """Non-positive values should raise ValueError"""
        with pytest.raises(ValueError):
            validate_positive(0, "test")
        with pytest.raises(ValueError):
            validate_positive(-1, "test")


class TestPosition:
    """Test Position dataclass"""

    @pytest.fixture
    def winning_position(self):
        return Position(
            market_id="m1",
            outcome="YES",
            shares=100,
            avg_price=0.40,
            current_price=0.60,
            fair_value=0.65
        )

    @pytest.fixture
    def losing_position(self):
        return Position(
            market_id="m2",
            outcome="NO",
            shares=100,
            avg_price=0.50,
            current_price=0.30,
            fair_value=0.35
        )

    def test_cost_basis(self, winning_position):
        """Cost basis should be shares * avg_price"""
        assert winning_position.cost_basis == pytest.approx(40.0)

    def test_current_value(self, winning_position):
        """Current value should be shares * current_price"""
        assert winning_position.current_value == pytest.approx(60.0)

    def test_unrealized_pnl_profit(self, winning_position):
        """Unrealized P&L should be positive for winner"""
        assert winning_position.unrealized_pnl == pytest.approx(20.0)

    def test_unrealized_pnl_loss(self, losing_position):
        """Unrealized P&L should be negative for loser"""
        assert losing_position.unrealized_pnl == pytest.approx(-20.0)

    def test_pnl_pct(self, winning_position, losing_position):
        """P&L percentage should be calculated correctly"""
        assert winning_position.pnl_pct == pytest.approx(50.0)
        assert losing_position.pnl_pct == pytest.approx(-40.0)


class TestKellyCriterion:
    """Test Kelly Criterion calculations"""

    def test_full_kelly_positive_edge(self):
        """Positive edge should give positive Kelly"""
        # 60% win rate at even odds
        kelly = KellyCriterion.full_kelly(0.60, 1.0)
        assert kelly == pytest.approx(0.20, abs=0.01)

    def test_full_kelly_no_edge(self):
        """No edge should give zero Kelly"""
        kelly = KellyCriterion.full_kelly(0.50, 1.0)
        assert kelly == pytest.approx(0.0, abs=0.01)

    def test_full_kelly_negative_edge(self):
        """Negative edge should give zero Kelly (clamped)"""
        kelly = KellyCriterion.full_kelly(0.40, 1.0)
        assert kelly == 0.0

    def test_full_kelly_better_odds(self):
        """Better odds increase Kelly"""
        # 50% at 2:1 odds
        kelly = KellyCriterion.full_kelly(0.50, 2.0)
        assert kelly == pytest.approx(0.25, abs=0.01)

    def test_fractional_kelly(self):
        """Fractional Kelly should be scaled"""
        full = KellyCriterion.full_kelly(0.60, 1.0)
        quarter = KellyCriterion.fractional_kelly(0.60, 1.0, fraction=0.25)

        assert quarter == pytest.approx(full * 0.25, abs=0.01)

    def test_kelly_from_prices_positive_edge(self):
        """Underpriced market should give positive Kelly"""
        kelly = KellyCriterion.kelly_from_prices(
            market_price=0.40,
            fair_value=0.60,
            fraction=0.25
        )
        assert kelly > 0

    def test_kelly_from_prices_no_edge(self):
        """Fair price should give near-zero Kelly"""
        kelly = KellyCriterion.kelly_from_prices(
            market_price=0.50,
            fair_value=0.50,
            fraction=0.25
        )
        assert kelly == pytest.approx(0, abs=0.01)

    def test_kelly_from_prices_with_confidence(self):
        """Lower confidence should reduce Kelly"""
        high_conf = KellyCriterion.kelly_from_prices(
            market_price=0.40,
            fair_value=0.60,
            fraction=0.25,
            confidence=1.0
        )
        low_conf = KellyCriterion.kelly_from_prices(
            market_price=0.40,
            fair_value=0.60,
            fraction=0.25,
            confidence=0.5
        )

        assert low_conf < high_conf

    def test_kelly_invalid_inputs(self):
        """Invalid inputs should raise"""
        with pytest.raises(ValueError):
            KellyCriterion.kelly_from_prices(0.0, 0.5)  # Invalid price
        with pytest.raises(ValueError):
            KellyCriterion.kelly_from_prices(0.5, 1.5)  # Invalid fair value
        with pytest.raises(ValueError):
            KellyCriterion.kelly_from_prices(0.5, 0.6, fraction=1.5)  # Invalid fraction


class TestRiskManager:
    """Test RiskManager class"""

    @pytest.fixture
    def risk_manager(self):
        return RiskManager(
            max_position_size=0.10,
            max_market_exposure=0.25,
            stop_loss_pct=-0.25,
            daily_loss_limit=-0.05,
            max_drawdown_limit=-0.20
        )

    def test_initialization_valid(self, risk_manager):
        """Valid parameters should initialize correctly"""
        assert risk_manager.max_position_size == 0.10
        assert risk_manager.stop_loss_pct == -0.25

    def test_initialization_invalid(self):
        """Invalid parameters should raise"""
        with pytest.raises(ValueError):
            RiskManager(max_position_size=1.5)  # > 1
        with pytest.raises(ValueError):
            RiskManager(stop_loss_pct=0.25)  # Positive
        with pytest.raises(ValueError):
            RiskManager(daily_loss_limit=0.10)  # Positive

    def test_calculate_position_size(self, risk_manager):
        """Position size should respect limits"""
        bankroll = 10000
        kelly = 0.15  # 15% Kelly suggests

        shares = risk_manager.calculate_position_size(
            bankroll=bankroll,
            kelly_fraction=kelly,
            market_price=0.50
        )

        # Max position is 10% = $1000, at $0.50 = 2000 shares max
        # Kelly suggests 15% = $1500, at $0.50 = 3000 shares
        # Should be capped at 2000
        assert shares == 2000

    def test_calculate_position_size_kelly_smaller(self, risk_manager):
        """Kelly should be used when smaller than max"""
        bankroll = 10000
        kelly = 0.05  # 5% Kelly suggests

        shares = risk_manager.calculate_position_size(
            bankroll=bankroll,
            kelly_fraction=kelly,
            market_price=0.50
        )

        # Kelly suggests 5% = $500, at $0.50 = 1000 shares
        assert shares == 1000

    def test_check_daily_loss_limit(self, risk_manager):
        """Daily loss should trigger halt"""
        risk_manager.start_trading_day(10000)

        # No halt at start
        result = risk_manager.check_daily_loss_limit(9800)
        assert result['halt_trading'] is False

        # Halt at limit
        result = risk_manager.check_daily_loss_limit(9400)  # -6% > -5%
        assert result['halt_trading'] is True

    def test_check_drawdown_limit(self, risk_manager):
        """Drawdown should trigger halt"""
        risk_manager.peak_portfolio_value = 10000

        # No halt at small drawdown
        result = risk_manager.check_drawdown_limit(9000)  # -10%
        assert result['halt_trading'] is False

        # Halt at limit
        result = risk_manager.check_drawdown_limit(7800)  # -22% > -20%
        assert result['halt_trading'] is True

    def test_stop_loss_recommendations(self, risk_manager):
        """Should recommend closing positions at stop loss"""
        positions = [
            Position("m1", "YES", 100, 0.50, 0.35, 0.60),  # -30%
            Position("m2", "NO", 100, 0.50, 0.55, 0.45),   # +10%
        ]

        recommendations = risk_manager.get_stop_loss_recommendations(
            positions, portfolio_value=10000
        )

        # Only m1 should be stopped
        assert len(recommendations) == 1
        assert recommendations[0]['market_id'] == "m1"
        assert recommendations[0]['action'] == 'CLOSE'

    def test_should_allow_trade(self, risk_manager):
        """Should check if trading is allowed"""
        allowed, reason = risk_manager.should_allow_trade()
        assert allowed is True

        # Halt trading
        risk_manager.trading_halted = True
        risk_manager.halt_reason = "Test halt"

        allowed, reason = risk_manager.should_allow_trade()
        assert allowed is False
        assert "Test halt" in reason


class TestPortfolioOptimizer:
    """Test PortfolioOptimizer class"""

    def test_optimize_allocations(self):
        """Should allocate by Sharpe ratio"""
        opportunities = [
            {'market_id': 'm1', 'expected_return': 0.20, 'volatility': 0.10},
            {'market_id': 'm2', 'expected_return': 0.10, 'volatility': 0.10},
        ]

        result = PortfolioOptimizer.optimize_allocations(opportunities, bankroll=10000)

        # m1 has higher Sharpe, should get more allocation
        m1_row = result[result['market'] == 'm1'].iloc[0]
        m2_row = result[result['market'] == 'm2'].iloc[0]

        assert m1_row['allocation'] > m2_row['allocation']

    def test_optimize_empty_list(self):
        """Empty opportunities should return empty DataFrame"""
        result = PortfolioOptimizer.optimize_allocations([], bankroll=10000)
        assert result.empty


class TestBettingSimulator:
    """Test BettingSimulator class"""

    @pytest.fixture
    def simulator(self):
        return BettingSimulator(initial_bankroll=10000)

    def test_simulate_winning_bet(self, simulator):
        """Winning bet should increase bankroll"""
        result = simulator.simulate_bet(
            bet_size=100,
            win_prob=0.6,
            odds=1.0,
            outcome=True
        )

        assert result['outcome'] is True
        assert result['profit'] == 100
        assert simulator.bankroll == 10100

    def test_simulate_losing_bet(self, simulator):
        """Losing bet should decrease bankroll"""
        result = simulator.simulate_bet(
            bet_size=100,
            win_prob=0.6,
            odds=1.0,
            outcome=False
        )

        assert result['outcome'] is False
        assert result['profit'] == -100
        assert simulator.bankroll == 9900

    def test_simulate_sequence_kelly(self, simulator):
        """Kelly sizing should handle sequence"""
        opportunities = [
            {'win_prob': 0.6, 'odds': 1.0, 'outcome': True},
            {'win_prob': 0.6, 'odds': 1.0, 'outcome': False},
            {'win_prob': 0.6, 'odds': 1.0, 'outcome': True},
        ]

        df = simulator.simulate_sequence(opportunities, sizing_method='kelly')

        assert len(df) == 3
        assert 'bankroll' in df.columns
        assert df['bankroll'].iloc[-1] != 10000  # Should have changed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
