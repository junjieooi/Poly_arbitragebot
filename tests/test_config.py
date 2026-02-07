"""
Unit tests for config module
"""

import pytest
import os
import tempfile
import sys
sys.path.insert(0, '..')

from config import (
    Config,
    TradingConfig,
    RiskConfig,
    SimulationConfig,
    APIConfig,
    LoggingConfig,
    TelegramConfig,
    get_config,
    reload_config,
    _get_env_float,
    _get_env_int,
    _get_env_bool,
)


class TestEnvHelpers:
    """Test environment variable helper functions"""

    def test_get_env_float_valid(self):
        """Valid float string should parse correctly"""
        os.environ['TEST_FLOAT'] = '3.14'
        result = _get_env_float('TEST_FLOAT', 0.0)
        assert result == pytest.approx(3.14)
        del os.environ['TEST_FLOAT']

    def test_get_env_float_default(self):
        """Missing env var should return default"""
        result = _get_env_float('NONEXISTENT_VAR', 1.5)
        assert result == pytest.approx(1.5)

    def test_get_env_float_invalid(self):
        """Invalid float string should return default"""
        os.environ['TEST_FLOAT'] = 'not_a_float'
        result = _get_env_float('TEST_FLOAT', 2.5)
        assert result == pytest.approx(2.5)
        del os.environ['TEST_FLOAT']

    def test_get_env_int_valid(self):
        """Valid int string should parse correctly"""
        os.environ['TEST_INT'] = '42'
        result = _get_env_int('TEST_INT', 0)
        assert result == 42
        del os.environ['TEST_INT']

    def test_get_env_int_default(self):
        """Missing env var should return default"""
        result = _get_env_int('NONEXISTENT_VAR', 10)
        assert result == 10

    def test_get_env_int_invalid(self):
        """Invalid int string should return default"""
        os.environ['TEST_INT'] = '3.14'
        result = _get_env_int('TEST_INT', 5)
        assert result == 5
        del os.environ['TEST_INT']

    def test_get_env_bool_true_values(self):
        """True-like values should return True"""
        for val in ['true', 'True', 'TRUE', '1', 'yes', 'YES', 'on', 'ON']:
            os.environ['TEST_BOOL'] = val
            result = _get_env_bool('TEST_BOOL', False)
            assert result is True
            del os.environ['TEST_BOOL']

    def test_get_env_bool_false_values(self):
        """False-like values should return False"""
        for val in ['false', 'False', '0', 'no', 'off', '']:
            os.environ['TEST_BOOL'] = val
            result = _get_env_bool('TEST_BOOL', True)
            assert result is False
            del os.environ['TEST_BOOL']


class TestTradingConfig:
    """Test TradingConfig dataclass"""

    def test_default_values(self):
        """Default values should be sensible"""
        config = TradingConfig()

        assert 0 <= config.fee_rate <= 0.1
        assert 0 <= config.min_edge <= 1
        assert 0 < config.min_liquidity_ratio <= 1
        assert 0 <= config.max_slippage <= 1

    def test_pure_arbitrage_default_enabled(self):
        """Pure arbitrage mode should be enabled by default (low risk)"""
        config = TradingConfig()
        assert config.pure_arbitrage_only is True


class TestRiskConfig:
    """Test RiskConfig dataclass"""

    def test_default_values(self):
        """Default risk values should be conservative"""
        config = RiskConfig()

        # Conservative defaults
        assert config.max_position_size <= 0.05  # Max 5%
        assert config.max_market_exposure <= 0.25
        assert config.stop_loss_pct < 0
        assert config.daily_loss_limit < 0
        assert config.max_drawdown_limit < 0

    def test_fixed_sizing_default_enabled(self):
        """Fixed sizing should be enabled by default (safer)"""
        config = RiskConfig()
        assert config.use_fixed_sizing is True


