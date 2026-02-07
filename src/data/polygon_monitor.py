"""
Polygon Blockchain Monitor for Polymarket CLOB.

Monitors the Polymarket CLOB contract on Polygon to detect on-chain trades
and calculate VWAP per block. Detects arbitrage when YES + NO VWAP != $1.00.

Contract: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045 (Polymarket CLOB)

Key Events:
- OrderFilled: Emitted when an order is filled
- OrdersMatched: Emitted when orders match on the orderbook

Block Time: ~2 seconds on Polygon
"""

import asyncio
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from web3 import AsyncWeb3, Web3
    from web3.exceptions import Web3Exception
    from web3.middleware import ExtraDataToPOAMiddleware
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False
    Web3 = None
    AsyncWeb3 = None
    Web3Exception = Exception
    ExtraDataToPOAMiddleware = None

# Configure logging
logger = logging.getLogger(__name__)


class TradeSide(Enum):
    """Trade side (buy/sell)."""
    BUY = "buy"
    SELL = "sell"


@dataclass
class Trade:
    """On-chain trade record."""
    tx_hash: str
    block_number: int
    block_timestamp: int
    token_id: str
    side: TradeSide
    price: float  # 0-1 representing probability
    size: float  # Amount in USDC (6 decimals)
    maker: str
    taker: str
    fee: float

    @property
    def is_yes(self) -> bool:
        """Check if this is a YES token trade (heuristic based on token ID)."""
        # Token IDs for YES typically have certain patterns
        # This is a simplification - actual implementation needs market mapping
        return True  # Placeholder - needs real logic

    @property
    def value(self) -> float:
        """Trade value in USDC."""
        return self.price * self.size


@dataclass
class BlockVWAP:
    """VWAP calculation for a single block."""
    block_number: int
    block_timestamp: int
    yes_trades: List[Trade] = field(default_factory=list)
    no_trades: List[Trade] = field(default_factory=list)

    @property
    def vwap_yes(self) -> Optional[float]:
        """Calculate VWAP for YES trades in this block."""
        if not self.yes_trades:
            return None
        total_value = sum(t.price * t.size for t in self.yes_trades)
        total_size = sum(t.size for t in self.yes_trades)
        return total_value / total_size if total_size > 0 else None

    @property
    def vwap_no(self) -> Optional[float]:
        """Calculate VWAP for NO trades in this block."""
        if not self.no_trades:
            return None
        total_value = sum(t.price * t.size for t in self.no_trades)
        total_size = sum(t.size for t in self.no_trades)
        return total_value / total_size if total_size > 0 else None

    @property
    def has_arbitrage(self) -> bool:
        """Check if this block shows arbitrage (YES + NO != $1)."""
        if self.vwap_yes is None or self.vwap_no is None:
            return False
        deviation = abs(self.vwap_yes + self.vwap_no - 1.0)
        return deviation > 0.02  # 2% threshold

    @property
    def arbitrage_profit(self) -> float:
        """Calculate potential arbitrage profit per $1 risked."""
        if self.vwap_yes is None or self.vwap_no is None:
            return 0.0
        total = self.vwap_yes + self.vwap_no
        if total < 1.0:
            return 1.0 - total  # Buy both for < $1
        else:
            return total - 1.0  # Sell both for > $1


@dataclass
class ArbitrageSignal:
    """Arbitrage signal detected from blockchain data."""
    block_number: int
    timestamp: datetime
    market_id: str
    vwap_yes: float
    vwap_no: float
    deviation: float
    profit_estimate: float
    volume_yes: float
    volume_no: float


# Polymarket CLOB Contract ABI (simplified - key events only)
CLOB_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "orderHash", "type": "bytes32"},
            {"indexed": True, "name": "maker", "type": "address"},
            {"indexed": True, "name": "taker", "type": "address"},
            {"indexed": False, "name": "makerAssetId", "type": "uint256"},
            {"indexed": False, "name": "takerAssetId", "type": "uint256"},
            {"indexed": False, "name": "makerAmountFilled", "type": "uint256"},
            {"indexed": False, "name": "takerAmountFilled", "type": "uint256"},
            {"indexed": False, "name": "fee", "type": "uint256"},
        ],
        "name": "OrderFilled",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "takerOrderHash", "type": "bytes32"},
            {"indexed": True, "name": "takerOrderMaker", "type": "address"},
            {"indexed": False, "name": "makerAssetId", "type": "uint256"},
            {"indexed": False, "name": "takerAssetId", "type": "uint256"},
            {"indexed": False, "name": "makerAmountFilled", "type": "uint256"},
            {"indexed": False, "name": "takerAmountFilled", "type": "uint256"},
        ],
        "name": "OrdersMatched",
        "type": "event"
    }
]


