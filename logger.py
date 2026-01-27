"""
Logging Configuration
Centralized logging setup for all modules
"""

import logging
import sys
from typing import Optional
from pathlib import Path


def setup_logging(
    level: str = 'INFO',
    log_file: Optional[str] = 'trading.log',
    log_format: str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    console_output: bool = True
) -> logging.Logger:
    """
    Configure logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file, None to disable file logging
        log_format: Log message format
        console_output: Whether to also log to console

    Returns:
        Root logger instance
    """
    # Get numeric level
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(log_format)

    # File handler
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific module.

    Args:
        name: Module name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class TradingLogger:
    """
    Specialized logger for trading operations with structured logging.
    """

    def __init__(self, name: str):
        self.logger = get_logger(name)

    def trade_signal(self, market_id: str, action: str, reason: str, **kwargs):
        """Log a trade signal."""
        extra = ' '.join(f"{k}={v}" for k, v in kwargs.items())
        self.logger.info(f"SIGNAL | market={market_id} action={action} reason={reason} {extra}")

    def trade_executed(self, market_id: str, action: str, shares: int, price: float, **kwargs):
        """Log an executed trade."""
        extra = ' '.join(f"{k}={v}" for k, v in kwargs.items())
        self.logger.info(f"TRADE | market={market_id} action={action} shares={shares} price={price:.4f} {extra}")

    def position_update(self, market_id: str, shares: int, pnl: float, pnl_pct: float):
        """Log position updates."""
        self.logger.info(f"POSITION | market={market_id} shares={shares} pnl={pnl:.2f} pnl_pct={pnl_pct:.2f}%")

    def risk_alert(self, alert_type: str, message: str, **kwargs):
        """Log risk management alerts."""
        extra = ' '.join(f"{k}={v}" for k, v in kwargs.items())
        self.logger.warning(f"RISK | type={alert_type} message={message} {extra}")

    def stop_loss_triggered(self, market_id: str, pnl_pct: float):
        """Log stop loss trigger."""
        self.logger.warning(f"STOP_LOSS | market={market_id} pnl_pct={pnl_pct:.2f}%")

    def api_error(self, operation: str, error: str, **kwargs):
        """Log API errors."""
        extra = ' '.join(f"{k}={v}" for k, v in kwargs.items())
        self.logger.error(f"API_ERROR | operation={operation} error={error} {extra}")

    def arbitrage_opportunity(self, opp_type: str, profit: float, profit_pct: float, markets: list):
        """Log arbitrage opportunity detection."""
        self.logger.info(
            f"ARBITRAGE | type={opp_type} profit={profit:.4f} profit_pct={profit_pct:.2f}% markets={','.join(markets)}"
        )

    def daily_summary(self, total_pnl: float, total_trades: int, win_rate: float):
        """Log daily trading summary."""
        self.logger.info(
            f"DAILY_SUMMARY | total_pnl={total_pnl:.2f} total_trades={total_trades} win_rate={win_rate:.2%}"
        )


# Initialize logging with config if available
def init_from_config():
    """Initialize logging from config module."""
    try:
        from config import get_config
        config = get_config()
        setup_logging(
            level=config.logging.level,
            log_file=config.logging.file_path,
            log_format=config.logging.format,
            console_output=config.logging.console_output
        )
    except ImportError:
        # Config not available, use defaults
        setup_logging()


if __name__ == "__main__":
    # Test logging
    setup_logging(level='DEBUG')

    logger = get_logger(__name__)
    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")

    trading_logger = TradingLogger("test")
    trading_logger.trade_signal("market_1", "BUY", "Edge detected", edge=0.05, kelly=0.12)
    trading_logger.trade_executed("market_1", "BUY", 100, 0.45)
    trading_logger.risk_alert("position_size", "Position exceeds limit", current=0.15, max=0.10)
    trading_logger.arbitrage_opportunity("Multi-Outcome", 0.02, 2.1, ["m1", "m2", "m3"])
