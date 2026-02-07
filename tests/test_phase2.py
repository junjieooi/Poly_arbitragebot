"""
Unit tests for Phase 2 components.

Tests:
- Dependency Finder (without API calls)
- IP Solver (mathematical correctness)
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.detection.dependency_finder import (
    MarketInfo,
    Dependency,
    ConstraintType as DepConstraintType,
    DependencyDatabase,
    RateLimiter
)
from src.optimization.ip_solver import (
    Market,
    DependencyConstraint,
    ConstraintType as IPConstraintType,
    ArbitrageResult,
    ScipyIPSolver,
    get_solver,
    HAS_GUROBI
)


# ============================================================================
# Dependency Finder Tests
# ============================================================================

class TestMarketInfo:
    """Tests for MarketInfo dataclass."""

    def test_creation(self):
        market = MarketInfo(
            market_id="m1",
            question="Will X happen?",
            outcomes=["Yes", "No"],
            description="Test description"
        )
        assert market.market_id == "m1"
        assert market.question == "Will X happen?"
        assert market.outcomes == ["Yes", "No"]

    def test_to_prompt_text(self):
        market = MarketInfo(
            market_id="m1",
            question="Will Bitcoin hit $100k?",
            outcomes=["Yes", "No"],
            description="This market resolves Yes if BTC > $100k",
            end_date="2025-12-31"
        )
        text = market.to_prompt_text()
        assert "Will Bitcoin hit $100k?" in text
        assert "Yes, No" in text
        assert "2025-12-31" in text


class TestDependency:
    """Tests for Dependency dataclass."""

    def test_creation(self):
        dep = Dependency(
            market_a_id="m1",
            market_b_id="m2",
            constraint_type=DepConstraintType.IMPLICATION,
            condition_a="Yes",
            condition_b="Yes",
            explanation="If A then B",
            confidence=0.9
        )
        assert dep.constraint_type == DepConstraintType.IMPLICATION
        assert dep.confidence == 0.9

    def test_to_constraint(self):
        dep = Dependency(
            market_a_id="m1",
            market_b_id="m2",
            constraint_type=DepConstraintType.MUTUAL_EXCLUSION,
            condition_a="Yes",
            condition_b="Yes",
            valid_combinations=[["Yes", "No"], ["No", "Yes"], ["No", "No"]]
        )
        constraint = dep.to_constraint()
        assert constraint["type"] == "mutual_exclusion"
        assert constraint["cond_a"] == ("m1", "Yes")
        assert constraint["cond_b"] == ("m2", "Yes")


class TestDependencyDatabase:
    """Tests for DependencyDatabase."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        db_path = tmp_path / "test_deps.db"
        return DependencyDatabase(str(db_path))

    def test_save_and_retrieve(self, db):
        dep = Dependency(
            market_a_id="m1",
            market_b_id="m2",
            constraint_type=DepConstraintType.IMPLICATION,
            condition_a="Yes",
            condition_b="Yes",
            valid_combinations=[["Yes", "Yes"], ["No", "Yes"], ["No", "No"]],
            explanation="Test",
            confidence=0.8
        )

        dep_id = db.save_dependency(dep)
        assert dep_id > 0

        deps = db.get_dependencies()
        assert len(deps) == 1
        assert deps[0].market_a_id == "m1"
        assert deps[0].constraint_type == DepConstraintType.IMPLICATION

    def test_mark_validated(self, db):
        dep = Dependency(
            market_a_id="m1",
            market_b_id="m2",
            constraint_type=DepConstraintType.MUTUAL_EXCLUSION,
            condition_a="Yes",
            condition_b="Yes",
            valid_combinations=[],
            validated=False
        )
        dep_id = db.save_dependency(dep)

        db.mark_validated(dep_id, True)

        deps = db.get_dependencies(validated_only=True)
        assert len(deps) == 1
        assert deps[0].validated is True

    def test_was_analyzed(self, db):
        assert not db.was_analyzed("m1", "m2")

        db.log_analysis("m1", "m2", result="no_dependency")

        assert db.was_analyzed("m1", "m2")
        assert db.was_analyzed("m2", "m1")  # Order independent


