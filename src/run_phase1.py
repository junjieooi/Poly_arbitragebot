"""
Phase 1 Runner: Data Pipeline & Simple Arbitrage Detection

This script runs all Phase 1 components:
1. WebSocket Feed Handler - Real-time order book updates
2. Polygon Monitor - On-chain trade monitoring
3. Simple Arbitrage Detector - Basic arbitrage detection

Usage:
    python -m src.run_phase1 [--config config/phase1_config.yaml]

Environment Variables:
    POLYGON_RPC_URL - Alchemy/Infura RPC URL for Polygon
    POLYMARKET_API_KEY - (optional) API key for authenticated endpoints
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.websocket_feed import WebSocketFeedHandler, OrderBookChangeEvent
from src.data.polygon_monitor import PolygonMonitor, ArbitrageSignal
from src.detection.simple_arbitrage import (
    SimpleArbitrageDetector,
    ArbitrageOpportunity
)

# Configure logging
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def setup_logging(config: Dict[str, Any]) -> None:
    """Configure logging from config."""
    log_config = config.get('logging', {})
    level = getattr(logging, log_config.get('level', 'INFO').upper())
    log_file = log_config.get('file', 'logs/phase1.log')
    log_format = log_config.get(
        'format',
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create log directory
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    logging.basicConfig(
        level=level,
        format=log_format,
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )


class Phase1Runner:
    """
    Orchestrates all Phase 1 components.

    Components:
    - WebSocket Feed: Real-time order book updates
    - Polygon Monitor: On-chain trade monitoring
    - Arbitrage Detector: Simple arbitrage detection
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Phase 1 runner.

        Args:
            config: Configuration dictionary from YAML
        """
        self.config = config
        self._running = False

        # Initialize components
        self._init_websocket_feed()
        self._init_polygon_monitor()
        self._init_arbitrage_detector()

        # Statistics
        self._start_time: Optional[datetime] = None
        self._stats_task: Optional[asyncio.Task] = None

    def _init_websocket_feed(self) -> None:
        """Initialize WebSocket feed handler."""
        ws_config = self.config.get('websocket', {})

        self.feed_handler = WebSocketFeedHandler(
            max_reconnect_attempts=ws_config.get('max_reconnect_attempts', 10),
            base_reconnect_delay=ws_config.get('base_reconnect_delay', 1.0),
            max_reconnect_delay=ws_config.get('max_reconnect_delay', 60.0)
        )

        # Set change threshold
        if 'change_threshold' in ws_config:
            self.feed_handler.CHANGE_THRESHOLD = ws_config['change_threshold']

        # Register callbacks
        self.feed_handler.on_book_change(self._on_book_change)

        logger.info("WebSocket feed handler initialized")

    def _init_polygon_monitor(self) -> None:
        """Initialize Polygon blockchain monitor."""
        poly_config = self.config.get('polygon', {})

        rpc_url = os.environ.get('POLYGON_RPC_URL', poly_config.get('rpc_url'))

        if not rpc_url:
            logger.warning(
                "No POLYGON_RPC_URL set. Polygon monitoring disabled. "
                "Set via environment variable or config."
            )
            self.polygon_monitor = None
            return

        self.polygon_monitor = PolygonMonitor(
            rpc_url=rpc_url,
            contract_address=poly_config.get('contract_address'),
            poll_interval=poly_config.get('poll_interval', 2.0)
        )

        # Set thresholds
        if 'arbitrage_threshold' in poly_config:
            self.polygon_monitor.ARBITRAGE_THRESHOLD = poly_config['arbitrage_threshold']
        if 'max_history_blocks' in poly_config:
            self.polygon_monitor.MAX_HISTORY_BLOCKS = poly_config['max_history_blocks']

        # Register callbacks
        self.polygon_monitor.on_arbitrage(self._on_chain_arbitrage)

        logger.info("Polygon monitor initialized")

    def _init_arbitrage_detector(self) -> None:
        """Initialize simple arbitrage detector."""
        arb_config = self.config.get('arbitrage', {})

        # Create data directory for database
        db_path = arb_config.get('database_path', 'data/arbitrage_opportunities.db')
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.arbitrage_detector = SimpleArbitrageDetector(
            feed_handler=self.feed_handler,
            db_path=db_path,
            check_interval=arb_config.get('check_interval', 0.5)
        )

        # Set thresholds
        if 'buy_threshold' in arb_config:
            self.arbitrage_detector.BUY_THRESHOLD = arb_config['buy_threshold']
        if 'sell_threshold' in arb_config:
            self.arbitrage_detector.SELL_THRESHOLD = arb_config['sell_threshold']
        if 'min_profit' in arb_config:
            self.arbitrage_detector.MIN_PROFIT = arb_config['min_profit']

        # Register markets from config
        markets = self.config.get('markets', [])
        for market in markets:
            self.arbitrage_detector.add_market(
                market_id=market['market_id'],
                yes_token_id=market['yes_token'],
                no_token_id=market['no_token'],
                question=market.get('question', '')
            )

            # Also register with Polygon monitor
            if self.polygon_monitor:
                self.polygon_monitor.register_market(
                    market['market_id'],
                    market['yes_token'],
                    market['no_token']
                )

        # Register callback
        self.arbitrage_detector.on_opportunity(self._on_arbitrage_opportunity)

        logger.info(f"Arbitrage detector initialized with {len(markets)} markets")

    def _on_book_change(self, event: OrderBookChangeEvent) -> None:
        """Handle significant order book change."""
        logger.info(
            f"Book change: {event.token_id[:20]}... "
            f"{event.side} {event.old_price:.4f} -> {event.new_price:.4f} "
            f"({event.change_pct*100:.1f}%)"
        )

    def _on_chain_arbitrage(self, signal: ArbitrageSignal) -> None:
        """Handle on-chain arbitrage signal from Polygon."""
        logger.warning(
            f"ON-CHAIN ARBITRAGE: Block {signal.block_number} "
            f"YES={signal.vwap_yes:.4f} NO={signal.vwap_no:.4f} "
            f"Deviation={signal.deviation:.4f} "
            f"Est. Profit=${signal.profit_estimate:.4f}"
        )

    def _on_arbitrage_opportunity(self, opp: ArbitrageOpportunity) -> None:
        """Handle detected arbitrage opportunity."""
        logger.warning(f"ARBITRAGE: {opp}")

    async def _stats_loop(self) -> None:
        """Periodically display statistics."""
        stats_config = self.config.get('stats', {})
        interval = stats_config.get('display_interval', 60)
        lookback = stats_config.get('lookback_hours', 24)

        while self._running:
            await asyncio.sleep(interval)

            if not self._running:
                break

            # Print stats
            self.arbitrage_detector.print_stats(hours=lookback)

            # Print Polygon stats if available
            if self.polygon_monitor:
                poly_stats = self.polygon_monitor.get_stats()
                logger.info(
                    f"Polygon: {poly_stats['blocks_processed']} blocks, "
                    f"{poly_stats['trades_found']} trades, "
                    f"{poly_stats['arbitrage_signals']} arb signals"
                )

    async def start(self) -> None:
        """Start all Phase 1 components."""
        if self._running:
            logger.warning("Already running")
            return

        self._running = True
        self._start_time = datetime.utcnow()

        logger.info("=" * 60)
        logger.info("PHASE 1: Starting Data Pipeline & Arbitrage Detection")
        logger.info("=" * 60)

        tasks = []

        # Start Polygon monitor in background
        if self.polygon_monitor:
            tasks.append(asyncio.create_task(
                self.polygon_monitor.start(),
                name="polygon_monitor"
            ))

        # Start statistics loop
        self._stats_task = asyncio.create_task(
            self._stats_loop(),
            name="stats_loop"
        )
        tasks.append(self._stats_task)

        # Start arbitrage detector (runs feed handler internally)
        tasks.append(asyncio.create_task(
            self.arbitrage_detector.start(),
            name="arbitrage_detector"
        ))

        logger.info(f"Started {len(tasks)} async tasks")

        # Wait for all tasks
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled")

    async def stop(self) -> None:
        """Stop all Phase 1 components gracefully."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping Phase 1 components...")

        # Stop components
        await self.arbitrage_detector.stop()

        if self.polygon_monitor:
            await self.polygon_monitor.stop()

        # Print final stats
        if self._start_time:
            runtime = datetime.utcnow() - self._start_time
            logger.info(f"Total runtime: {runtime}")

        self.arbitrage_detector.print_stats()

        logger.info("Phase 1 stopped")


