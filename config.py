"""
Centralized Configuration Management
Load settings from environment variables with sensible defaults
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


def _get_env_float(key: str, default: float) -> float:
    """Get float from environment variable with default."""
    try:
        return float(os.getenv(key, default))
    except (ValueError, TypeError):
        return default


def _get_env_int(key: str, default: int) -> int:
    """Get int from environment variable with default."""
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        return default


def _get_env_bool(key: str, default: bool) -> bool:
    """Get bool from environment variable with default."""
    val = os.getenv(key, str(default)).lower()
    return val in ('true', '1', 'yes', 'on')


@dataclass
class TradingConfig:
    """Trading-specific configuration"""
    fee_rate: float = field(default_factory=lambda: _get_env_float('FEE_RATE', 0.02))
    min_edge: float = field(default_factory=lambda: _get_env_float('MIN_EDGE', 0.08))  # Conservative: 8% min edge
    min_liquidity_ratio: float = field(default_factory=lambda: _get_env_float('MIN_LIQUIDITY_RATIO', 0.05))  # Conservative: 5%
    max_slippage: float = field(default_factory=lambda: _get_env_float('MAX_SLIPPAGE', 0.01))  # Conservative: 1% max slippage
    pure_arbitrage_only: bool = field(default_factory=lambda: _get_env_bool('PURE_ARBITRAGE_ONLY', True))  # Disable stat arb


@dataclass
class RiskConfig:
    """Risk management configuration - LOW RISK DEFAULTS"""
    max_position_size: float = field(default_factory=lambda: _get_env_float('MAX_POSITION_SIZE', 0.01))  # Conservative: 1%
    max_market_exposure: float = field(default_factory=lambda: _get_env_float('MAX_MARKET_EXPOSURE', 0.10))  # Conservative: 10%
    max_portfolio_risk: float = field(default_factory=lambda: _get_env_float('MAX_PORTFOLIO_RISK', 0.30))  # Conservative: 30%
    kelly_fraction: float = field(default_factory=lambda: _get_env_float('KELLY_FRACTION', 0.25))
    stop_loss_pct: float = field(default_factory=lambda: _get_env_float('STOP_LOSS_PCT', -0.15))  # Conservative: -15%
    daily_loss_limit: float = field(default_factory=lambda: _get_env_float('DAILY_LOSS_LIMIT', -0.02))  # Conservative: -2%
    max_drawdown_limit: float = field(default_factory=lambda: _get_env_float('MAX_DRAWDOWN_LIMIT', -0.10))  # Conservative: -10%
    # Fixed position sizing (safer than Kelly)
    use_fixed_sizing: bool = field(default_factory=lambda: _get_env_bool('USE_FIXED_SIZING', True))  # Use fixed % instead of Kelly
    fixed_position_pct: float = field(default_factory=lambda: _get_env_float('FIXED_POSITION_PCT', 0.01))  # 1% per trade


@dataclass
class SimulationConfig:
    """Monte Carlo simulation configuration"""
    initial_bankroll: float = field(default_factory=lambda: _get_env_float('INITIAL_BANKROLL', 10000.0))
    num_simulations: int = field(default_factory=lambda: _get_env_int('NUM_SIMULATIONS', 1000))
    num_trades: int = field(default_factory=lambda: _get_env_int('NUM_TRADES', 100))
    random_seed: Optional[int] = field(default_factory=lambda: _get_env_int('RANDOM_SEED', 42) if os.getenv('RANDOM_SEED') else None)
    model_error: float = field(default_factory=lambda: _get_env_float('MODEL_ERROR', 0.10))


@dataclass
class APIConfig:
    """Polymarket API configuration"""
    api_key: Optional[str] = field(default_factory=lambda: os.getenv('POLYMARKET_API_KEY'))
    api_secret: Optional[str] = field(default_factory=lambda: os.getenv('POLYMARKET_API_SECRET'))
    private_key: Optional[str] = field(default_factory=lambda: os.getenv('POLYMARKET_PRIVATE_KEY'))
    base_url: str = field(default_factory=lambda: os.getenv('POLYMARKET_BASE_URL', 'https://clob.polymarket.com'))
    ws_url: str = field(default_factory=lambda: os.getenv('POLYMARKET_WS_URL', 'wss://ws-subscriptions-clob.polymarket.com/ws/market'))
    chain_id: int = field(default_factory=lambda: _get_env_int('POLYMARKET_CHAIN_ID', 137))  # Polygon mainnet
    rate_limit_requests: int = field(default_factory=lambda: _get_env_int('RATE_LIMIT_REQUESTS', 10))
    rate_limit_period: float = field(default_factory=lambda: _get_env_float('RATE_LIMIT_PERIOD', 1.0))
    request_timeout: float = field(default_factory=lambda: _get_env_float('REQUEST_TIMEOUT', 30.0))
    max_retries: int = field(default_factory=lambda: _get_env_int('MAX_RETRIES', 3))


@dataclass
class LoggingConfig:
    """Logging configuration"""
    level: str = field(default_factory=lambda: os.getenv('LOG_LEVEL', 'INFO'))
    file_path: Optional[str] = field(default_factory=lambda: os.getenv('LOG_FILE', 'trading.log'))
    format: str = field(default_factory=lambda: os.getenv(
        'LOG_FORMAT',
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    console_output: bool = field(default_factory=lambda: _get_env_bool('LOG_CONSOLE', True))


@dataclass
class TelegramConfig:
    """Telegram alert configuration"""
    bot_token: Optional[str] = field(default_factory=lambda: os.getenv('TELEGRAM_BOT_TOKEN'))
    chat_id: Optional[str] = field(default_factory=lambda: os.getenv('TELEGRAM_CHAT_ID'))
    enabled: bool = field(default_factory=lambda: _get_env_bool('TELEGRAM_ENABLED', True))


@dataclass
class Config:
    """Main configuration container"""
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    api: APIConfig = field(default_factory=APIConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)

    # Environment mode
    is_paper_trading: bool = field(default_factory=lambda: _get_env_bool('PAPER_TRADING', True))
    debug_mode: bool = field(default_factory=lambda: _get_env_bool('DEBUG_MODE', False))

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        # Trading validation
        if not 0 <= self.trading.fee_rate <= 1:
            errors.append(f"fee_rate must be 0-1, got {self.trading.fee_rate}")
        if not 0 <= self.trading.min_edge <= 1:
            errors.append(f"min_edge must be 0-1, got {self.trading.min_edge}")

        # Risk validation
        if not 0 < self.risk.max_position_size <= 1:
            errors.append(f"max_position_size must be 0-1, got {self.risk.max_position_size}")
        if not 0 < self.risk.kelly_fraction <= 1:
            errors.append(f"kelly_fraction must be 0-1, got {self.risk.kelly_fraction}")
        if self.risk.stop_loss_pct >= 0:
            errors.append(f"stop_loss_pct should be negative, got {self.risk.stop_loss_pct}")
        if self.risk.daily_loss_limit >= 0:
            errors.append(f"daily_loss_limit should be negative, got {self.risk.daily_loss_limit}")

        # Simulation validation
        if self.simulation.initial_bankroll <= 0:
            errors.append(f"initial_bankroll must be positive, got {self.simulation.initial_bankroll}")
        if self.simulation.num_simulations <= 0:
            errors.append(f"num_simulations must be positive, got {self.simulation.num_simulations}")

        # API validation (only for live trading)
        if not self.is_paper_trading:
            if not self.api.api_key:
                errors.append("POLYMARKET_API_KEY required for live trading")
            if not self.api.private_key:
                errors.append("POLYMARKET_PRIVATE_KEY required for live trading")

        return errors

    @classmethod
    def from_env_file(cls, env_path: str = '.env') -> 'Config':
        """Load configuration from .env file if it exists."""
        env_file = Path(env_path)
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ.setdefault(key.strip(), value.strip())
        return cls()


# Global config instance (lazy loaded)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get or create the global config instance."""
    global _config
    if _config is None:
        _config = Config.from_env_file()
    return _config


