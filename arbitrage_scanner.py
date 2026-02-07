"""
Polymarket Arbitrage Scanner
Detects and quantifies arbitrage opportunities across multiple market types
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime

from logger import get_logger, TradingLogger

logger = get_logger(__name__)
trading_logger = TradingLogger(__name__)

@dataclass
class MarketData:
    """Single outcome in a prediction market"""
    market_id: str
    outcome: str
    price: float
    volume: float
    liquidity: float
    timestamp: datetime

@dataclass
class OrderBookLevel:
    """Single level in order book"""
    price: float
    size: float


@dataclass
class ArbitrageOpportunity:
    """Detected arbitrage opportunity"""
    type: str
    markets: List[str]
    actions: List[str]
    prices: List[float]
    profit: float
    profit_pct: float
    risk_score: float
    confidence: float
    effective_prices: Optional[List[float]] = None  # After slippage
    liquidity_sufficient: bool = True


class LiquidityAnalyzer:
    """Analyze market liquidity and calculate slippage"""

    def __init__(self, min_liquidity_ratio: float = 0.1):
        """
        Args:
            min_liquidity_ratio: Max fraction of available liquidity to use (default 10%)
        """
        if not 0 < min_liquidity_ratio <= 1:
            raise ValueError(f"min_liquidity_ratio must be (0, 1], got {min_liquidity_ratio}")
        self.min_liquidity_ratio = min_liquidity_ratio

    def calculate_effective_price(
        self,
        quote_price: float,
        order_size: float,
        order_book: List[OrderBookLevel],
        is_buy: bool = True
    ) -> Tuple[float, float]:
        """
        Calculate effective fill price accounting for slippage.

        Args:
            quote_price: Quoted market price
            order_size: Desired order size in shares
            order_book: List of (price, size) tuples representing order book depth
            is_buy: True for buy orders (walk asks), False for sell orders (walk bids)

        Returns:
            Tuple of (effective_price, slippage_pct)
        """
        if not order_book:
            return quote_price, 0.0

        # Sort order book appropriately
        if is_buy:
            # For buys, walk up the asks (lowest first)
            sorted_book = sorted(order_book, key=lambda x: x.price)
        else:
            # For sells, walk down the bids (highest first)
            sorted_book = sorted(order_book, key=lambda x: x.price, reverse=True)

        remaining_size = order_size
        total_cost = 0.0

        for level in sorted_book:
            fill_size = min(remaining_size, level.size)
            total_cost += fill_size * level.price
            remaining_size -= fill_size

            if remaining_size <= 0:
                break

        if remaining_size > 0:
            # Not enough liquidity, use worst price for remainder
            logger.warning(
                f"Insufficient liquidity: need {order_size}, available {order_size - remaining_size}"
            )
            # Assume 10% slippage for unfilled portion
            worst_price = sorted_book[-1].price if sorted_book else quote_price
            penalty = worst_price * 1.10 if is_buy else worst_price * 0.90
            total_cost += remaining_size * penalty

        effective_price = total_cost / order_size
        slippage_pct = abs(effective_price - quote_price) / quote_price * 100

        return effective_price, slippage_pct

    def check_liquidity_sufficient(
        self,
        required_size: float,
        available_liquidity: float
    ) -> Dict:
        """
        Check if order size is within acceptable liquidity limits.

        Args:
            required_size: Desired order size
            available_liquidity: Total available liquidity in order book

        Returns:
            Dict with liquidity analysis
        """
        if available_liquidity <= 0:
            return {
                'sufficient': False,
                'reason': 'No liquidity available',
                'utilization': float('inf')
            }

        utilization = required_size / available_liquidity

        return {
            'sufficient': utilization <= self.min_liquidity_ratio,
            'utilization': utilization,
            'utilization_pct': utilization * 100,
            'max_recommended_size': available_liquidity * self.min_liquidity_ratio,
            'available_liquidity': available_liquidity
        }

    def estimate_market_impact(
        self,
        order_size: float,
        daily_volume: float,
        volatility: float = 0.02
    ) -> float:
        """
        Estimate market impact using square-root model.

        Args:
            order_size: Order size in dollars
            daily_volume: Average daily volume in dollars
            volatility: Daily volatility (default 2%)

        Returns:
            Estimated market impact as percentage
        """
        if daily_volume <= 0:
            return 0.10  # Assume 10% impact for illiquid markets

        # Square-root market impact model: impact = volatility * sqrt(size/volume)
        participation_rate = order_size / daily_volume
        impact = volatility * np.sqrt(participation_rate)

        return min(impact, 0.10)  # Cap at 10%

class ArbitrageScanner:
    """Scan for various types of arbitrage opportunities"""

    def __init__(
        self,
        fee_rate: float = 0.02,
        min_liquidity_ratio: float = 0.1,
        max_slippage_pct: float = 2.0
    ):
        """
        Args:
            fee_rate: Trading fee rate (default 2%)
            min_liquidity_ratio: Max fraction of liquidity to use
            max_slippage_pct: Maximum acceptable slippage percentage
        """
        if not 0 <= fee_rate < 1:
            raise ValueError(f"fee_rate must be [0, 1), got {fee_rate}")

        self.fee_rate = fee_rate
        self.max_slippage_pct = max_slippage_pct
        self.opportunities = []
        self.liquidity_analyzer = LiquidityAnalyzer(min_liquidity_ratio)
    
    def check_multi_outcome_arb(self, outcomes: List[MarketData]) -> List[ArbitrageOpportunity]:
        """
        Type 1: Multi-outcome arbitrage
        If sum of all YES prices > $1, sell all outcomes
        If sum of all YES prices < $1, buy all outcomes
        """
        opportunities = []

        prices = [o.price for o in outcomes]
        total_price = sum(prices)

        # Account for fees
        # For buy-all: We pay total_price + fees on that purchase, guaranteed payout = $1
        # For sell-all: We receive total_price - fees on that sale, pay out = $1
        # Fee is charged on the transaction amount, not on profit
        effective_total_buy = total_price * (1 + self.fee_rate)  # Cost including fees
        effective_total_sell = total_price * (1 - self.fee_rate)  # Revenue after fees
        
        # Sell all opportunity (prices sum > $1)
        if effective_total_sell > 1.0:
            profit = effective_total_sell - 1.0
            opportunities.append(ArbitrageOpportunity(
                type="Multi-Outcome Sell All",
                markets=[o.market_id for o in outcomes],
                actions=["SELL"] * len(outcomes),
                prices=prices,
                profit=profit,
                profit_pct=(profit / 1.0) * 100,
                risk_score=0.1,  # Very low risk
                confidence=0.95
            ))
        
        # Buy all opportunity (prices sum < $1)
        if effective_total_buy < 1.0:
            profit = 1.0 - effective_total_buy
            opportunities.append(ArbitrageOpportunity(
                type="Multi-Outcome Buy All",
                markets=[o.market_id for o in outcomes],
                actions=["BUY"] * len(outcomes),
                prices=prices,
                profit=profit,
                profit_pct=(profit / effective_total_buy) * 100,
                risk_score=0.1,
                confidence=0.95
            ))
        
        return opportunities

    def evaluate_with_slippage(
        self,
        opportunity: ArbitrageOpportunity,
        order_books: Dict[str, List[OrderBookLevel]],
        position_size: float
    ) -> ArbitrageOpportunity:
        """
        Re-evaluate arbitrage opportunity accounting for slippage.

        Args:
            opportunity: Original arbitrage opportunity
            order_books: Dict mapping market_id to order book levels
            position_size: Desired position size in shares

        Returns:
            Updated ArbitrageOpportunity with effective prices and liquidity check
        """
        effective_prices = []
        total_slippage = 0.0
        liquidity_ok = True

        for market_id, action, price in zip(
            opportunity.markets,
            opportunity.actions,
            opportunity.prices
        ):
            order_book = order_books.get(market_id, [])
            is_buy = action == "BUY"

            if order_book:
                effective_price, slippage = self.liquidity_analyzer.calculate_effective_price(
                    price, position_size, order_book, is_buy
                )
                effective_prices.append(effective_price)
                total_slippage += slippage

                # Check liquidity
                total_liquidity = sum(level.size for level in order_book)
                liq_check = self.liquidity_analyzer.check_liquidity_sufficient(
                    position_size, total_liquidity
                )
                if not liq_check['sufficient']:
                    liquidity_ok = False
                    logger.warning(
                        f"Insufficient liquidity in {market_id}: "
                        f"need {position_size}, recommended max {liq_check['max_recommended_size']:.0f}"
                    )
            else:
                effective_prices.append(price)

        # Recalculate profit with effective prices
        if opportunity.type == "Multi-Outcome Buy All":
            effective_total = sum(effective_prices) * (1 + self.fee_rate)
            new_profit = 1.0 - effective_total
        elif opportunity.type == "Multi-Outcome Sell All":
            effective_total = sum(effective_prices) * (1 - self.fee_rate)
            new_profit = effective_total - 1.0
        else:
            # For pair trades, recalculate based on spread
            if len(effective_prices) >= 2:
                new_profit = (effective_prices[0] - effective_prices[1]) * (1 - 2 * self.fee_rate)
            else:
                new_profit = opportunity.profit

        # Check if still profitable after slippage
        avg_slippage = total_slippage / len(opportunity.markets) if opportunity.markets else 0

        return ArbitrageOpportunity(
            type=opportunity.type,
            markets=opportunity.markets,
            actions=opportunity.actions,
            prices=opportunity.prices,
            profit=new_profit,
            profit_pct=(new_profit / sum(effective_prices)) * 100 if sum(effective_prices) > 0 else 0,
            risk_score=opportunity.risk_score + (avg_slippage / 100),  # Increase risk for slippage
            confidence=opportunity.confidence * (1 - avg_slippage / 100),  # Reduce confidence
            effective_prices=effective_prices,
            liquidity_sufficient=liquidity_ok and avg_slippage <= self.max_slippage_pct
        )

    def check_temporal_inconsistency(self, 
                                     short_term: MarketData,
                                     long_term: MarketData) -> List[ArbitrageOpportunity]:
        """
        Type 2: Temporal inconsistency
        "Event by March" should be <= "Event by June"
        If short_term.price > long_term.price, arbitrage exists
        """
        opportunities = []
        
        if short_term.price > long_term.price:
            # Buy long-term, sell short-term
            profit = (short_term.price - long_term.price) * (1 - 2 * self.fee_rate)
            
            if profit > 0:
                opportunities.append(ArbitrageOpportunity(
                    type="Temporal Inconsistency",
                    markets=[short_term.market_id, long_term.market_id],
                    actions=["SELL", "BUY"],
                    prices=[short_term.price, long_term.price],
                    profit=profit,
                    profit_pct=(profit / long_term.price) * 100,
                    risk_score=0.2,
                    confidence=0.90
                ))
        
        return opportunities
    
    def check_subset_inconsistency(self,
                                   subset: MarketData,
                                   superset: MarketData) -> List[ArbitrageOpportunity]:
        """
        Type 3: Subset inconsistency
        "BTC hits $150k" should be >= "BTC hits $200k"
        If subset.price < superset.price, arbitrage exists
        """
        opportunities = []
        
        if subset.price < superset.price:
            # Buy subset, sell superset
            profit = (superset.price - subset.price) * (1 - 2 * self.fee_rate)
            
            if profit > 0:
                opportunities.append(ArbitrageOpportunity(
                    type="Subset Inconsistency",
                    markets=[subset.market_id, superset.market_id],
                    actions=["BUY", "SELL"],
                    prices=[subset.price, superset.price],
                    profit=profit,
                    profit_pct=(profit / subset.price) * 100,
                    risk_score=0.15,
                    confidence=0.92
                ))
        
        return opportunities


def validate_price(price: float, name: str = "price") -> None:
    """Validate that price is in valid range (0, 1)"""
    if not isinstance(price, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(price).__name__}")
    if not 0 < price < 1:
        raise ValueError(f"{name} must be between 0 and 1 (exclusive), got {price}")


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


class StatisticalArbitrage:
    """Statistical arbitrage using probability models"""

    def __init__(self, confidence_threshold: float = 0.6):
        if not 0 < confidence_threshold <= 1:
            raise ValueError(f"confidence_threshold must be (0, 1], got {confidence_threshold}")
        self.confidence_threshold = confidence_threshold
        self.historical_accuracy = {}

    def calculate_fair_value(self, features: Dict) -> float:
        """
        Calculate fair value probability using features
        This is a placeholder - implement your actual model here
        """
        # Example: Simple weighted average of indicators
        weights = {
            'base_rate': 0.4,
            'recent_trend': 0.3,
            'sentiment': 0.2,
            'volume_signal': 0.1
        }
        
        fair_value = sum(features.get(k, 0.5) * v for k, v in weights.items())
        return np.clip(fair_value, 0.01, 0.99)
    
    def calculate_edge(self, market_price: float, fair_value: float) -> float:
        """Calculate edge in cents"""
        return fair_value - market_price
    
    def calculate_kelly_fraction(self,
                                 market_price: float,
                                 fair_value: float,
                                 confidence: float = 0.8) -> float:
        """
        Calculate Kelly Criterion position size
        f* = (bp - q) / b

        Args:
            market_price: Current market price (0-1 exclusive)
            fair_value: Your estimated probability (0-1)
            confidence: Confidence in estimate (0-1)

        Returns:
            Kelly fraction (0-1), or 0 if no positive edge
        """
        validate_price(market_price, "market_price")
        validate_probability(fair_value, "fair_value")
        validate_probability(confidence, "confidence")

        if market_price >= 1.0 or market_price <= 0:
            return 0.0

        # Odds (decimal odds minus 1)
        b = (1.0 / market_price) - 1

        # Use fair_value directly as win probability (don't multiply by confidence here)
        # Confidence will be applied to the final sizing
        p = fair_value
        q = 1 - p

        # Kelly fraction: f* = (bp - q) / b
        kelly = (b * p - q) / b

        if kelly <= 0:
            return 0.0

        # Use fractional Kelly (1/4 Kelly for safety) and apply confidence
        # Confidence reduces position size when we're less certain
        return kelly * 0.25 * confidence
    
    def calculate_expected_value(self,
                                market_price: float,
                                fair_value: float) -> Tuple[float, float]:
        """
        Calculate expected value and ROI
        EV = (P_win × $1) - (P_lose × Cost)
        """
        ev = (fair_value * 1.0) - ((1 - fair_value) * market_price)
        roi = ev / market_price if market_price > 0 else 0
        return ev, roi
    
    def should_trade(self,
                    market_price: float,
                    fair_value: float,
                    min_edge: float = 0.05) -> Dict:
        """
        Determine if trade meets criteria

        Args:
            market_price: Current market price (0-1 exclusive)
            fair_value: Your estimated probability (0-1)
            min_edge: Minimum edge required to trade (default 5%)

        Returns:
            Dict with trade recommendation
        """
        validate_price(market_price, "market_price")
        validate_probability(fair_value, "fair_value")
        if min_edge < 0:
            raise ValueError(f"min_edge must be non-negative, got {min_edge}")

        edge = self.calculate_edge(market_price, fair_value)
        ev, roi = self.calculate_expected_value(market_price, fair_value)
        kelly = self.calculate_kelly_fraction(market_price, fair_value)
        
        recommendation = {
            'trade': abs(edge) >= min_edge and kelly > 0,
            'action': 'BUY' if edge > 0 else 'SELL',
            'edge': edge,
            'edge_pct': (edge / market_price) * 100,
            'expected_value': ev,
            'roi': roi * 100,
            'kelly_fraction': kelly,
            'suggested_size': kelly
        }
        
        return recommendation


class MeanReversionStrategy:
    """Mean reversion based on z-score"""
    
    def __init__(self, window: int = 20, z_threshold: float = 2.0):
        self.window = window
        self.z_threshold = z_threshold
        self.price_history = {}
    
    def add_price(self, market_id: str, price: float, fair_value: float):
        """Add price point to history"""
        if market_id not in self.price_history:
            self.price_history[market_id] = []
        
        self.price_history[market_id].append({
            'price': price,
            'fair_value': fair_value,
            'deviation': price - fair_value
        })
        
        # Keep only recent window
        if len(self.price_history[market_id]) > self.window:
            self.price_history[market_id].pop(0)
    
    def calculate_z_score(self, market_id: str) -> float:
        """Calculate z-score of current deviation"""
        if market_id not in self.price_history or len(self.price_history[market_id]) < 5:
            return 0.0
        
        deviations = [p['deviation'] for p in self.price_history[market_id]]
        
        mean_dev = np.mean(deviations)
        std_dev = np.std(deviations)
        
        if std_dev == 0:
            return 0.0
        
        current_dev = deviations[-1]
        z_score = (current_dev - mean_dev) / std_dev
        
        return z_score
    
    def get_signal(self, market_id: str) -> Dict:
        """Get mean reversion signal"""
        z_score = self.calculate_z_score(market_id)
        
        signal = {
            'z_score': z_score,
            'action': None,
            'strength': abs(z_score) / self.z_threshold
        }
        
        if z_score > self.z_threshold:
            signal['action'] = 'SELL'  # Price too high, expect reversion down
        elif z_score < -self.z_threshold:
            signal['action'] = 'BUY'   # Price too low, expect reversion up
        
        return signal


def generate_example_opportunities():
    """Generate example arbitrage opportunities for testing"""
    
    # Example 1: Multi-outcome arbitrage
    outcomes = [
        MarketData("market_1", "Outcome A", 0.28, 10000, 50000, datetime.now()),
        MarketData("market_1", "Outcome B", 0.26, 8000, 40000, datetime.now()),
        MarketData("market_1", "Outcome C", 0.25, 7000, 35000, datetime.now()),
        MarketData("market_1", "Outcome D", 0.24, 5000, 25000, datetime.now()),
    ]
    
    scanner = ArbitrageScanner(fee_rate=0.02)
    arb_opps = scanner.check_multi_outcome_arb(outcomes)
    
    # Example 2: Statistical arbitrage
    stat_arb = StatisticalArbitrage()
    features = {
        'base_rate': 0.65,
        'recent_trend': 0.70,
        'sentiment': 0.68,
        'volume_signal': 0.62
    }
    fair_value = stat_arb.calculate_fair_value(features)
    market_price = 0.55
    
    trade_rec = stat_arb.should_trade(market_price, fair_value, min_edge=0.05)
    
    return arb_opps, trade_rec, fair_value, market_price


if __name__ == "__main__":
    from logger import setup_logging
    setup_logging(level='INFO')

    arb_opps, trade_rec, fv, mp = generate_example_opportunities()

    logger.info("=== ARBITRAGE OPPORTUNITIES ===")
    for opp in arb_opps:
        logger.info(f"Type: {opp.type}")
        logger.info(f"Profit: ${opp.profit:.4f} ({opp.profit_pct:.2f}%)")
        logger.info(f"Actions: {opp.actions}")
        logger.info(f"Prices: {opp.prices}")
        logger.info(f"Risk Score: {opp.risk_score}")
        trading_logger.arbitrage_opportunity(opp.type, opp.profit, opp.profit_pct, opp.markets)

    logger.info("=== STATISTICAL ARBITRAGE ===")
    logger.info(f"Market Price: {mp:.2f}c")
    logger.info(f"Fair Value: {fv:.2f}c")
    logger.info(f"Edge: {trade_rec['edge']:.2f}c ({trade_rec['edge_pct']:.2f}%)")
    logger.info(f"Expected ROI: {trade_rec['roi']:.2f}%")
    logger.info(f"Kelly Fraction: {trade_rec['kelly_fraction']:.2%}")
    logger.info(f"Recommendation: {trade_rec['action'] if trade_rec['trade'] else 'PASS'}")