class TestRateLimiter:
    """Tests for RateLimiter."""

    @pytest.mark.asyncio
    async def test_acquire(self):
        limiter = RateLimiter(requests_per_minute=60)  # 1 per second

        # First request should be immediate
        import time
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        limiter = RateLimiter(requests_per_minute=120)  # 2 per second

        # Rapid requests should work
        for _ in range(3):
            await limiter.acquire()


# ============================================================================
# IP Solver Tests
# ============================================================================

class TestMarket:
    """Tests for Market dataclass."""

    def test_creation(self):
        market = Market(
            market_id="m1",
            conditions=["Yes", "No"],
            prices={"Yes": 0.6, "No": 0.42}
        )
        assert market.market_id == "m1"
        assert len(market.conditions) == 2
        assert market.prices["Yes"] == 0.6

    def test_default_prices(self):
        market = Market(
            market_id="m1",
            conditions=["A", "B", "C"]
        )
        assert market.prices == {"A": 0.0, "B": 0.0, "C": 0.0}


class TestDependencyConstraint:
    """Tests for DependencyConstraint."""

    def test_creation(self):
        constraint = DependencyConstraint(
            constraint_type=IPConstraintType.IMPLICATION,
            market_a="m1",
            condition_a="Yes",
            market_b="m2",
            condition_b="Yes"
        )
        assert constraint.cond_a == ("m1", "Yes")
        assert constraint.cond_b == ("m2", "Yes")


class TestArbitrageResult:
    """Tests for ArbitrageResult."""

    def test_str_with_arbitrage(self):
        result = ArbitrageResult(
            has_arbitrage=True,
            min_cost=0.93,
            profit=0.07,
            positions={("m1", "Yes"): 1.0},
            solver_status="optimal"
        )
        s = str(result)
        assert "ARBITRAGE FOUND" in s
        assert "0.07" in s

    def test_str_without_arbitrage(self):
        result = ArbitrageResult(
            has_arbitrage=False,
            min_cost=1.02,
            profit=0,
            positions={},
            solver_status="optimal"
        )
        s = str(result)
        assert "No arbitrage" in s