def reload_config() -> Config:
    """Force reload of configuration."""
    global _config
    _config = Config.from_env_file()
    return _config


# Example .env file template
ENV_TEMPLATE = """
# Polymarket Arbitrage Bot Configuration
# Copy this to .env and customize values

# === Trading Settings (LOW RISK) ===
FEE_RATE=0.02
MIN_EDGE=0.08
MIN_LIQUIDITY_RATIO=0.05
MAX_SLIPPAGE=0.01
PURE_ARBITRAGE_ONLY=true

# === Risk Management (LOW RISK) ===
MAX_POSITION_SIZE=0.01
MAX_MARKET_EXPOSURE=0.10
MAX_PORTFOLIO_RISK=0.30
KELLY_FRACTION=0.25
STOP_LOSS_PCT=-0.15
DAILY_LOSS_LIMIT=-0.02
MAX_DRAWDOWN_LIMIT=-0.10
USE_FIXED_SIZING=true
FIXED_POSITION_PCT=0.01

# === Simulation ===
INITIAL_BANKROLL=10000
NUM_SIMULATIONS=1000
NUM_TRADES=100
RANDOM_SEED=42
MODEL_ERROR=0.10

# === Polymarket API ===
# POLYMARKET_API_KEY=your_api_key_here
# POLYMARKET_API_SECRET=your_api_secret_here
# POLYMARKET_PRIVATE_KEY=your_private_key_here
POLYMARKET_BASE_URL=https://clob.polymarket.com
POLYMARKET_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
POLYMARKET_CHAIN_ID=137
RATE_LIMIT_REQUESTS=10
RATE_LIMIT_PERIOD=1.0
REQUEST_TIMEOUT=30.0
MAX_RETRIES=3

# === Logging ===
LOG_LEVEL=INFO
LOG_FILE=trading.log
LOG_CONSOLE=true

# === Environment ===
PAPER_TRADING=true
DEBUG_MODE=false
""".strip()


if __name__ == "__main__":
    # Print configuration template
    print("=== Configuration Template ===")
    print(ENV_TEMPLATE)

    print("\n\n=== Current Configuration ===")
    config = get_config()
    errors = config.validate()

    print(f"Paper Trading: {config.is_paper_trading}")
    print(f"Debug Mode: {config.debug_mode}")
    print(f"\n=== LOW RISK SETTINGS ===")
    print(f"Pure Arbitrage Only: {config.trading.pure_arbitrage_only}")
    print(f"Min Edge Required: {config.trading.min_edge:.2%}")
    print(f"Max Slippage: {config.trading.max_slippage:.2%}")
    print(f"Use Fixed Sizing: {config.risk.use_fixed_sizing}")
    print(f"Fixed Position %: {config.risk.fixed_position_pct:.2%}")
    print(f"Max Position Size: {config.risk.max_position_size:.2%}")
    print(f"Daily Loss Limit: {config.risk.daily_loss_limit:.2%}")
    print(f"Max Drawdown Limit: {config.risk.max_drawdown_limit:.2%}")
    print(f"Stop Loss: {config.risk.stop_loss_pct:.2%}")

    if errors:
        print("\nConfiguration Errors:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("\nConfiguration is valid.")
