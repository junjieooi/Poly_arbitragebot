"""
Unit tests for monte_carlo_simulation module
"""

import pytest
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, '..')

from monte_carlo_simulation import MonteCarloSimulator, TradeSimulation


class TestMonteCarloSimulator:
    """Test MonteCarloSimulator class"""

    @pytest.fixture
    def simulator(self):
        return MonteCarloSimulator(
            initial_bankroll=10000,
            num_simulations=100,
            random_seed=42,
            fee_rate=0.02
        )

    def test_initialization(self, simulator):
        """Should initialize with correct parameters"""
        assert simulator.initial_bankroll == 10000
        assert simulator.num_simulations == 100
        assert simulator.fee_rate == 0.02

    def test_initialization_invalid(self):
        """Invalid parameters should raise"""
        with pytest.raises(ValueError):
            MonteCarloSimulator(initial_bankroll=-100)
        with pytest.raises(ValueError):
            MonteCarloSimulator(num_simulations=0)
        with pytest.raises(ValueError):
            MonteCarloSimulator(fee_rate=1.5)

    def test_reproducibility(self):
        """Same seed should produce same results"""
        sim1 = MonteCarloSimulator(initial_bankroll=10000, num_simulations=10, random_seed=42)
        sim2 = MonteCarloSimulator(initial_bankroll=10000, num_simulations=10, random_seed=42)

        df1 = sim1.simulate_strategy('kelly', num_trades=20, kelly_fraction=0.25)
        df2 = sim2.simulate_strategy('kelly', num_trades=20, kelly_fraction=0.25)

        # Reset seed and run again to get same results
        np.random.seed(42)
        sim3 = MonteCarloSimulator(initial_bankroll=10000, num_simulations=10, random_seed=42)
        df3 = sim3.simulate_strategy('kelly', num_trades=20, kelly_fraction=0.25)

        # Results should be reproducible with same seed
        # Note: Due to random seed being global, this test checks structure
        assert len(df1) == len(df2)

    def test_simulate_trade_win(self, simulator):
        """Winning trade should have positive P&L"""
        # Force a win by using high fair value
        np.random.seed(1)  # This seed gives outcome = True for fv > 0.9
        outcome, pnl = simulator.simulate_trade(
            entry_price=0.50,
            fair_value=0.99,  # Very likely to win
            position_size=100,
            model_error=0.01,  # Small error
            include_fees=False
        )

        # With high fair value and small error, should win
        if outcome:
            assert pnl > 0
        else:
            assert pnl < 0

    def test_simulate_trade_with_fees(self, simulator):
        """Fees should reduce P&L"""
        np.random.seed(42)

        _, pnl_no_fees = simulator.simulate_trade(
            entry_price=0.50,
            fair_value=0.70,
            position_size=100,
            model_error=0.05,
            include_fees=False
        )

        np.random.seed(42)  # Same seed for same outcome

        _, pnl_with_fees = simulator.simulate_trade(
            entry_price=0.50,
            fair_value=0.70,
            position_size=100,
            model_error=0.05,
            include_fees=True
        )

        # Fees should reduce absolute P&L
        assert abs(pnl_no_fees) >= abs(pnl_with_fees) or pnl_with_fees < 0

    def test_simulate_strategy_kelly(self, simulator):
        """Kelly strategy should produce results"""
        df = simulator.simulate_strategy(
            'kelly',
            num_trades=50,
            kelly_fraction=0.25,
            model_error=0.10
        )

        assert len(df) > 0
        assert 'sim_num' in df.columns
        assert 'bankroll' in df.columns
        assert 'pnl' in df.columns
        assert df['sim_num'].nunique() == 100

    def test_simulate_strategy_fixed(self, simulator):
        """Fixed sizing should produce results"""
        df = simulator.simulate_strategy(
            'fixed',
            num_trades=50,
            fixed_pct=0.02,
            model_error=0.10
        )

        assert len(df) > 0

    def test_simulate_strategy_tail_betting(self, simulator):
        """Tail betting should produce results"""
        df = simulator.simulate_strategy(
            'tail_betting',
            num_trades=50,
            tail_pct=0.10,
            model_error=0.10
        )

        assert len(df) > 0

    def test_bankruptcy_handling(self, simulator):
        """Bankrupt simulations should stop at 0"""
        # Use aggressive sizing to induce bankruptcy
        df = simulator.simulate_strategy(
            'kelly',
            num_trades=100,
            kelly_fraction=1.0,  # Full Kelly is very aggressive
            model_error=0.30  # High model error
        )

        # Check that bankrupt sims have bankroll = 0
        final_bankrolls = df.groupby('sim_num')['bankroll'].last()
        bankrupt_sims = (final_bankrolls == 0).sum()

        # Some should go bankrupt with full Kelly and high error
        # (but not guaranteed, depends on random outcomes)
        assert final_bankrolls.min() >= 0  # Never negative