class PolygonMonitor:
    """
    Real-time Polygon blockchain monitor for Polymarket trades.

    Features:
    - Real-time block processing (~2 second intervals)
    - Trade extraction and VWAP calculation
    - Arbitrage detection based on VWAP deviation
    - Historical data storage (last 950 blocks ~30 minutes)

    Usage:
        monitor = PolygonMonitor(rpc_url="https://polygon-rpc.com")
        monitor.on_arbitrage(my_callback)
        await monitor.start()
    """

    # Polymarket CLOB contract on Polygon
    CLOB_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

    # USDC on Polygon (6 decimals)
    USDC_DECIMALS = 6

    # Token price decimals (Polymarket uses 6)
    PRICE_DECIMALS = 6

    # History size (950 blocks ≈ 30 minutes at 2 sec/block)
    MAX_HISTORY_BLOCKS = 950

    # Arbitrage detection threshold
    ARBITRAGE_THRESHOLD = 0.02  # 2%

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        contract_address: Optional[str] = None,
        poll_interval: float = 2.0
    ):
        """
        Initialize Polygon monitor.

        Args:
            rpc_url: Polygon RPC URL (Alchemy, Infura, or public)
            contract_address: Override CLOB contract address
            poll_interval: Block polling interval in seconds
        """
        if not HAS_WEB3:
            raise ImportError("web3 package required. Install with: pip install web3>=6.0.0")

        self.rpc_url = rpc_url or os.environ.get(
            "POLYGON_RPC_URL",
            "https://polygon-rpc.com"
        )
        self.contract_address = Web3.to_checksum_address(
            contract_address or self.CLOB_CONTRACT
        )
        self.poll_interval = poll_interval

        # Initialize Web3
        self._w3: Optional[Web3] = None
        self._contract = None

        # State
        self._running = False
        self._last_block = 0
        self._trades: Dict[int, List[Trade]] = defaultdict(list)
        self._vwap_history: Dict[int, BlockVWAP] = {}
        self._market_token_map: Dict[str, Tuple[str, str]] = {}  # market_id -> (yes_token, no_token)

        # Callbacks
        self._arbitrage_callbacks: List[Callable[[ArbitrageSignal], None]] = []
        self._trade_callbacks: List[Callable[[Trade], None]] = []

        # Stats
        self._blocks_processed = 0
        self._trades_found = 0
        self._arbitrage_signals = 0

    async def initialize(self) -> None:
        """Initialize Web3 connection and contract."""
        logger.info(f"Connecting to Polygon RPC: {self.rpc_url[:50]}...")

        # Use async Web3 for better performance
        self._w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self.rpc_url))

        # Add POA middleware for Polygon
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        # Verify connection
        try:
            chain_id = await self._w3.eth.chain_id
            if chain_id != 137:
                logger.warning(f"Not on Polygon mainnet (chain_id={chain_id})")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Polygon: {e}")

        # Initialize contract
        self._contract = self._w3.eth.contract(
            address=self.contract_address,
            abi=CLOB_ABI
        )

        # Get latest block
        self._last_block = await self._w3.eth.block_number

        logger.info(
            f"Connected to Polygon. Latest block: {self._last_block}, "
            f"Contract: {self.contract_address}"
        )

    async def start(self) -> None:
        """Start monitoring Polygon for trades."""
        if self._running:
            logger.warning("Monitor already running")
            return

        if not self._w3:
            await self.initialize()

        self._running = True
        logger.info("Starting Polygon monitor...")

        try:
            while self._running:
                await self._process_new_blocks()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("Monitor cancelled")
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            raise
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop the monitor."""
        self._running = False
        logger.info("Polygon monitor stopped")

    def on_arbitrage(self, callback: Callable[[ArbitrageSignal], None]) -> None:
        """Register callback for arbitrage signals."""
        self._arbitrage_callbacks.append(callback)

    def on_trade(self, callback: Callable[[Trade], None]) -> None:
        """Register callback for new trades."""
        self._trade_callbacks.append(callback)

    def register_market(
        self,
        market_id: str,
        yes_token_id: str,
        no_token_id: str
    ) -> None:
        """
        Register a market's token mapping.

        Args:
            market_id: Market identifier
            yes_token_id: YES token condition ID
            no_token_id: NO token condition ID
        """
        self._market_token_map[market_id] = (yes_token_id, no_token_id)
        logger.info(f"Registered market {market_id}")

    async def _process_new_blocks(self) -> None:
        """Process all new blocks since last check."""
        current_block = await self._w3.eth.block_number

        if current_block <= self._last_block:
            return

        # Process each new block
        for block_num in range(self._last_block + 1, current_block + 1):
            try:
                await self._process_block(block_num)
                self._blocks_processed += 1
            except Exception as e:
                logger.error(f"Error processing block {block_num}: {e}")

        self._last_block = current_block

        # Prune old history
        self._prune_history()

    async def _process_block(self, block_number: int) -> None:
        """Process a single block for trades."""
        logger.debug(f"Processing block {block_number}")

        # Get block for timestamp
        block = await self._w3.eth.get_block(block_number)
        block_timestamp = block.timestamp

        # Get OrderFilled events
        event_filter = {
            "fromBlock": block_number,
            "toBlock": block_number,
            "address": self.contract_address
        }

        logs = await self._w3.eth.get_logs(event_filter)

        if not logs:
            return

        block_vwap = BlockVWAP(
            block_number=block_number,
            block_timestamp=block_timestamp
        )

        for log in logs:
            try:
                trade = self._decode_trade_event(log, block_number, block_timestamp)
                if trade:
                    self._trades[block_number].append(trade)
                    self._trades_found += 1

                    # Classify as YES or NO and add to VWAP
                    if self._is_yes_trade(trade):
                        block_vwap.yes_trades.append(trade)
                    else:
                        block_vwap.no_trades.append(trade)

                    # Emit trade callback
                    for callback in self._trade_callbacks:
                        try:
                            callback(trade)
                        except Exception as e:
                            logger.error(f"Error in trade callback: {e}")

            except Exception as e:
                logger.warning(f"Failed to decode log: {e}")

        # Store VWAP and check for arbitrage
        if block_vwap.yes_trades or block_vwap.no_trades:
            self._vwap_history[block_number] = block_vwap

            if block_vwap.has_arbitrage:
                self._emit_arbitrage_signal(block_vwap)

    def _decode_trade_event(
        self,
        log: Any,
        block_number: int,
        block_timestamp: int
    ) -> Optional[Trade]:
        """Decode an OrderFilled event log into a Trade."""
        try:
            # Decode the event data
            # This is simplified - actual decoding depends on exact event structure
            topics = log.topics
            data = log.data

            if len(topics) < 4:
                return None

            # Extract indexed parameters
            order_hash = topics[1].hex() if hasattr(topics[1], 'hex') else topics[1]
            maker = self._w3.to_checksum_address(topics[2][-20:])
            taker = self._w3.to_checksum_address(topics[3][-20:])

            # Decode non-indexed parameters from data
            # Data layout: makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee
            # Each is 32 bytes (256 bits)

            if isinstance(data, str):
                data = bytes.fromhex(data[2:]) if data.startswith("0x") else bytes.fromhex(data)

            if len(data) < 160:  # 5 * 32 bytes
                return None

            maker_asset_id = int.from_bytes(data[0:32], "big")
            taker_asset_id = int.from_bytes(data[32:64], "big")
            maker_amount = int.from_bytes(data[64:96], "big")
            taker_amount = int.from_bytes(data[96:128], "big")
            fee = int.from_bytes(data[128:160], "big")

            # Convert to standard units
            # Prices are in 6 decimal precision (0-1 range as millionths)
            # Amounts are in USDC (6 decimals)
            price = taker_amount / (10 ** self.PRICE_DECIMALS) if taker_amount > 0 else 0
            size = maker_amount / (10 ** self.USDC_DECIMALS)
            fee_usd = fee / (10 ** self.USDC_DECIMALS)

            # Determine side based on asset IDs
            # This is a heuristic - actual logic depends on Polymarket specifics
            side = TradeSide.BUY  # Simplified

            return Trade(
                tx_hash=log.transactionHash.hex() if hasattr(log.transactionHash, 'hex') else str(log.transactionHash),
                block_number=block_number,
                block_timestamp=block_timestamp,
                token_id=str(maker_asset_id),
                side=side,
                price=min(price, 1.0),  # Cap at 1.0
                size=size,
                maker=maker,
                taker=taker,
                fee=fee_usd
            )

        except Exception as e:
            logger.debug(f"Failed to decode trade: {e}")
            return None

    def _is_yes_trade(self, trade: Trade) -> bool:
        """
        Determine if a trade is for a YES token.

        This requires knowing the market structure. For now, use a heuristic
        based on token ID patterns.
        """
        # Check registered markets
        for market_id, (yes_token, no_token) in self._market_token_map.items():
            if trade.token_id == yes_token:
                return True
            if trade.token_id == no_token:
                return False

        # Heuristic: YES tokens often have lower token IDs
        # This is a placeholder - real implementation needs market data
        return int(trade.token_id) % 2 == 0

    def _emit_arbitrage_signal(self, block_vwap: BlockVWAP) -> None:
        """Emit arbitrage signal to callbacks."""
        if block_vwap.vwap_yes is None or block_vwap.vwap_no is None:
            return

        signal = ArbitrageSignal(
            block_number=block_vwap.block_number,
            timestamp=datetime.fromtimestamp(block_vwap.block_timestamp),
            market_id="unknown",  # Would need market lookup
            vwap_yes=block_vwap.vwap_yes,
            vwap_no=block_vwap.vwap_no,
            deviation=abs(block_vwap.vwap_yes + block_vwap.vwap_no - 1.0),
            profit_estimate=block_vwap.arbitrage_profit,
            volume_yes=sum(t.size for t in block_vwap.yes_trades),
            volume_no=sum(t.size for t in block_vwap.no_trades)
        )

        self._arbitrage_signals += 1

        logger.info(
            f"ARBITRAGE DETECTED at block {block_vwap.block_number}: "
            f"YES={block_vwap.vwap_yes:.4f} NO={block_vwap.vwap_no:.4f} "
            f"Deviation={signal.deviation:.4f} Profit={signal.profit_estimate:.4f}"
        )

        for callback in self._arbitrage_callbacks:
            try:
                callback(signal)
            except Exception as e:
                logger.error(f"Error in arbitrage callback: {e}")

    def _prune_history(self) -> None:
        """Remove old trades beyond history limit."""
        if not self._trades:
            return

        min_block = self._last_block - self.MAX_HISTORY_BLOCKS

        # Prune trades
        old_blocks = [b for b in self._trades.keys() if b < min_block]
        for block in old_blocks:
            del self._trades[block]

        # Prune VWAP history
        old_vwap = [b for b in self._vwap_history.keys() if b < min_block]
        for block in old_vwap:
            del self._vwap_history[block]

    def get_recent_trades(
        self,
        blocks: int = 100,
        token_id: Optional[str] = None
    ) -> List[Trade]:
        """Get recent trades, optionally filtered by token."""
        min_block = self._last_block - blocks
        trades = []

        for block_num, block_trades in self._trades.items():
            if block_num < min_block:
                continue
            for trade in block_trades:
                if token_id is None or trade.token_id == token_id:
                    trades.append(trade)

        return sorted(trades, key=lambda t: t.block_number, reverse=True)

    def get_vwap_history(
        self,
        blocks: int = 100
    ) -> List[BlockVWAP]:
        """Get VWAP history for recent blocks."""
        min_block = self._last_block - blocks
        return [
            vwap for block_num, vwap in sorted(self._vwap_history.items())
            if block_num >= min_block
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get monitor statistics."""
        return {
            "running": self._running,
            "last_block": self._last_block,
            "blocks_processed": self._blocks_processed,
            "trades_found": self._trades_found,
            "arbitrage_signals": self._arbitrage_signals,
            "history_blocks": len(self._trades),
            "registered_markets": len(self._market_token_map)
        }

    def calculate_rolling_vwap(
        self,
        blocks: int = 50,
        token_id: Optional[str] = None
    ) -> Dict[str, float]:
        """
        Calculate rolling VWAP over recent blocks.

        Returns:
            Dict with 'yes_vwap', 'no_vwap', 'volume_yes', 'volume_no'
        """
        min_block = self._last_block - blocks

        yes_value = 0.0
        yes_volume = 0.0
        no_value = 0.0
        no_volume = 0.0

        for block_num, vwap in self._vwap_history.items():
            if block_num < min_block:
                continue

            for trade in vwap.yes_trades:
                if token_id is None or trade.token_id == token_id:
                    yes_value += trade.price * trade.size
                    yes_volume += trade.size

            for trade in vwap.no_trades:
                if token_id is None or trade.token_id == token_id:
                    no_value += trade.price * trade.size
                    no_volume += trade.size

        return {
            "yes_vwap": yes_value / yes_volume if yes_volume > 0 else None,
            "no_vwap": no_value / no_volume if no_volume > 0 else None,
            "volume_yes": yes_volume,
            "volume_no": no_volume
        }


# Convenience function for testing
async def main():
    """Test the Polygon monitor."""
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    rpc_url = os.environ.get("POLYGON_RPC_URL")
    if not rpc_url:
        print("Set POLYGON_RPC_URL environment variable")
        print("Example: export POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY")
        sys.exit(1)

    monitor = PolygonMonitor(rpc_url=rpc_url)

    # Register callbacks
    def on_arb(signal: ArbitrageSignal):
        print(f"ARB: {signal.vwap_yes:.4f} + {signal.vwap_no:.4f} = "
              f"{signal.vwap_yes + signal.vwap_no:.4f} "
              f"(profit: {signal.profit_estimate:.4f})")

    def on_trade(trade: Trade):
        print(f"TRADE: {trade.token_id[:20]}... {trade.side.value} "
              f"{trade.size:.2f} @ {trade.price:.4f}")

    monitor.on_arbitrage(on_arb)
    monitor.on_trade(on_trade)

    try:
        await monitor.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await monitor.stop()
        print(f"\nStats: {monitor.get_stats()}")


if __name__ == "__main__":
    asyncio.run(main())
