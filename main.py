"""
Polymarket Arbitrage Bot - Main Entry Point

This is the main application entry point that coordinates all components:
- Configuration management
- Polymarket API client
- Arbitrage detection
- Risk management
- Position tracking

Usage:
    python main.py                  # Run with default settings
    python main.py --paper          # Paper trading mode (default)
    python main.py --live           # Live trading mode (requires API keys)
    python main.py --scan-only      # Only scan for opportunities, don't trade
    python main.py --backtest       # Run Monte Carlo simulation
"""

import asyncio
import argparse
import signal
import sys
from datetime import datetime
from typing import Optional

from config import get_config, Config
from logger import setup_logging, get_logger, TradingLogger
from polymarket_client import PolymarketClient, Market, OrderSide
from arbitrage_scanner import (
    ArbitrageScanner,
    StatisticalArbitrage,
    MarketData,
)
from risk_management import RiskManager, KellyCriterion, Position
from monte_carlo_simulation import MonteCarloSimulator, compare_strategies
from telegram_alerts import TelegramNotifier, TelegramConfig

# Low-risk mode indicator
LOW_RISK_MODE = True

logger = get_logger(__name__)
trading_logger = TradingLogger(__name__)


class PolymarketBot:
    """Main bot class coordinating all components"""

    def __init__(self, config: Config):
        self.config = config
        self.running = False

        # Initialize components
        self.client: Optional[PolymarketClient] = None
        self.scanner = ArbitrageScanner(
            fee_rate=config.trading.fee_rate,
            min_liquidity_ratio=config.trading.min_liquidity_ratio,
        )
        # Only initialize stat_arb if not in pure arbitrage mode
        self.stat_arb = None if config.trading.pure_arbitrage_only else StatisticalArbitrage()
        self.pure_arbitrage_only = config.trading.pure_arbitrage_only
        self.use_fixed_sizing = config.risk.use_fixed_sizing
        self.fixed_position_pct = config.risk.fixed_position_pct
        self.risk_manager = RiskManager(
            max_position_size=config.risk.max_position_size,
            max_market_exposure=config.risk.max_market_exposure,
            stop_loss_pct=config.risk.stop_loss_pct,
            daily_loss_limit=config.risk.daily_loss_limit,
            max_drawdown_limit=config.risk.max_drawdown_limit,
        )

        # State
        self.positions: list[Position] = []
        self.portfolio_value = config.simulation.initial_bankroll

        # Telegram alerts
        self.telegram: Optional[TelegramNotifier] = None
        if config.telegram.enabled and config.telegram.bot_token and config.telegram.chat_id:
            self.telegram = TelegramNotifier(TelegramConfig(
                bot_token=config.telegram.bot_token,
                chat_id=config.telegram.chat_id,
                enabled=config.telegram.enabled
            ))
            logger.info("Telegram alerts enabled")

    async def start(self):
        """Initialize and start the bot"""
        logger.info("=" * 60)
        logger.info("Polymarket Arbitrage Bot Starting")
        logger.info("=" * 60)

        # Validate configuration
        errors = self.config.validate()
        if errors:
            for error in errors:
                logger.error(f"Config error: {error}")
            if not self.config.is_paper_trading:
                raise ValueError("Configuration errors prevent live trading")

        logger.info(f"Mode: {'Paper Trading' if self.config.is_paper_trading else 'LIVE TRADING'}")
        logger.info(f"Strategy: {'PURE ARBITRAGE ONLY (Low Risk)' if self.pure_arbitrage_only else 'All Strategies'}")
        logger.info(f"Position Sizing: {'FIXED ' + str(self.fixed_position_pct*100) + '%' if self.use_fixed_sizing else 'Kelly ' + str(self.config.risk.kelly_fraction*100) + '%'}")
        logger.info(f"Initial Bankroll: ${self.portfolio_value:,.2f}")
        logger.info(f"Fee Rate: {self.config.trading.fee_rate:.2%}")
        logger.info(f"Min Edge Required: {self.config.trading.min_edge:.2%}")
        logger.info(f"Max Position Size: {self.config.risk.max_position_size:.2%}")
        logger.info(f"Daily Loss Limit: {self.config.risk.daily_loss_limit:.2%}")
        logger.info(f"Max Drawdown Limit: {self.config.risk.max_drawdown_limit:.2%}")

        # Initialize API client (skip if already set, e.g., for demo mode)
        if self.client is None:
            self.client = PolymarketClient()
        await self.client.connect()

        # Start daily tracking
        self.risk_manager.start_trading_day(self.portfolio_value)

        self.running = True
        logger.info("Bot initialized successfully")

        # Send Telegram startup alert
        if self.telegram:
            strategy = "Pure Arbitrage (Low Risk)" if self.pure_arbitrage_only else "All Strategies"
            await self.telegram.alert_bot_started(
                mode="Paper Trading" if self.config.is_paper_trading else "LIVE",
                bankroll=self.portfolio_value,
                strategy=strategy
            )

    async def stop(self):
        """Gracefully stop the bot"""
        logger.info("Shutting down...")
        self.running = False

        # Send Telegram shutdown alert
        if self.telegram:
            await self.telegram.alert_bot_stopped("Manual shutdown")
            await self.telegram.close()

        if self.client:
            await self.client.close()

        logger.info("Bot stopped")

    async def scan_opportunities(self) -> list:
        """Scan for arbitrage opportunities"""
        opportunities = []

        try:
            # Fetch active markets
            markets = await self.client.get_markets(limit=50, active=True)
            logger.info(f"Scanning {len(markets)} active markets...")

            # Group markets by related conditions for multi-outcome analysis
            for market in markets:
                if not market.tokens or len(market.tokens) < 2:
                    continue

                # Create MarketData for each outcome
                outcomes = []
                for token in market.tokens:
                    token_id = token.get('token_id')
                    if not token_id:
                        continue

                    price = await self.client.get_price(token_id)
                    if price is None:
                        continue

                    outcomes.append(MarketData(
                        market_id=token_id,
                        outcome=token.get('outcome', 'Unknown'),
                        price=price,
                        volume=market.volume,
                        liquidity=market.liquidity,
                        timestamp=datetime.now()
                    ))

                if len(outcomes) >= 2:
                    # Check for multi-outcome arbitrage
                    arb_opps = self.scanner.check_multi_outcome_arb(outcomes)
                    for opp in arb_opps:
                        if opp.profit > 0:
                            trading_logger.arbitrage_opportunity(
                                opp.type, opp.profit, opp.profit_pct, opp.markets
                            )
                            # Send Telegram alert for opportunity
                            if self.telegram:
                                await self.telegram.alert_arbitrage_opportunity(
                                    opp_type=opp.type,
                                    profit=opp.profit,
                                    profit_pct=opp.profit_pct,
                                    markets=opp.markets,
                                    executed=False
                                )
                            opportunities.append({
                                'market': market,
                                'opportunity': opp,
                                'outcomes': outcomes
                            })

        except Exception as e:
            logger.error(f"Error scanning opportunities: {e}")
            trading_logger.api_error("scan_opportunities", str(e))

        return opportunities

    async def evaluate_opportunity(self, opp_data: dict) -> Optional[dict]:
        """Evaluate an opportunity and generate trade recommendation"""
        opp = opp_data['opportunity']
        market = opp_data['market']

        # Check if trading is allowed
        allowed, reason = self.risk_manager.should_allow_trade()
        if not allowed:
            logger.warning(f"Trading not allowed: {reason}")
            return None

        # Check minimum edge requirement (low-risk filter)
        if opp.profit_pct < self.config.trading.min_edge * 100:
            logger.debug(f"Edge {opp.profit_pct:.2f}% below minimum {self.config.trading.min_edge*100:.2f}%")
            return None

        # Calculate position size - USE FIXED SIZING FOR LOW RISK
        if self.use_fixed_sizing:
            # Fixed percentage sizing (safer than Kelly)
            position_fraction = self.fixed_position_pct
            logger.debug(f"Using fixed position sizing: {position_fraction:.2%}")
        else:
            # Kelly-based sizing
            position_fraction = KellyCriterion.kelly_from_prices(
                market_price=sum(opp.prices) / len(opp.prices),
                fair_value=1.0 / len(opp.prices),  # Assume fair split for arb
                fraction=self.config.risk.kelly_fraction
            )

        shares = self.risk_manager.calculate_position_size(
            bankroll=self.portfolio_value,
            kelly_fraction=position_fraction,
            market_price=opp.prices[0]
        )

        if shares == 0:
            logger.debug("Position size too small")
            return None

        return {
            'market': market,
            'opportunity': opp,
            'shares': shares,
            'position_fraction': position_fraction,
            'estimated_profit': opp.profit * shares
        }

    async def execute_trade(self, trade: dict) -> bool:
        """Execute a trade (paper or live)"""
        opp = trade['opportunity']
        shares = trade['shares']

        if self.config.is_paper_trading:
            # Paper trading - simulate execution
            logger.info(f"[PAPER] Executing {opp.type}")
            for market_id, action, price in zip(opp.markets, opp.actions, opp.prices):
                trading_logger.trade_executed(
                    market_id,
                    action,
                    shares,
                    price,
                    paper=True
                )

            # Update simulated P&L
            self.portfolio_value += trade['estimated_profit']
            risk_status = self.risk_manager.update_pnl(trade['estimated_profit'], self.portfolio_value)

            # Send Telegram trade alert
            if self.telegram:
                await self.telegram.alert_trade_executed(
                    action=opp.actions[0] if opp.actions else "TRADE",
                    market_id=opp.markets[0] if opp.markets else "unknown",
                    shares=shares,
                    price=opp.prices[0] if opp.prices else 0,
                    estimated_profit=trade['estimated_profit'],
                    paper=True
                )

            # Check if risk limit was hit
            if risk_status.get('halt_trading'):
                if self.telegram:
                    if 'daily' in risk_status.get('reason', '').lower():
                        await self.telegram.alert_daily_limit_hit(
                            daily_loss_pct=risk_status.get('daily_loss_pct', 0),
                            limit_pct=self.config.risk.daily_loss_limit * 100
                        )
                    else:
                        await self.telegram.alert_drawdown_limit_hit(
                            drawdown_pct=risk_status.get('drawdown_pct', 0),
                            limit_pct=self.config.risk.max_drawdown_limit * 100
                        )

            return True
        else:
            # Live trading
            logger.info(f"[LIVE] Executing {opp.type}")

            try:
                for market_id, action, price in zip(opp.markets, opp.actions, opp.prices):
                    side = OrderSide.BUY if action == "BUY" else OrderSide.SELL

                    order = await self.client.create_order(
                        token_id=market_id,
                        side=side,
                        price=price,
                        size=shares
                    )

                    if order:
                        trading_logger.trade_executed(
                            market_id,
                            action,
                            shares,
                            price,
                            order_id=order.id
                        )
                        # Send Telegram alert for live trade
                        if self.telegram:
                            await self.telegram.alert_trade_executed(
                                action=action,
                                market_id=market_id,
                                shares=shares,
                                price=price,
                                estimated_profit=trade['estimated_profit'],
                                paper=False
                            )
                    else:
                        logger.error(f"Order failed for {market_id}")
                        if self.telegram:
                            await self.telegram.alert_error("Order Failed", f"Failed to execute order for {market_id}")
                        return False

                return True

            except Exception as e:
                logger.error(f"Trade execution failed: {e}")
                trading_logger.api_error("execute_trade", str(e))
                if self.telegram:
                    await self.telegram.alert_error("Trade Execution", str(e))
                return False

    async def run_main_loop(self, scan_interval: int = 60):
        """Main trading loop"""
        logger.info(f"Starting main loop (scan interval: {scan_interval}s)")

        while self.running:
            try:
                # Scan for opportunities
                opportunities = await self.scan_opportunities()
                logger.info(f"Found {len(opportunities)} opportunities")

                # Evaluate and potentially execute
                for opp_data in opportunities:
                    trade = await self.evaluate_opportunity(opp_data)
                    if trade:
                        success = await self.execute_trade(trade)
                        if success:
                            logger.info(f"Trade executed successfully")

                # Wait before next scan
                await asyncio.sleep(scan_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)  # Brief pause before retry

    async def run_scan_only(self):
        """Scan and report opportunities without trading"""
        logger.info("Running in scan-only mode")

        opportunities = await self.scan_opportunities()

        if not opportunities:
            logger.info("No arbitrage opportunities found")
            return

        logger.info(f"\n{'=' * 60}")
        logger.info("ARBITRAGE OPPORTUNITIES FOUND")
        logger.info(f"{'=' * 60}")

        for i, opp_data in enumerate(opportunities, 1):
            opp = opp_data['opportunity']
            market = opp_data['market']

            logger.info(f"\n{i}. {opp.type}")
            logger.info(f"   Market: {market.question[:60]}...")
            logger.info(f"   Profit: ${opp.profit:.4f} ({opp.profit_pct:.2f}%)")
            logger.info(f"   Prices: {opp.prices}")
            logger.info(f"   Risk Score: {opp.risk_score:.2f}")
            logger.info(f"   Confidence: {opp.confidence:.2%}")


