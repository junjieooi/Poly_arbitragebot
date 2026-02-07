"""
Position Sizing & Risk Management
Kelly Criterion, portfolio optimization, and risk controls
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from scipy.optimize import minimize

from logger import get_logger, TradingLogger

logger = get_logger(__name__)
trading_logger = TradingLogger(__name__)

@dataclass
class Position:
    """Trading position"""
    market_id: str
    outcome: str
    shares: int
    avg_price: float
    current_price: float
    fair_value: float
    
    @property
    def cost_basis(self) -> float:
        return self.shares * self.avg_price
    
    @property
    def current_value(self) -> float:
        return self.shares * self.current_price
    
    @property
    def unrealized_pnl(self) -> float:
        return self.current_value - self.cost_basis
    
    @property
    def pnl_pct(self) -> float:
        return (self.unrealized_pnl / self.cost_basis * 100) if self.cost_basis > 0 else 0


def validate_probability(prob: float, name: str = "probability") -> None:
    """Validate that probability is in valid range [0, 1]"""
    if not isinstance(prob, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(prob).__name__}")
    if not 0 <= prob <= 1:
        raise ValueError(f"{name} must be between 0 and 1 (inclusive), got {prob}")


def validate_positive(value: float, name: str = "value") -> None:
    """Validate that value is positive"""
    if not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def validate_price(price: float, name: str = "price") -> None:
    """Validate that price is in valid range (0, 1)"""
    if not isinstance(price, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(price).__name__}")
    if not 0 < price < 1:
        raise ValueError(f"{name} must be between 0 and 1 (exclusive), got {price}")


class KellyCriterion:
    """Kelly Criterion for optimal position sizing"""

    @staticmethod
    def full_kelly(win_prob: float, odds: float) -> float:
        """
        Calculate full Kelly fraction
        f* = (bp - q) / b

        Args:
            win_prob: Probability of winning (0-1)
            odds: Decimal odds (payout/stake - 1)

        Returns:
            Kelly fraction, clamped to 0 if negative

        Raises:
            ValueError: If win_prob not in [0, 1]
            ValueError: If odds <= 0
        """
        validate_probability(win_prob, "win_prob")
        if odds <= 0:
            return 0.0

        lose_prob = 1 - win_prob
        kelly = (odds * win_prob - lose_prob) / odds

        return max(0, kelly)
    
    @staticmethod
    def fractional_kelly(win_prob: float, odds: float, fraction: float = 0.25) -> float:
        """
        Calculate fractional Kelly (safer than full Kelly)
        Common fractions: 1/4 (0.25), 1/3 (0.33), 1/2 (0.5)
        """
        full = KellyCriterion.full_kelly(win_prob, odds)
        return full * fraction
    
    @staticmethod
    def kelly_from_prices(market_price: float,
                         fair_value: float,
                         fraction: float = 0.25,
                         confidence: float = 1.0) -> float:
        """
        Calculate Kelly from market price and fair value

        Args:
            market_price: Current market price (0-1 exclusive)
            fair_value: Your estimated fair value (0-1)
            fraction: Kelly fraction (0.25 = quarter Kelly)
            confidence: Confidence in your estimate (0-1)

        Returns:
            Suggested position size as fraction of bankroll

        Raises:
            ValueError: If inputs are out of valid range
        """
        validate_price(market_price, "market_price")
        validate_probability(fair_value, "fair_value")
        validate_probability(confidence, "confidence")
        if not 0 < fraction <= 1:
            raise ValueError(f"fraction must be (0, 1], got {fraction}")

        # Adjust fair value by confidence
        adjusted_fv = fair_value * confidence

        # Calculate odds
        odds = (1.0 / market_price) - 1

        # Kelly fraction
        return KellyCriterion.fractional_kelly(adjusted_fv, odds, fraction)


class RiskManager:
    """Portfolio-level risk management with enhanced controls"""

    def __init__(self,
                 max_position_size: float = 0.10,
                 max_market_exposure: float = 0.25,
                 max_portfolio_risk: float = 0.50,
                 stop_loss_pct: float = -0.25,
                 daily_loss_limit: float = -0.05,
                 max_drawdown_limit: float = -0.20,
                 drawdown_sizing_enabled: bool = True):
        """
        Args:
            max_position_size: Max % of portfolio in single position (0-1)
            max_market_exposure: Max % in single market (all outcomes) (0-1)
            max_portfolio_risk: Max % of portfolio at risk (0-1)
            stop_loss_pct: Position stop loss threshold (negative, e.g., -0.25)
            daily_loss_limit: Maximum daily loss before halting (negative, e.g., -0.05)
            max_drawdown_limit: Maximum drawdown before halting (negative, e.g., -0.20)
            drawdown_sizing_enabled: Reduce position size during drawdowns

        Raises:
            ValueError: If parameters are out of valid range
        """
        if not 0 < max_position_size <= 1:
            raise ValueError(f"max_position_size must be (0, 1], got {max_position_size}")
        if not 0 < max_market_exposure <= 1:
            raise ValueError(f"max_market_exposure must be (0, 1], got {max_market_exposure}")
        if not 0 < max_portfolio_risk <= 1:
            raise ValueError(f"max_portfolio_risk must be (0, 1], got {max_portfolio_risk}")
        if stop_loss_pct >= 0:
            raise ValueError(f"stop_loss_pct must be negative, got {stop_loss_pct}")
        if daily_loss_limit >= 0:
            raise ValueError(f"daily_loss_limit must be negative, got {daily_loss_limit}")
        if max_drawdown_limit >= 0:
            raise ValueError(f"max_drawdown_limit must be negative, got {max_drawdown_limit}")

        self.max_position_size = max_position_size
        self.max_market_exposure = max_market_exposure
        self.max_portfolio_risk = max_portfolio_risk
        self.stop_loss_pct = stop_loss_pct
        self.daily_loss_limit = daily_loss_limit
        self.max_drawdown_limit = max_drawdown_limit
        self.drawdown_sizing_enabled = drawdown_sizing_enabled

        # State tracking
        self.daily_pnl = 0.0
        self.daily_start_value = 0.0
        self.peak_portfolio_value = 0.0
        self.trading_halted = False
        self.halt_reason = None
    
    def calculate_position_size(self,
                               bankroll: float,
                               kelly_fraction: float,
                               market_price: float) -> int:
        """
        Calculate number of shares to buy

        Args:
            bankroll: Current bankroll (must be positive)
            kelly_fraction: Suggested Kelly fraction (0-1)
            market_price: Current market price (0-1 exclusive)

        Returns:
            Number of shares (integer)

        Raises:
            ValueError: If inputs are out of valid range
        """
        validate_positive(bankroll, "bankroll")
        if kelly_fraction < 0:
            raise ValueError(f"kelly_fraction must be non-negative, got {kelly_fraction}")
        validate_price(market_price, "market_price")

        # Kelly suggests this fraction of bankroll
        kelly_dollar_amount = bankroll * kelly_fraction

        # Apply max position size constraint
        max_dollar_amount = bankroll * self.max_position_size
        dollar_amount = min(kelly_dollar_amount, max_dollar_amount)

        # Convert to shares
        shares = int(dollar_amount / market_price)

        return shares
    
    def check_position_limits(
        self,
        position: Position,
        portfolio_value: float,
        all_positions: Optional[List[Position]] = None
    ) -> Dict:
        """
        Check if position violates risk limits.

        Args:
            position: Position to check
            portfolio_value: Total portfolio value
            all_positions: Optional list of all positions for market exposure check

        Returns:
            Dict with limit check results
        """
        if portfolio_value <= 0:
            return {
                'position_size_ok': False,
                'position_size_pct': 0,
                'stop_loss_triggered': False,
                'pnl_pct': 0,
                'market_exposure_ok': False,
                'market_exposure_pct': 0,
                'reason': 'Invalid portfolio value'
            }

        position_size_pct = position.cost_basis / portfolio_value

        # Calculate market exposure if all positions provided
        market_exposure_pct = 0.0
        market_exposure_ok = True

        if all_positions:
            # Sum up all positions in the same market
            market_exposure = sum(
                p.cost_basis for p in all_positions
                if p.market_id == position.market_id
            )
            market_exposure_pct = market_exposure / portfolio_value
            market_exposure_ok = market_exposure_pct <= self.max_market_exposure

            if not market_exposure_ok:
                logger.warning(
                    f"Market exposure limit exceeded for {position.market_id}: "
                    f"{market_exposure_pct:.2%} > {self.max_market_exposure:.2%}"
                )

        checks = {
            'position_size_ok': position_size_pct <= self.max_position_size,
            'position_size_pct': position_size_pct,
            'stop_loss_triggered': position.pnl_pct <= (self.stop_loss_pct * 100),
            'pnl_pct': position.pnl_pct,
            'market_exposure_ok': market_exposure_ok,
            'market_exposure_pct': market_exposure_pct
        }

        return checks
    
    def calculate_portfolio_risk(self, positions: List[Position]) -> Dict:
        """Calculate portfolio-level risk metrics"""
        
        if not positions:
            return {'total_risk': 0, 'positions': 0}
        
        total_cost = sum(p.cost_basis for p in positions)
        total_value = sum(p.current_value for p in positions)
        total_pnl = sum(p.unrealized_pnl for p in positions)
        
        # Group by market to check market exposure
        market_exposure = {}
        for pos in positions:
            if pos.market_id not in market_exposure:
                market_exposure[pos.market_id] = 0
            market_exposure[pos.market_id] += pos.cost_basis
        
        return {
            'total_cost': total_cost,
            'total_value': total_value,
            'total_pnl': total_pnl,
            'pnl_pct': (total_pnl / total_cost * 100) if total_cost > 0 else 0,
            'num_positions': len(positions),
            'num_markets': len(market_exposure),
            'largest_market_exposure': max(market_exposure.values()) if market_exposure else 0,
            'positions_in_loss': sum(1 for p in positions if p.unrealized_pnl < 0)
        }
    
    def get_stop_loss_recommendations(self, 
                                     positions: List[Position],
                                     portfolio_value: float) -> List[Dict]:
        """Get positions that should be stopped out"""
        
        recommendations = []
        
        for pos in positions:
            checks = self.check_position_limits(pos, portfolio_value)
            
            if checks['stop_loss_triggered']:
                recommendations.append({
                    'market_id': pos.market_id,
                    'outcome': pos.outcome,
                    'action': 'CLOSE',
                    'reason': 'Stop loss triggered',
                    'pnl_pct': pos.pnl_pct,
                    'shares': pos.shares
                })
        
        return recommendations

    # === Enhanced Risk Controls ===

    def start_trading_day(self, portfolio_value: float) -> None:
        """
        Initialize daily tracking. Call at start of each trading day.

        Args:
            portfolio_value: Current portfolio value
        """
        validate_positive(portfolio_value, "portfolio_value")
        self.daily_start_value = portfolio_value
        self.daily_pnl = 0.0
        self.trading_halted = False
        self.halt_reason = None

        # Update peak for drawdown tracking
        if portfolio_value > self.peak_portfolio_value:
            self.peak_portfolio_value = portfolio_value

        logger.info(f"Trading day started. Portfolio: ${portfolio_value:,.2f}")

    def update_pnl(self, pnl: float, current_portfolio_value: float) -> Dict:
        """
        Update P&L and check risk limits.

        Args:
            pnl: P&L from latest trade
            current_portfolio_value: Current portfolio value

        Returns:
            Dict with status and any actions required
        """
        self.daily_pnl += pnl

        # Update peak
        if current_portfolio_value > self.peak_portfolio_value:
            self.peak_portfolio_value = current_portfolio_value

        # Check daily loss limit
        daily_check = self.check_daily_loss_limit(current_portfolio_value)
        if daily_check['halt_trading']:
            self.trading_halted = True
            self.halt_reason = daily_check['reason']
            trading_logger.risk_alert('DAILY_LOSS', daily_check['reason'])
            return daily_check

        # Check drawdown limit
        drawdown_check = self.check_drawdown_limit(current_portfolio_value)
        if drawdown_check['halt_trading']:
            self.trading_halted = True
            self.halt_reason = drawdown_check['reason']
            trading_logger.risk_alert('MAX_DRAWDOWN', drawdown_check['reason'])
            return drawdown_check

        return {
            'halt_trading': False,
            'daily_pnl': self.daily_pnl,
            'daily_pnl_pct': self.daily_pnl / self.daily_start_value * 100 if self.daily_start_value > 0 else 0,
            'drawdown_pct': drawdown_check['drawdown_pct']
        }

    def check_daily_loss_limit(self, current_portfolio_value: float) -> Dict:
        """
        Check if daily loss limit has been hit.

        Args:
            current_portfolio_value: Current portfolio value

        Returns:
            Dict with halt_trading flag and details
        """
        if self.daily_start_value <= 0:
            return {'halt_trading': False, 'reason': 'Daily tracking not initialized'}

        daily_return = (current_portfolio_value - self.daily_start_value) / self.daily_start_value

        if daily_return <= self.daily_loss_limit:
            return {
                'halt_trading': True,
                'action': 'HALT_TRADING',
                'reason': f'Daily loss limit hit: {daily_return:.2%} (limit: {self.daily_loss_limit:.2%})',
                'daily_loss_pct': daily_return * 100,
                'daily_loss_limit': self.daily_loss_limit * 100
            }

        return {
            'halt_trading': False,
            'daily_return_pct': daily_return * 100,
            'remaining_budget_pct': (daily_return - self.daily_loss_limit) * 100
        }

    def check_drawdown_limit(self, current_portfolio_value: float) -> Dict:
        """
        Check if maximum drawdown limit has been hit.

        Args:
            current_portfolio_value: Current portfolio value

        Returns:
            Dict with halt_trading flag and details
        """
        if self.peak_portfolio_value <= 0:
            return {'halt_trading': False, 'drawdown_pct': 0}

        drawdown = (current_portfolio_value - self.peak_portfolio_value) / self.peak_portfolio_value

        if drawdown <= self.max_drawdown_limit:
            return {
                'halt_trading': True,
                'action': 'HALT_TRADING',
                'reason': f'Max drawdown hit: {drawdown:.2%} (limit: {self.max_drawdown_limit:.2%})',
                'drawdown_pct': drawdown * 100,
                'max_drawdown_limit': self.max_drawdown_limit * 100
            }

        return {
            'halt_trading': False,
            'drawdown_pct': drawdown * 100,
            'remaining_drawdown_budget_pct': (drawdown - self.max_drawdown_limit) * 100
        }

    def get_drawdown_adjusted_size(
        self,
        base_kelly_fraction: float,
        current_portfolio_value: float
    ) -> float:
        """
        Adjust position size based on current drawdown.
        Reduces size during drawdowns to preserve capital.

        Args:
            base_kelly_fraction: Base Kelly fraction
            current_portfolio_value: Current portfolio value

        Returns:
            Adjusted Kelly fraction
        """
        if not self.drawdown_sizing_enabled:
            return base_kelly_fraction

        if current_portfolio_value <= 0 or self.peak_portfolio_value <= 0:
            return 0.0

        # Calculate current drawdown (will be 0 or negative)
        current_drawdown = (current_portfolio_value - self.peak_portfolio_value) / self.peak_portfolio_value

        # If not in drawdown, return base fraction
        if current_drawdown >= 0:
            return base_kelly_fraction

        # Calculate how deep we are into max drawdown (0 to 1)
        # At 0% drawdown: ratio = 0, at max_drawdown: ratio = 1
        drawdown_ratio = abs(current_drawdown) / abs(self.max_drawdown_limit)

        # Clamp ratio to 1 (shouldn't exceed, but safety check)
        drawdown_ratio = min(drawdown_ratio, 1.0)

        # Linear reduction: at 50% of max drawdown, reduce to 50% of base size
        # Never reduce below 25% of base size to maintain some activity
        reduction_factor = max(0.25, 1.0 - drawdown_ratio)

        adjusted = base_kelly_fraction * reduction_factor

        logger.debug(
            f"Drawdown adjustment: base={base_kelly_fraction:.2%}, "
            f"drawdown={current_drawdown:.2%}, ratio={drawdown_ratio:.2%}, "
            f"adjusted={adjusted:.2%}"
        )

        return adjusted

    def calculate_position_size_with_drawdown(
        self,
        bankroll: float,
        kelly_fraction: float,
        market_price: float,
        current_drawdown_pct: float = 0.0
    ) -> int:
        """
        Calculate position size with drawdown adjustment.

        Args:
            bankroll: Current bankroll
            kelly_fraction: Suggested Kelly fraction
            market_price: Current market price
            current_drawdown_pct: Current drawdown percentage (0 to -100)

        Returns:
            Adjusted number of shares
        """
        if self.trading_halted:
            logger.warning(f"Trading halted: {self.halt_reason}")
            return 0

        adjusted_kelly = kelly_fraction

        # Apply drawdown-based reduction
        if self.drawdown_sizing_enabled and current_drawdown_pct < 0:
            # Linear reduction: at 50% of max drawdown, reduce size by 50%
            drawdown_ratio = abs(current_drawdown_pct) / abs(self.max_drawdown_limit * 100)
            reduction_factor = max(0.25, 1 - drawdown_ratio)  # Don't reduce below 25%
            adjusted_kelly = kelly_fraction * reduction_factor
            logger.debug(
                f"Drawdown adjustment: {kelly_fraction:.2%} -> {adjusted_kelly:.2%} "
                f"(drawdown: {current_drawdown_pct:.1f}%)"
            )

        return self.calculate_position_size(bankroll, adjusted_kelly, market_price)

    def get_stop_loss_orders(self, positions: List[Position]) -> List[Dict]:
        """
        Generate stop-loss orders for positions that need to be closed.

        Args:
            positions: List of current positions

        Returns:
            List of order dictionaries for execution
        """
        orders = []

        for pos in positions:
            if pos.pnl_pct <= (self.stop_loss_pct * 100):
                order = {
                    'market_id': pos.market_id,
                    'outcome': pos.outcome,
                    'action': 'SELL',
                    'shares': pos.shares,
                    'order_type': 'MARKET',
                    'reason': f'Stop loss triggered at {pos.pnl_pct:.1f}%'
                }
                orders.append(order)
                trading_logger.stop_loss_triggered(pos.market_id, pos.pnl_pct)

        return orders

    def should_allow_trade(self) -> Tuple[bool, str]:
        """
        Check if trading is currently allowed.

        Returns:
            Tuple of (allowed, reason)
        """
        if self.trading_halted:
            return False, f"Trading halted: {self.halt_reason}"

        return True, "Trading allowed"

    def reset_daily_limits(self) -> None:
        """Reset daily tracking (call at end of day or manually)."""
        self.daily_pnl = 0.0
        self.daily_start_value = 0.0
        self.trading_halted = False
        self.halt_reason = None
        logger.info("Daily limits reset")


class PortfolioOptimizer:
    """Optimize portfolio allocation across multiple opportunities"""
    
    @staticmethod
    def optimize_allocations(opportunities: List[Dict],
                           bankroll: float,
                           risk_free_rate: float = 0.0) -> pd.DataFrame:
        """
        Optimize portfolio allocation using mean-variance optimization
        
        Args:
            opportunities: List of dicts with 'expected_return' and 'volatility'
            bankroll: Total capital to allocate
            risk_free_rate: Risk-free rate for Sharpe calculation
        
        Returns:
            DataFrame with optimal allocations
        """
        n = len(opportunities)
        
        if n == 0:
            return pd.DataFrame()
        
        # Extract expected returns and volatilities
        returns = np.array([opp['expected_return'] for opp in opportunities])
        vols = np.array([opp.get('volatility', 0.3) for opp in opportunities])
        
        # Simple optimization: weight by Sharpe ratio
        sharpe_ratios = (returns - risk_free_rate) / vols
        sharpe_ratios = np.maximum(sharpe_ratios, 0)  # No negative weights
        
        if sharpe_ratios.sum() == 0:
            weights = np.ones(n) / n  # Equal weight
        else:
            weights = sharpe_ratios / sharpe_ratios.sum()
        
        # Calculate allocations
        allocations = weights * bankroll
        
        # Create results DataFrame
        results = pd.DataFrame({
            'market': [opp['market_id'] for opp in opportunities],
            'expected_return': returns,
            'volatility': vols,
            'sharpe_ratio': sharpe_ratios,
            'weight': weights,
            'allocation': allocations
        })
        
        return results.sort_values('allocation', ascending=False)


class BettingSimulator:
    """Backtest betting strategies"""
    
    def __init__(self, initial_bankroll: float = 10000):
        self.initial_bankroll = initial_bankroll
        self.bankroll = initial_bankroll
        self.history = []
    
    def simulate_bet(self,
                    bet_size: float,
                    win_prob: float,
                    odds: float,
                    outcome: bool) -> Dict:
        """
        Simulate a single bet

        Args:
            bet_size: Amount wagered
            win_prob: True probability of winning
            odds: Decimal odds
            outcome: True if bet wins

        Returns:
            Dict with bet results
        """
        profit = 0.0  # Initialize to fix potential undefined variable bug

        if outcome:
            profit = bet_size * odds
            self.bankroll += profit
        else:
            profit = -bet_size
            self.bankroll -= bet_size

        result = {
            'bet_size': bet_size,
            'win_prob': win_prob,
            'odds': odds,
            'outcome': outcome,
            'profit': profit,
            'bankroll': self.bankroll,
            'roi': (self.bankroll / self.initial_bankroll - 1) * 100
        }
        
        self.history.append(result)
        return result
    
    def simulate_sequence(self,
                         opportunities: List[Dict],
                         sizing_method: str = 'kelly') -> pd.DataFrame:
        """
        Simulate sequence of bets
        
        Args:
            opportunities: List with 'win_prob', 'odds', 'outcome'
            sizing_method: 'kelly', 'fixed', or 'martingale'
        
        Returns:
            DataFrame with simulation results
        """
        self.bankroll = self.initial_bankroll
        self.history = []
        
        for opp in opportunities:
            win_prob = opp['win_prob']
            odds = opp['odds']
            outcome = opp['outcome']
            
            # Determine bet size
            if sizing_method == 'kelly':
                kelly = KellyCriterion.full_kelly(win_prob, odds) * 0.25  # Quarter Kelly
                bet_size = self.bankroll * kelly
            elif sizing_method == 'fixed':
                bet_size = self.initial_bankroll * 0.02  # 2% fixed
            else:  # equal
                bet_size = self.bankroll * 0.05
            
            bet_size = min(bet_size, self.bankroll)  # Can't bet more than bankroll
            
            if bet_size > 0:
                self.simulate_bet(bet_size, win_prob, odds, outcome)
        
        return pd.DataFrame(self.history)


# Example usage
if __name__ == "__main__":
    from logger import setup_logging
    setup_logging(level='INFO')

    logger.info("=== KELLY CRITERION EXAMPLES ===")

    # Example 1: Market at 40c, fair value 60c
    market_price = 0.40
    fair_value = 0.60

    kelly = KellyCriterion.kelly_from_prices(market_price, fair_value, fraction=0.25)
    logger.info(f"Market: {market_price:.0%}, Fair Value: {fair_value:.0%}")
    logger.info(f"Quarter Kelly: {kelly:.1%} of bankroll")

    # Example 2: Position sizing
    bankroll = 10000
    rm = RiskManager(max_position_size=0.10)
    shares = rm.calculate_position_size(bankroll, kelly, market_price)
    logger.info(f"Bankroll: ${bankroll:,.0f}")
    logger.info(f"Suggested shares: {shares} @ {market_price:.2f}c")
    logger.info(f"Total cost: ${shares * market_price:.2f}")

    # Example 3: Portfolio risk
    positions = [
        Position("market_1", "YES", 100, 0.45, 0.52, 0.60),
        Position("market_2", "NO", 150, 0.38, 0.35, 0.30),
        Position("market_3", "YES", 80, 0.55, 0.48, 0.65),
    ]

    risk_metrics = rm.calculate_portfolio_risk(positions)
    logger.info("=== PORTFOLIO METRICS ===")
    logger.info(f"Total Cost: ${risk_metrics['total_cost']:.2f}")
    logger.info(f"Total Value: ${risk_metrics['total_value']:.2f}")
    logger.info(f"P&L: ${risk_metrics['total_pnl']:.2f} ({risk_metrics['pnl_pct']:.2f}%)")
    logger.info(f"Positions: {risk_metrics['num_positions']}")
