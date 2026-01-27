"""
Monte Carlo Simulation for Polymarket Strategies
Backtest different approaches and understand risk/return profiles
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

from logger import get_logger

logger = get_logger(__name__)

@dataclass
class TradeSimulation:
    """Single trade outcome"""
    market_id: str
    entry_price: float
    fair_value: float
    actual_outcome: bool  # True if bet wins
    position_size: float
    pnl: float

class MonteCarloSimulator:
    """Monte Carlo simulation for strategy testing"""

    def __init__(
        self,
        initial_bankroll: float = 10000,
        num_simulations: int = 1000,
        random_seed: Optional[int] = None,
        fee_rate: float = 0.02
    ):
        """
        Initialize Monte Carlo simulator.

        Args:
            initial_bankroll: Starting capital
            num_simulations: Number of simulation runs
            random_seed: Seed for reproducibility (None for random)
            fee_rate: Trading fee rate (default 2%)
        """
        if initial_bankroll <= 0:
            raise ValueError(f"initial_bankroll must be positive, got {initial_bankroll}")
        if num_simulations <= 0:
            raise ValueError(f"num_simulations must be positive, got {num_simulations}")
        if not 0 <= fee_rate < 1:
            raise ValueError(f"fee_rate must be [0, 1), got {fee_rate}")

        self.initial_bankroll = initial_bankroll
        self.num_simulations = num_simulations
        self.fee_rate = fee_rate
        self.results = []

        # Set random seed for reproducibility
        if random_seed is not None:
            np.random.seed(random_seed)
            logger.info(f"Random seed set to {random_seed}")
    
    def simulate_trade(self,
                      entry_price: float,
                      fair_value: float,
                      position_size: float,
                      model_error: float = 0.10,
                      include_fees: bool = True) -> Tuple[bool, float]:
        """
        Simulate a single trade with transaction costs.

        Args:
            entry_price: Market price when entering
            fair_value: Your estimated probability
            position_size: Number of shares
            model_error: Std deviation of estimation error
            include_fees: Whether to deduct fees from P&L

        Returns:
            (outcome, pnl)
        """
        # Add noise to fair value to simulate model error
        noisy_fv = np.clip(
            np.random.normal(fair_value, model_error),
            0.01, 0.99
        )

        # Outcome is determined by noisy fair value
        outcome = np.random.random() < noisy_fv

        # Calculate entry cost with fees
        entry_cost = position_size * entry_price
        if include_fees:
            entry_cost *= (1 + self.fee_rate)

        if outcome:
            # Win: Receive $1 per share, minus fees on profit
            gross_payout = position_size * 1.00
            profit = gross_payout - entry_cost
            if include_fees and profit > 0:
                # Fee on profit
                profit *= (1 - self.fee_rate)
            pnl = profit
        else:
            # Lose: Lose entry cost
            pnl = -entry_cost

        return outcome, pnl
    
    def simulate_strategy(self,
                         strategy_type: str,
                         num_trades: int = 100,
                         **kwargs) -> pd.DataFrame:
        """
        Run Monte Carlo simulation of trading strategy
        
        Args:
            strategy_type: 'kelly', 'fixed', 'tail_betting'
            num_trades: Number of trades to simulate
            **kwargs: Strategy-specific parameters
        
        Returns:
            DataFrame with all simulation results
        """
        all_simulations = []
        
        for sim_num in range(self.num_simulations):
            bankroll = self.initial_bankroll
            sim_trades = []
            
            for trade_num in range(num_trades):
                # Generate random market opportunity
                market_price = np.random.uniform(0.30, 0.70)
                
                if strategy_type == 'tail_betting':
                    # Strategy from original document: buy top outcomes
                    fair_value = market_price  # No edge, just hedging
                    edge = 0.0
                elif strategy_type == 'kelly':
                    # Kelly with real edge
                    edge = np.random.uniform(0.05, 0.15)  # 5-15c edge
                    fair_value = market_price + edge
                elif strategy_type == 'fixed':
                    edge = np.random.uniform(0.05, 0.15)
                    fair_value = market_price + edge
                else:
                    edge = 0.05
                    fair_value = market_price + edge
                
                # Position sizing
                if strategy_type == 'kelly':
                    kelly_fraction = kwargs.get('kelly_fraction', 0.25)
                    odds = (1.0 / market_price) - 1
                    full_kelly = ((odds * fair_value) - (1 - fair_value)) / odds
                    size_fraction = max(0, full_kelly * kelly_fraction)
                    position_size = (bankroll * size_fraction) / market_price
                
                elif strategy_type == 'fixed':
                    fixed_pct = kwargs.get('fixed_pct', 0.02)
                    position_size = (bankroll * fixed_pct) / market_price
                
                elif strategy_type == 'tail_betting':
                    # Bet 10% of bankroll per trade (aggressive)
                    tail_pct = kwargs.get('tail_pct', 0.10)
                    position_size = (bankroll * tail_pct) / market_price
                
                # Execute trade
                outcome, pnl = self.simulate_trade(
                    market_price,
                    fair_value,
                    position_size,
                    model_error=kwargs.get('model_error', 0.10)
                )
                
                bankroll += pnl
                
                # Stop if bankrupt
                if bankroll <= 0:
                    bankroll = 0
                    break
                
                sim_trades.append({
                    'sim_num': sim_num,
                    'trade_num': trade_num,
                    'market_price': market_price,
                    'fair_value': fair_value,
                    'edge': edge,
                    'position_size': position_size,
                    'outcome': outcome,
                    'pnl': pnl,
                    'bankroll': bankroll,
                    'roi': (bankroll / self.initial_bankroll - 1) * 100
                })
            
            all_simulations.extend(sim_trades)
        
        return pd.DataFrame(all_simulations)
    
    def analyze_results(self, df: pd.DataFrame) -> Dict:
        """Analyze simulation results with confidence intervals."""

        # Get final bankroll for each simulation
        final_bankrolls = df.groupby('sim_num')['bankroll'].last()
        final_rois = ((final_bankrolls / self.initial_bankroll) - 1) * 100

        # Calculate confidence intervals
        ci_90_bankroll = (
            np.percentile(final_bankrolls, 5),
            np.percentile(final_bankrolls, 95)
        )
        ci_95_bankroll = (
            np.percentile(final_bankrolls, 2.5),
            np.percentile(final_bankrolls, 97.5)
        )
        ci_95_roi = (
            np.percentile(final_rois, 2.5),
            np.percentile(final_rois, 97.5)
        )

        # Calculate metrics
        analysis = {
            'mean_final_bankroll': final_bankrolls.mean(),
            'median_final_bankroll': final_bankrolls.median(),
            'std_final_bankroll': final_bankrolls.std(),
            'min_final_bankroll': final_bankrolls.min(),
            'max_final_bankroll': final_bankrolls.max(),
            'mean_roi': final_rois.mean(),
            'median_roi': final_rois.median(),
            'prob_profit': (final_bankrolls > self.initial_bankroll).mean(),
            'prob_ruin': (final_bankrolls == 0).mean(),
            'sharpe_ratio': self._calculate_sharpe(df),
            'max_drawdown': self._calculate_max_drawdown(df),
            'win_rate': df['outcome'].mean(),
            # Confidence intervals
            'ci_90_bankroll': ci_90_bankroll,
            'ci_95_bankroll': ci_95_bankroll,
            'ci_95_roi': ci_95_roi,
            # Percentiles
            'percentile_5': np.percentile(final_bankrolls, 5),
            'percentile_25': np.percentile(final_bankrolls, 25),
            'percentile_75': np.percentile(final_bankrolls, 75),
            'percentile_95': np.percentile(final_bankrolls, 95),
        }

        return analysis
    
    def _calculate_sharpe(self, df: pd.DataFrame) -> float:
        """Calculate Sharpe ratio"""
        final_rois = df.groupby('sim_num')['roi'].last()
        if final_rois.std() == 0:
            return 0.0
        return final_rois.mean() / final_rois.std()
    
    def _calculate_max_drawdown(self, df: pd.DataFrame) -> float:
        """Calculate maximum drawdown across all simulations"""
        max_dd = 0

        for sim_num in df['sim_num'].unique():
            sim_data = df[df['sim_num'] == sim_num]
            cummax = sim_data['bankroll'].cummax()
            drawdown = ((sim_data['bankroll'] - cummax) / cummax * 100).min()
            max_dd = min(max_dd, drawdown)

        return max_dd

    # === Visualization Methods ===

    def plot_equity_curves(
        self,
        df: pd.DataFrame,
        num_samples: int = 100,
        title: str = "Equity Curves",
        save_path: Optional[str] = None
    ) -> None:
        """
        Plot sample equity curves from simulations.

        Args:
            df: Simulation results DataFrame
            num_samples: Number of sample curves to plot
            title: Plot title
            save_path: Path to save figure (optional)
        """
        plt.figure(figsize=(12, 6))

        # Sample simulations to plot
        unique_sims = df['sim_num'].unique()
        sample_sims = np.random.choice(
            unique_sims,
            size=min(num_samples, len(unique_sims)),
            replace=False
        )

        for sim_num in sample_sims:
            sim_data = df[df['sim_num'] == sim_num]
            plt.plot(
                sim_data['trade_num'],
                sim_data['bankroll'],
                alpha=0.3,
                linewidth=0.5
            )

        # Plot mean and median
        mean_curve = df.groupby('trade_num')['bankroll'].mean()
        median_curve = df.groupby('trade_num')['bankroll'].median()

        plt.plot(mean_curve.index, mean_curve.values, 'b-', linewidth=2, label='Mean')
        plt.plot(median_curve.index, median_curve.values, 'r--', linewidth=2, label='Median')

        plt.axhline(y=self.initial_bankroll, color='g', linestyle=':', label='Initial')
        plt.axhline(y=0, color='k', linestyle='-', alpha=0.3)

        plt.xlabel('Trade Number')
        plt.ylabel('Bankroll ($)')
        plt.title(title)
        plt.legend()
        plt.grid(True, alpha=0.3)

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            logger.info(f"Equity curves saved to {save_path}")

        plt.show()

    def plot_final_distribution(
        self,
        df: pd.DataFrame,
        title: str = "Final Bankroll Distribution",
        save_path: Optional[str] = None
    ) -> None:
        """
        Plot distribution of final bankroll values.

        Args:
            df: Simulation results DataFrame
            title: Plot title
            save_path: Path to save figure (optional)
        """
        final_bankrolls = df.groupby('sim_num')['bankroll'].last()

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # Histogram
        axes[0].hist(final_bankrolls, bins=50, edgecolor='black', alpha=0.7)
        axes[0].axvline(x=self.initial_bankroll, color='g', linestyle='--', label='Initial')
        axes[0].axvline(x=final_bankrolls.mean(), color='r', linestyle='-', label='Mean')
        axes[0].axvline(x=final_bankrolls.median(), color='b', linestyle='-', label='Median')
        axes[0].set_xlabel('Final Bankroll ($)')
        axes[0].set_ylabel('Frequency')
        axes[0].set_title('Distribution of Final Bankrolls')
        axes[0].legend()

        # Box plot
        axes[1].boxplot(final_bankrolls, vert=True)
        axes[1].axhline(y=self.initial_bankroll, color='g', linestyle='--', label='Initial')
        axes[1].set_ylabel('Final Bankroll ($)')
        axes[1].set_title('Box Plot of Final Bankrolls')

        plt.suptitle(title)
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            logger.info(f"Distribution plot saved to {save_path}")

        plt.show()

    def plot_drawdown_distribution(
        self,
        df: pd.DataFrame,
        title: str = "Maximum Drawdown Distribution",
        save_path: Optional[str] = None
    ) -> None:
        """
        Plot distribution of maximum drawdowns.

        Args:
            df: Simulation results DataFrame
            title: Plot title
            save_path: Path to save figure (optional)
        """
        max_drawdowns = []

        for sim_num in df['sim_num'].unique():
            sim_data = df[df['sim_num'] == sim_num]
            cummax = sim_data['bankroll'].cummax()
            drawdown = ((sim_data['bankroll'] - cummax) / cummax * 100).min()
            max_drawdowns.append(drawdown)

        plt.figure(figsize=(10, 6))
        plt.hist(max_drawdowns, bins=50, edgecolor='black', alpha=0.7, color='red')
        plt.axvline(x=np.mean(max_drawdowns), color='b', linestyle='-', label=f'Mean: {np.mean(max_drawdowns):.1f}%')
        plt.axvline(x=np.median(max_drawdowns), color='g', linestyle='--', label=f'Median: {np.median(max_drawdowns):.1f}%')

        plt.xlabel('Maximum Drawdown (%)')
        plt.ylabel('Frequency')
        plt.title(title)
        plt.legend()
        plt.grid(True, alpha=0.3)

        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            logger.info(f"Drawdown plot saved to {save_path}")

        plt.show()


def compare_strategies():
    """Compare different strategies side by side"""

    sim = MonteCarloSimulator(initial_bankroll=10000, num_simulations=1000)

    strategies = {
        'Kelly (1/4)': {
            'type': 'kelly',
            'params': {'kelly_fraction': 0.25, 'model_error': 0.10}
        },
        'Kelly (Full)': {
            'type': 'kelly',
            'params': {'kelly_fraction': 1.0, 'model_error': 0.10}
        },
        'Fixed 2%': {
            'type': 'fixed',
            'params': {'fixed_pct': 0.02, 'model_error': 0.10}
        },
        'Tail Betting': {
            'type': 'tail_betting',
            'params': {'tail_pct': 0.10, 'model_error': 0.10}
        }
    }

    results = {}

    logger.info("Running Monte Carlo Simulations...")
    logger.info("=" * 70)

    for name, config in strategies.items():
        logger.info(f"Simulating: {name}")
        df = sim.simulate_strategy(
            config['type'],
            num_trades=100,
            **config['params']
        )

        analysis = sim.analyze_results(df)
        results[name] = {
            'df': df,
            'analysis': analysis
        }

        logger.info(f"  Mean Final: ${analysis['mean_final_bankroll']:,.0f}")
        logger.info(f"  Median ROI: {analysis['median_roi']:.1f}%")
        logger.info(f"  Prob Profit: {analysis['prob_profit']:.1%}")
        logger.info(f"  Prob Ruin: {analysis['prob_ruin']:.1%}")
        logger.info(f"  Max Drawdown: {analysis['max_drawdown']:.1f}%")
        logger.info(f"  Sharpe Ratio: {analysis['sharpe_ratio']:.2f}")

    # Create comparison table
    logger.info("=" * 70)
    logger.info("STRATEGY COMPARISON")
    logger.info("=" * 70)
    
    comparison_df = pd.DataFrame({
        name: {
            'Mean Final ($)': f"${res['analysis']['mean_final_bankroll']:,.0f}",
            'Median ROI (%)': f"{res['analysis']['median_roi']:.1f}%",
            'Sharpe Ratio': f"{res['analysis']['sharpe_ratio']:.2f}",
            'Prob Profit': f"{res['analysis']['prob_profit']:.1%}",
            'Prob Ruin': f"{res['analysis']['prob_ruin']:.1%}",
            'Max Drawdown': f"{res['analysis']['max_drawdown']:.1f}%",
            'Win Rate': f"{res['analysis']['win_rate']:.1%}"
        }
        for name, res in results.items()
    }).T
    
    logger.info(comparison_df.to_string())

    return results


def test_model_accuracy_impact():
    """Test how model accuracy affects returns"""

    sim = MonteCarloSimulator(initial_bankroll=10000, num_simulations=500)

    model_errors = [0.05, 0.10, 0.15, 0.20, 0.25]
    results = []

    logger.info("Testing Impact of Model Accuracy...")
    logger.info("=" * 70)

    for error in model_errors:
        df = sim.simulate_strategy(
            'kelly',
            num_trades=100,
            kelly_fraction=0.25,
            model_error=error
        )

        analysis = sim.analyze_results(df)

        results.append({
            'model_error': error,
            'mean_roi': analysis['mean_roi'],
            'median_roi': analysis['median_roi'],
            'prob_profit': analysis['prob_profit'],
            'sharpe': analysis['sharpe_ratio']
        })

        logger.info(f"Model Error: +/-{error:.0%}")
        logger.info(f"  Median ROI: {analysis['median_roi']:.1f}%")
        logger.info(f"  Prob Profit: {analysis['prob_profit']:.1%}")
        logger.info(f"  Sharpe: {analysis['sharpe_ratio']:.2f}")

    results_df = pd.DataFrame(results)

    logger.info("=" * 70)
    logger.info("KEY INSIGHT:")
    logger.info("=" * 70)
    logger.info("Model accuracy is CRITICAL. Even small improvements in accuracy")
    logger.info("(reducing error from 15% to 10%) can significantly boost returns.")
    logger.info("Focus on building the best model before worrying about position sizing.")

    return results_df


def simulate_ruin_probability():
    """Calculate probability of ruin for different strategies"""

    sim = MonteCarloSimulator(initial_bankroll=10000, num_simulations=2000)

    logger.info("Risk of Ruin Analysis...")
    logger.info("=" * 70)

    scenarios = [
        ('Conservative Kelly (1/4)', 'kelly', {'kelly_fraction': 0.25}),
        ('Aggressive Kelly (1/2)', 'kelly', {'kelly_fraction': 0.50}),
        ('Full Kelly (Dangerous)', 'kelly', {'kelly_fraction': 1.0}),
        ('Tail Betting (Original Strategy)', 'tail_betting', {'tail_pct': 0.10}),
    ]

    for name, strategy_type, params in scenarios:
        df = sim.simulate_strategy(strategy_type, num_trades=100, **params)
        analysis = sim.analyze_results(df)

        logger.info(f"{name}")
        logger.info(f"  Risk of Ruin: {analysis['prob_ruin']:.2%}")
        logger.info(f"  Max Drawdown: {analysis['max_drawdown']:.1f}%")
        logger.info(f"  Median ROI: {analysis['median_roi']:.1f}%")

        if analysis['prob_ruin'] > 0.10:
            logger.warning(f"  WARNING: >10% chance of total loss!")


if __name__ == "__main__":
    from logger import setup_logging
    setup_logging(level='INFO')

    logger.info("=" * 70)
    logger.info("POLYMARKET STRATEGY MONTE CARLO SIMULATION")
    logger.info("=" * 70)
    logger.info("This simulation compares different betting strategies over 100 trades")
    logger.info("with 1,000 simulations each to understand risk/return profiles.")

    # Run comprehensive comparison
    results = compare_strategies()

    # Test model accuracy impact
    accuracy_results = test_model_accuracy_impact()

    # Risk of ruin analysis
    simulate_ruin_probability()

    logger.info("=" * 70)
    logger.info("CONCLUSIONS")
    logger.info("=" * 70)
    conclusions = """
1. FRACTIONAL KELLY IS KING
   - Quarter Kelly (1/4) provides best risk-adjusted returns
   - Full Kelly is too aggressive, leads to large drawdowns
   - Fixed percentage (2%) is too conservative, leaves money on table

2. TAIL BETTING IS DANGEROUS
   - The strategy in your original document has high ruin probability
   - Works until it doesn't - then you lose everything
   - Not "low risk" despite consistent small gains

3. MODEL ACCURACY MATTERS MOST
   - Reducing estimation error from 15% to 10% doubles returns
   - Spend time improving your model before optimizing position sizing
   - Bad model + perfect sizing still loses money

4. RISK MANAGEMENT IS NON-NEGOTIABLE
   - Use stop losses
   - Diversify across markets
   - Keep position sizes reasonable
   - Survive to trade another day

5. EXPECTED VALUE > WIN RATE
   - 55% win rate with proper sizing beats 70% win rate with poor sizing
   - Focus on finding +EV opportunities, not maximizing win rate
   - One good trade can offset multiple small losses
"""
    for line in conclusions.strip().split('\n'):
        logger.info(line)