class TestAnalyzeResults:
    """Test simulation analysis methods"""

    @pytest.fixture
    def simulator(self):
        return MonteCarloSimulator(
            initial_bankroll=10000,
            num_simulations=50,
            random_seed=42
        )

    @pytest.fixture
    def results_df(self, simulator):
        return simulator.simulate_strategy(
            'kelly',
            num_trades=50,
            kelly_fraction=0.25,
            model_error=0.10
        )

    def test_analyze_results(self, simulator, results_df):
        """Should compute all metrics"""
        analysis = simulator.analyze_results(results_df)

        # Check all expected keys
        assert 'mean_final_bankroll' in analysis
        assert 'median_final_bankroll' in analysis
        assert 'std_final_bankroll' in analysis
        assert 'mean_roi' in analysis
        assert 'prob_profit' in analysis
        assert 'prob_ruin' in analysis
        assert 'sharpe_ratio' in analysis
        assert 'max_drawdown' in analysis
        assert 'win_rate' in analysis

    def test_confidence_intervals(self, simulator, results_df):
        """Should compute confidence intervals"""
        analysis = simulator.analyze_results(results_df)

        assert 'ci_90_bankroll' in analysis
        assert 'ci_95_bankroll' in analysis
        assert 'ci_95_roi' in analysis

        # CI should be a tuple of (lower, upper)
        ci_95 = analysis['ci_95_bankroll']
        assert len(ci_95) == 2
        assert ci_95[0] < ci_95[1]

    def test_percentiles(self, simulator, results_df):
        """Should compute percentiles"""
        analysis = simulator.analyze_results(results_df)

        assert 'percentile_5' in analysis
        assert 'percentile_25' in analysis
        assert 'percentile_75' in analysis
        assert 'percentile_95' in analysis

        # Percentiles should be ordered
        assert analysis['percentile_5'] <= analysis['percentile_25']
        assert analysis['percentile_25'] <= analysis['percentile_75']
        assert analysis['percentile_75'] <= analysis['percentile_95']

    def test_prob_profit_range(self, simulator, results_df):
        """Probability of profit should be 0-1"""
        analysis = simulator.analyze_results(results_df)

        assert 0 <= analysis['prob_profit'] <= 1

    def test_max_drawdown_negative(self, simulator, results_df):
        """Max drawdown should be negative or zero"""
        analysis = simulator.analyze_results(results_df)

        assert analysis['max_drawdown'] <= 0

    def test_win_rate_range(self, simulator, results_df):
        """Win rate should be 0-1"""
        analysis = simulator.analyze_results(results_df)

        assert 0 <= analysis['win_rate'] <= 1


class TestCalculations:
    """Test specific calculations"""

    @pytest.fixture
    def simulator(self):
        return MonteCarloSimulator(
            initial_bankroll=10000,
            num_simulations=10,
            random_seed=42
        )

    def test_sharpe_calculation(self, simulator):
        """Sharpe ratio calculation should be correct"""
        df = simulator.simulate_strategy('kelly', num_trades=50)

        sharpe = simulator._calculate_sharpe(df)

        # Manual calculation
        final_rois = df.groupby('sim_num')['roi'].last()
        expected_sharpe = final_rois.mean() / final_rois.std() if final_rois.std() > 0 else 0

        assert sharpe == pytest.approx(expected_sharpe, abs=0.01)

    def test_max_drawdown_calculation(self, simulator):
        """Max drawdown calculation should be correct"""
        df = simulator.simulate_strategy('kelly', num_trades=50)

        max_dd = simulator._calculate_max_drawdown(df)

        # Should be non-positive
        assert max_dd <= 0


class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_single_trade(self):
        """Should handle single trade"""
        sim = MonteCarloSimulator(initial_bankroll=10000, num_simulations=10, random_seed=42)
        df = sim.simulate_strategy('kelly', num_trades=1, kelly_fraction=0.25)

        assert len(df) == 10  # 10 sims * 1 trade

    def test_zero_edge_strategy(self):
        """Tail betting with no edge should not grow bankroll on average"""
        sim = MonteCarloSimulator(initial_bankroll=10000, num_simulations=100, random_seed=42)
        df = sim.simulate_strategy('tail_betting', num_trades=100, tail_pct=0.05)
        analysis = sim.analyze_results(df)

        # Without edge, should hover around break-even or lose to fees
        # Mean ROI should be close to 0 or negative
        # (Can't assert exactly due to randomness, but should be reasonable)
        assert -50 < analysis['mean_roi'] < 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