def run_backtest():
    """Run Monte Carlo backtest simulation"""
    logger.info("Running Monte Carlo backtest simulation...")
    logger.info("=" * 60)

    results = compare_strategies()

    return results


async def main_async(args):
    """Async main function"""
    config = get_config()

    # Override config based on args
    if args.live:
        config.is_paper_trading = False
    if args.paper:
        config.is_paper_trading = True

    bot = PolymarketBot(config)

    # Use mock client for demo mode
    if args.demo:
        from mock_client import MockPolymarketClient
        bot.client = MockPolymarketClient(volatility=0.02, create_arbitrage=True)
        logger.info("Using MOCK client (demo mode)")

    # Set up signal handlers
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        bot.running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await bot.start()

        if args.scan_only:
            await bot.run_scan_only()
        else:
            await bot.run_main_loop(scan_interval=args.interval)

    finally:
        await bot.stop()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Polymarket Arbitrage Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python main.py                  # Paper trading mode
    python main.py --scan-only      # Just scan for opportunities
    python main.py --backtest       # Run simulation
    python main.py --live           # Live trading (requires API keys)
        """
    )

    parser.add_argument(
        '--paper',
        action='store_true',
        default=True,
        help='Run in paper trading mode (default)'
    )
    parser.add_argument(
        '--live',
        action='store_true',
        help='Run in live trading mode (requires API keys)'
    )
    parser.add_argument(
        '--scan-only',
        action='store_true',
        help='Only scan for opportunities, do not trade'
    )
    parser.add_argument(
        '--backtest',
        action='store_true',
        help='Run Monte Carlo backtest simulation'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=60,
        help='Scan interval in seconds (default: 60)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    parser.add_argument(
        '--demo',
        action='store_true',
        help='Run with mock data (no API needed)'
    )

    args = parser.parse_args()

    # Setup logging
    log_level = 'DEBUG' if args.debug else 'INFO'
    setup_logging(level=log_level)

    logger.info("Polymarket Arbitrage Bot")
    logger.info(f"Started at {datetime.now().isoformat()}")

    if args.backtest:
        run_backtest()
        return

    # Run async main
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