class TestScipyIPSolver:
    """Tests for ScipyIPSolver."""

    @pytest.fixture
    def solver(self):
        return ScipyIPSolver()

    def test_single_market_no_arb(self, solver):
        """Single market with fair prices (sum = 1)."""
        markets = {
            "m1": Market(
                market_id="m1",
                conditions=["Yes", "No"],
                prices={"Yes": 0.5, "No": 0.5}
            )
        }

        result = solver.solve(markets, [])

        assert not result.has_arbitrage
        assert result.min_cost == pytest.approx(0.5, rel=0.01)

    def test_single_market_with_arb(self, solver):
        """Single market with prices summing to < 1 (buy arb)."""
        markets = {
            "m1": Market(
                market_id="m1",
                conditions=["Yes", "No"],
                prices={"Yes": 0.45, "No": 0.45}  # Sum = 0.90
            )
        }

        result = solver.solve(markets, [])

        assert result.has_arbitrage
        assert result.min_cost == pytest.approx(0.45, rel=0.01)
        assert result.profit == pytest.approx(0.55, rel=0.01)

    def test_implication_constraint(self, solver):
        """Test implication constraint: A → B."""
        markets = {
            "trump_pa": Market(
                market_id="trump_pa",
                conditions=["Yes", "No"],
                prices={"Yes": 0.55, "No": 0.48}
            ),
            "rep_pa": Market(
                market_id="rep_pa",
                conditions=["Yes", "No"],
                prices={"Yes": 0.60, "No": 0.42}
            )
        }

        # Trump wins PA → Republican wins PA
        constraints = [
            DependencyConstraint(
                constraint_type=IPConstraintType.IMPLICATION,
                market_a="trump_pa", condition_a="Yes",
                market_b="rep_pa", condition_b="Yes"
            )
        ]

        result = solver.solve(markets, constraints)

        # With implication, if Trump=Yes is selected, Rep=Yes must be selected
        # Minimum cost should be from selecting the cheapest consistent outcome
        assert result.solver_status == "optimal"

        # Verify implication is respected in solution
        if result.positions.get(("trump_pa", "Yes"), 0) > 0.5:
            assert result.positions.get(("rep_pa", "Yes"), 0) > 0.5

    def test_mutual_exclusion_constraint(self, solver):
        """Test mutual exclusion: ¬(A ∧ B)."""
        markets = {
            "duke": Market(
                market_id="duke",
                conditions=["Yes", "No"],
                prices={"Yes": 0.15, "No": 0.87}
            ),
            "unc": Market(
                market_id="unc",
                conditions=["Yes", "No"],
                prices={"Yes": 0.12, "No": 0.90}
            )
        }

        # Duke wins and UNC wins are mutually exclusive
        constraints = [
            DependencyConstraint(
                constraint_type=IPConstraintType.MUTUAL_EXCLUSION,
                market_a="duke", condition_a="Yes",
                market_b="unc", condition_b="Yes"
            )
        ]

        result = solver.solve(markets, constraints)

        assert result.solver_status == "optimal"

        # Verify mutex is respected
        duke_yes = result.positions.get(("duke", "Yes"), 0) > 0.5
        unc_yes = result.positions.get(("unc", "Yes"), 0) > 0.5
        assert not (duke_yes and unc_yes)

    def test_multi_outcome_market(self, solver):
        """Test market with more than 2 outcomes."""
        markets = {
            "winner": Market(
                market_id="winner",
                conditions=["Duke", "UNC", "Kentucky", "Other"],
                prices={
                    "Duke": 0.15,
                    "UNC": 0.12,
                    "Kentucky": 0.08,
                    "Other": 0.60
                }
            )
        }

        result = solver.solve(markets, [])

        assert result.solver_status == "optimal"
        # Minimum is Kentucky at 0.08
        assert result.min_cost == pytest.approx(0.08, rel=0.01)

        # Exactly one outcome selected
        selected = sum(1 for v in result.positions.values() if v > 0.5)
        assert selected == 1

    def test_infeasible_constraints(self, solver):
        """Test with contradictory constraints."""
        markets = {
            "m1": Market(
                market_id="m1",
                conditions=["Yes", "No"],
                prices={"Yes": 0.5, "No": 0.5}
            ),
            "m2": Market(
                market_id="m2",
                conditions=["Yes", "No"],
                prices={"Yes": 0.5, "No": 0.5}
            )
        }

        # Contradictory: m1=Yes implies m2=Yes, but also m1=Yes and m2=Yes excluded
        constraints = [
            DependencyConstraint(
                constraint_type=IPConstraintType.IMPLICATION,
                market_a="m1", condition_a="Yes",
                market_b="m2", condition_b="Yes"
            ),
            DependencyConstraint(
                constraint_type=IPConstraintType.MUTUAL_EXCLUSION,
                market_a="m1", condition_a="Yes",
                market_b="m2", condition_b="Yes"
            )
        ]

        result = solver.solve(markets, constraints)

        # Should still find a solution (m1=No is valid)
        # The constraints only exclude m1=Yes,m2=Yes and m1=Yes,m2=No
        # So m1=No is forced, and m2 can be either
        assert result.solver_status == "optimal"

    def test_arbitrage_with_dependencies(self, solver):
        """
        Test arbitrage detection with dependencies.

        Scenario:
        - Market A: "Trump wins PA" - Yes: 0.55, No: 0.48 (sum=1.03)
        - Market B: "Rep wins PA" - Yes: 0.45, No: 0.58 (sum=1.03)
        - Dependency: Trump=Yes → Rep=Yes

        Without dependency: min cost = 0.45 (buy Rep=Yes)
        With dependency: still 0.45 (buy Rep=Yes, Trump can be No)
        """
        markets = {
            "trump": Market(
                market_id="trump",
                conditions=["Yes", "No"],
                prices={"Yes": 0.55, "No": 0.48}
            ),
            "rep": Market(
                market_id="rep",
                conditions=["Yes", "No"],
                prices={"Yes": 0.45, "No": 0.58}
            )
        }

        constraints = [
            DependencyConstraint(
                constraint_type=IPConstraintType.IMPLICATION,
                market_a="trump", condition_a="Yes",
                market_b="rep", condition_b="Yes"
            )
        ]

        result = solver.solve(markets, constraints, arbitrage_threshold=0.98)

        # Minimum consistent assignment: Trump=No (0.48), Rep=Yes (0.45) = 0.93
        assert result.has_arbitrage
        assert result.min_cost == pytest.approx(0.93, rel=0.01)


