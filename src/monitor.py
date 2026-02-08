"""
Trading Monitor - Real-time status dashboard for the arbitrage bot.

Features:
- Live status file updated every scan
- Command-line status checker
- Position tracking
- PnL monitoring
- Opportunity history

Usage:
    # Check status from another terminal
    python -m src.monitor status

    # Watch live updates
    python -m src.monitor watch

    # View recent opportunities
    python -m src.monitor opportunities
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Status file location
STATUS_FILE = Path("data/bot_status.json")
HISTORY_FILE = Path("data/opportunity_history.json")


@dataclass
class Position:
    """A trading position."""
    market_id: str
    market_name: str
    side: str  # "YES" or "NO"
    entry_price: float
    size: float
    current_price: float = 0.0
    entry_time: str = ""

    @property
    def pnl(self) -> float:
        """Unrealized PnL."""
        if self.side == "YES":
            return (self.current_price - self.entry_price) * self.size
        else:
            return (self.entry_price - self.current_price) * self.size

    @property
    def pnl_pct(self) -> float:
        """PnL as percentage."""
        if self.entry_price == 0:
            return 0
        return (self.pnl / (self.entry_price * self.size)) * 100


@dataclass
class BotStatus:
    """Current bot status."""
    # Bot state
    running: bool = False
    mode: str = "paper"  # "paper" or "live"
    started_at: str = ""
    last_update: str = ""

    # Performance
    initial_balance: float = 10000.0
    current_balance: float = 10000.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0

    # Activity
    scans_completed: int = 0
    opportunities_found: int = 0
    trades_executed: int = 0

    # Current state
    markets_monitored: int = 0
    positions: List[Dict] = field(default_factory=list)
    recent_opportunities: List[Dict] = field(default_factory=list)

    # Errors
    last_error: str = ""
    error_count: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "BotStatus":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class StatusManager:
    """Manages bot status persistence and updates."""

    def __init__(self, status_file: Path = STATUS_FILE):
        self.status_file = status_file
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        self._status = BotStatus()
        self._load()

    def _load(self) -> None:
        """Load status from file."""
        if self.status_file.exists():
            try:
                with open(self.status_file) as f:
                    data = json.load(f)
                    self._status = BotStatus.from_dict(data)
            except Exception as e:
                print(f"Warning: Could not load status: {e}")

    def _save(self) -> None:
        """Save status to file."""
        self._status.last_update = datetime.utcnow().isoformat()
        with open(self.status_file, 'w') as f:
            json.dump(self._status.to_dict(), f, indent=2)

    @property
    def status(self) -> BotStatus:
        return self._status

    def start(self, mode: str = "paper", initial_balance: float = 10000.0) -> None:
        """Mark bot as started."""
        self._status.running = True
        self._status.mode = mode
        self._status.started_at = datetime.utcnow().isoformat()
        self._status.initial_balance = initial_balance
        self._status.current_balance = initial_balance
        self._status.scans_completed = 0
        self._status.opportunities_found = 0
        self._status.trades_executed = 0
        self._status.positions = []
        self._status.recent_opportunities = []
        self._status.error_count = 0
        self._save()

    def stop(self) -> None:
        """Mark bot as stopped."""
        self._status.running = False
        self._save()

    def update_scan(self, markets_count: int) -> None:
        """Update after a scan."""
        self._status.scans_completed += 1
        self._status.markets_monitored = markets_count
        self._save()

    def add_opportunity(self, opportunity: Dict) -> None:
        """Record a found opportunity."""
        self._status.opportunities_found += 1
        opportunity['found_at'] = datetime.utcnow().isoformat()
        self._status.recent_opportunities.insert(0, opportunity)
        self._status.recent_opportunities = self._status.recent_opportunities[:20]  # Keep last 20
        self._save()

        # Also save to history file
        self._save_to_history(opportunity)

    def _save_to_history(self, opportunity: Dict) -> None:
        """Append opportunity to history file."""
        history_file = HISTORY_FILE
        history = []
        if history_file.exists():
            try:
                with open(history_file) as f:
                    history = json.load(f)
            except:
                pass
        history.append(opportunity)
        with open(history_file, 'w') as f:
            json.dump(history[-1000:], f, indent=2)  # Keep last 1000

    def add_trade(self, trade: Dict) -> None:
        """Record an executed trade."""
        self._status.trades_executed += 1
        self._save()

    def update_balance(self, new_balance: float) -> None:
        """Update current balance and PnL."""
        self._status.current_balance = new_balance
        self._status.total_pnl = new_balance - self._status.initial_balance
        if self._status.initial_balance > 0:
            self._status.total_pnl_pct = (self._status.total_pnl / self._status.initial_balance) * 100
        self._save()

    def update_positions(self, positions: List[Dict]) -> None:
        """Update current positions."""
        self._status.positions = positions
        self._save()

    def record_error(self, error: str) -> None:
        """Record an error."""
        self._status.last_error = error
        self._status.error_count += 1
        self._save()


def print_status(status: BotStatus) -> None:
    """Print formatted status to console."""
    print("\n" + "=" * 60)
    print("🤖 ARBITRAGE BOT STATUS")
    print("=" * 60)

    # Status indicator
    if status.running:
        print(f"Status: 🟢 RUNNING ({status.mode.upper()} MODE)")
    else:
        print(f"Status: 🔴 STOPPED")

    print(f"Started: {status.started_at[:19] if status.started_at else 'N/A'}")
    print(f"Last Update: {status.last_update[:19] if status.last_update else 'N/A'}")

    # Performance
    print("\n📊 PERFORMANCE")
    print("-" * 40)
    print(f"Initial Balance:  ${status.initial_balance:,.2f}")
    print(f"Current Balance:  ${status.current_balance:,.2f}")
    pnl_symbol = "+" if status.total_pnl >= 0 else ""
    print(f"Total PnL:        {pnl_symbol}${status.total_pnl:,.2f} ({pnl_symbol}{status.total_pnl_pct:.2f}%)")

    # Activity
    print("\n📈 ACTIVITY")
    print("-" * 40)
    print(f"Scans Completed:     {status.scans_completed}")
    print(f"Markets Monitored:   {status.markets_monitored}")
    print(f"Opportunities Found: {status.opportunities_found}")
    print(f"Trades Executed:     {status.trades_executed}")

    # Positions
    if status.positions:
        print("\n💼 OPEN POSITIONS")
        print("-" * 40)
        for pos in status.positions:
            pnl = pos.get('pnl', 0)
            symbol = "+" if pnl >= 0 else ""
            print(f"  {pos.get('market_name', 'Unknown')[:30]}...")
            print(f"    {pos.get('side', '?')} @ ${pos.get('entry_price', 0):.4f} | PnL: {symbol}${pnl:.2f}")
    else:
        print("\n💼 POSITIONS: None")

    # Recent opportunities
    if status.recent_opportunities:
        print("\n🎯 RECENT OPPORTUNITIES")
        print("-" * 40)
        for opp in status.recent_opportunities[:5]:
            print(f"  [{opp.get('found_at', '')[:16]}] {opp.get('type', '').upper()}")
            print(f"    {opp.get('market', {}).get('question', 'Unknown')[:40]}...")
            print(f"    Profit: {opp.get('profit_pct', 0):.2f}%")

    # Errors
    if status.error_count > 0:
        print(f"\n⚠️  ERRORS: {status.error_count}")
        print(f"   Last: {status.last_error[:50]}...")

    print("\n" + "=" * 60)


def watch_status(interval: int = 5) -> None:
    """Watch status updates live."""
    print("Watching bot status (Ctrl+C to stop)...")

    try:
        while True:
            os.system('clear' if os.name == 'posix' else 'cls')

            if STATUS_FILE.exists():
                with open(STATUS_FILE) as f:
                    data = json.load(f)
                    status = BotStatus.from_dict(data)
                    print_status(status)
            else:
                print("No status file found. Is the bot running?")

            print(f"\nRefreshing every {interval}s... (Ctrl+C to stop)")
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nStopped watching.")


def show_opportunities() -> None:
    """Show opportunity history."""
    if not HISTORY_FILE.exists():
        print("No opportunity history found.")
        return

    with open(HISTORY_FILE) as f:
        history = json.load(f)

    print(f"\n📜 OPPORTUNITY HISTORY ({len(history)} total)")
    print("=" * 60)

    for opp in history[-20:]:  # Last 20
        print(f"\n[{opp.get('found_at', '')[:16]}] {opp.get('type', '').upper()} ARBITRAGE")
        print(f"  Market: {opp.get('market', {}).get('question', 'Unknown')[:50]}...")
        print(f"  Price Sum: ${opp.get('price_sum', 0):.4f}")
        print(f"  Profit: {opp.get('profit_pct', 0):.2f}%")


def main():
    parser = argparse.ArgumentParser(description="Bot Status Monitor")
    parser.add_argument('command', choices=['status', 'watch', 'opportunities', 'clear'],
                        help='Command to run')
    parser.add_argument('--interval', type=int, default=5,
                        help='Watch refresh interval in seconds')

    args = parser.parse_args()

    if args.command == 'status':
        if STATUS_FILE.exists():
            with open(STATUS_FILE) as f:
                data = json.load(f)
                status = BotStatus.from_dict(data)
                print_status(status)
        else:
            print("No status file found. Is the bot running?")

    elif args.command == 'watch':
        watch_status(args.interval)

    elif args.command == 'opportunities':
        show_opportunities()

    elif args.command == 'clear':
        if STATUS_FILE.exists():
            STATUS_FILE.unlink()
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()
        print("Status cleared.")


if __name__ == "__main__":
    main()