async def fetch_active_markets(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Fetch active markets from Polymarket API.

    Returns list of markets with token IDs for monitoring.
    """
    import aiohttp

    logger.info("Fetching active markets from Polymarket...")

    url = "https://clob.polymarket.com/markets"
    params = {"next_cursor": "MA=="}  # First page

    markets = []

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=30) as resp:
                if resp.status != 200:
                    logger.error(f"API returned status {resp.status}")
                    return []

                data = await resp.json()

                for market in data.get('data', [])[:limit]:
                    # Extract tokens
                    tokens = market.get('tokens', [])
                    if len(tokens) >= 2:
                        yes_token = next(
                            (t for t in tokens if t.get('outcome') == 'Yes'),
                            tokens[0]
                        )
                        no_token = next(
                            (t for t in tokens if t.get('outcome') == 'No'),
                            tokens[1]
                        )

                        markets.append({
                            'market_id': market.get('condition_id', ''),
                            'question': market.get('question', ''),
                            'yes_token': yes_token.get('token_id', ''),
                            'no_token': no_token.get('token_id', '')
                        })

                logger.info(f"Found {len(markets)} active markets")

        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")

    return markets


async def main():
    """Main entry point for Phase 1."""
    parser = argparse.ArgumentParser(
        description="Phase 1: Data Pipeline & Simple Arbitrage Detection"
    )
    parser.add_argument(
        '--config',
        default='config/phase1_config.yaml',
        help='Path to configuration file'
    )
    parser.add_argument(
        '--fetch-markets',
        type=int,
        default=0,
        metavar='N',
        help='Fetch N active markets from API instead of using config'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print configuration and exit without running'
    )

    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if config_path.exists():
        config = load_config(str(config_path))
    else:
        logger.warning(f"Config file not found: {config_path}")
        config = {}

    # Setup logging
    setup_logging(config)

    # Fetch markets if requested
    if args.fetch_markets > 0:
        markets = await fetch_active_markets(args.fetch_markets)
        config['markets'] = markets

        # Print fetched markets
        print("\nFetched Markets:")
        for m in markets:
            print(f"  - {m['question'][:60]}...")
            print(f"    YES: {m['yes_token'][:40]}...")
            print(f"    NO:  {m['no_token'][:40]}...")
        print()

    # Dry run
    if args.dry_run:
        print("\nConfiguration:")
        print(yaml.dump(config, default_flow_style=False))
        return

    # Validate we have markets
    if not config.get('markets'):
        print("\nNo markets configured!")
        print("Either:")
        print("  1. Add markets to config/phase1_config.yaml")
        print("  2. Use --fetch-markets N to fetch from API")
        print("\nExample:")
        print("  python -m src.run_phase1 --fetch-markets 10")
        return

    # Create runner
    runner = Phase1Runner(config)

    # Handle signals for graceful shutdown
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(runner.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Run
    try:
        await runner.start()
    except KeyboardInterrupt:
        pass
    finally:
        await runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