class TestGetSolver:
    """Test solver factory function."""

    def test_get_scipy_solver(self):
        solver = get_solver(prefer_gurobi=False)
        assert isinstance(solver, ScipyIPSolver)

    @pytest.mark.skipif(not HAS_GUROBI, reason="Gurobi not installed")
    def test_get_gurobi_solver(self):
        from src.optimization.ip_solver import GurobiIPSolver
        solver = get_solver(prefer_gurobi=True)
        assert isinstance(solver, GurobiIPSolver)
        solver.close()


# ============================================================================
# Integration Tests
# ============================================================================

class TestPhase2Integration:
    """Integration tests for Phase 2 workflow."""

    def test_dependency_to_constraint_conversion(self):
        """Test converting dependency finder output to IP solver input."""
        from src.run_phase2 import convert_dependency_to_constraint

        dep = Dependency(
            market_a_id="m1",
            market_b_id="m2",
            constraint_type=DepConstraintType.IMPLICATION,
            condition_a="Yes",
            condition_b="Yes"
        )

        constraint = convert_dependency_to_constraint(dep)

        assert constraint.constraint_type == IPConstraintType.IMPLICATION
        assert constraint.market_a == "m1"
        assert constraint.condition_a == "Yes"

    def test_full_workflow_mock(self, tmp_path):
        """Test full Phase 2 workflow with mocked Claude API."""
        # Create mock markets
        api_markets = [
            {
                'market_id': 'm1',
                'question': 'Will Trump win PA?',
                'outcomes': ['Yes', 'No'],
                'outcome_prices': [0.55, 0.48],
                'description': 'Test'
            },
            {
                'market_id': 'm2',
                'question': 'Will Republican win PA?',
                'outcomes': ['Yes', 'No'],
                'outcome_prices': [0.60, 0.42],
                'description': 'Test'
            }
        ]

        # Convert to IP format
        from src.run_phase2 import markets_to_ip_format
        ip_markets = markets_to_ip_format(api_markets)

        assert len(ip_markets) == 2
        assert ip_markets['m1'].prices['Yes'] == 0.55

        # Create a dependency manually (simulating Claude output)
        constraints = [
            DependencyConstraint(
                constraint_type=IPConstraintType.IMPLICATION,
                market_a="m1", condition_a="Yes",
                market_b="m2", condition_b="Yes"
            )
        ]

        # Solve
        solver = ScipyIPSolver()
        result = solver.solve(ip_markets, constraints)

        assert result.solver_status == "optimal"
        # Should select m1=No (0.48) and m2=Yes (0.60) = 1.08
        # Or m1=No (0.48) and m2=No (0.42) = 0.90 ← minimum
        assert result.min_cost == pytest.approx(0.90, rel=0.05)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