class TestConfig:
    """Test main Config class"""

    def test_default_paper_trading(self):
        """Should default to paper trading mode"""
        config = Config()
        assert config.is_paper_trading is True

    def test_validate_valid_config(self):
        """Valid config should have no errors"""
        config = Config()
        errors = config.validate()
        # May have API key errors if not set, that's expected
        non_api_errors = [e for e in errors if 'API' not in e and 'KEY' not in e.upper()]
        assert len(non_api_errors) == 0

    def test_validate_invalid_fee_rate(self):
        """Invalid fee rate should produce error"""
        config = Config()
        config.trading.fee_rate = 1.5  # Invalid: > 1

        errors = config.validate()
        assert any('fee_rate' in e for e in errors)

    def test_validate_invalid_min_edge(self):
        """Invalid min_edge should produce error"""
        config = Config()
        config.trading.min_edge = -0.1  # Invalid: negative

        errors = config.validate()
        assert any('min_edge' in e for e in errors)

    def test_validate_invalid_position_size(self):
        """Invalid max_position_size should produce error"""
        config = Config()
        config.risk.max_position_size = 0  # Invalid: <= 0

        errors = config.validate()
        assert any('max_position_size' in e for e in errors)

    def test_validate_invalid_kelly_fraction(self):
        """Invalid kelly_fraction should produce error"""
        config = Config()
        config.risk.kelly_fraction = 1.5  # Invalid: > 1

        errors = config.validate()
        assert any('kelly_fraction' in e for e in errors)

    def test_validate_positive_stop_loss(self):
        """Positive stop_loss_pct should produce error"""
        config = Config()
        config.risk.stop_loss_pct = 0.1  # Invalid: should be negative

        errors = config.validate()
        assert any('stop_loss_pct' in e for e in errors)

    def test_validate_positive_daily_loss_limit(self):
        """Positive daily_loss_limit should produce error"""
        config = Config()
        config.risk.daily_loss_limit = 0.05  # Invalid: should be negative

        errors = config.validate()
        assert any('daily_loss_limit' in e for e in errors)

    def test_validate_invalid_bankroll(self):
        """Non-positive bankroll should produce error"""
        config = Config()
        config.simulation.initial_bankroll = 0  # Invalid

        errors = config.validate()
        assert any('initial_bankroll' in e for e in errors)

    def test_validate_invalid_num_simulations(self):
        """Non-positive num_simulations should produce error"""
        config = Config()
        config.simulation.num_simulations = -1  # Invalid

        errors = config.validate()
        assert any('num_simulations' in e for e in errors)

    def test_validate_live_trading_requires_api_key(self):
        """Live trading should require API key"""
        config = Config()
        config.is_paper_trading = False
        config.api.api_key = None

        errors = config.validate()
        assert any('API_KEY' in e.upper() for e in errors)

    def test_validate_live_trading_requires_private_key(self):
        """Live trading should require private key"""
        config = Config()
        config.is_paper_trading = False
        config.api.api_key = "test_key"
        config.api.private_key = None

        errors = config.validate()
        assert any('PRIVATE_KEY' in e.upper() for e in errors)


class TestConfigFromEnvFile:
    """Test loading config from .env file"""

    def test_from_env_file_creates_config(self):
        """Should create config even without .env file"""
        config = Config.from_env_file('nonexistent.env')
        assert config is not None
        assert isinstance(config, Config)

    def test_from_env_file_loads_values(self):
        """Should load values from .env file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("FEE_RATE=0.05\n")
            f.write("MAX_POSITION_SIZE=0.02\n")
            f.write("# This is a comment\n")
            f.write("PAPER_TRADING=true\n")
            f.flush()

            # Clear existing env vars that might interfere
            for key in ['FEE_RATE', 'MAX_POSITION_SIZE', 'PAPER_TRADING']:
                os.environ.pop(key, None)

            config = Config.from_env_file(f.name)

            assert config.trading.fee_rate == pytest.approx(0.05)
            assert config.risk.max_position_size == pytest.approx(0.02)
            assert config.is_paper_trading is True

        # Cleanup
        os.unlink(f.name)

    def test_from_env_file_ignores_comments(self):
        """Comments should be ignored"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("# FEE_RATE=0.99\n")
            f.write("FEE_RATE=0.01\n")
            f.flush()

            os.environ.pop('FEE_RATE', None)
            config = Config.from_env_file(f.name)

            assert config.trading.fee_rate == pytest.approx(0.01)

        os.unlink(f.name)


class TestGetConfig:
    """Test global config getter"""

    def test_get_config_returns_config(self):
        """get_config should return Config instance"""
        config = get_config()
        assert isinstance(config, Config)

    def test_get_config_singleton(self):
        """get_config should return same instance"""
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_reload_config_creates_new(self):
        """reload_config should create new instance"""
        config1 = get_config()
        config2 = reload_config()
        # New instance but may have same values
        assert isinstance(config2, Config)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
