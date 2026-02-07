"""
Integer Programming Solver for Multi-Market Arbitrage Detection.

Uses Gurobi (or fallback to scipy) to solve integer programs that detect
arbitrage opportunities across logically dependent prediction markets.

Key Concepts:
- Each market has multiple conditions (outcomes), exactly one must be TRUE
- Dependencies between markets add constraints (implications, mutual exclusions)
- Arbitrage exists if we can buy a portfolio guaranteeing payout > cost

Mathematical Formulation:
    min  Σ price_i * z_i           (minimize cost of portfolio)
    s.t. Σ z_i = 1 for each market (exactly one outcome is true)
         z_A ≤ z_B                  (implication: A → B)
         z_A + z_B ≤ 1             (mutual exclusion: ¬(A ∧ B))
         z_i ∈ {0, 1}              (binary variables)

If optimal cost < 1.0, arbitrage exists (guaranteed $1 payout for < $1 cost).
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

# Try to import Gurobi, fall back to scipy
try:
    import gurobipy as gp
    from gurobipy import GRB
    HAS_GUROBI = True
except ImportError:
    HAS_GUROBI = False
    gp = None
    GRB = None

try:
    from scipy.optimize import linprog, milp, LinearConstraint, Bounds
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# Configure logging
logger = logging.getLogger(__name__)


class ConstraintType(Enum):
    """Type of constraint between conditions."""
    IMPLICATION = "implication"      # z_A ≤ z_B
    MUTUAL_EXCLUSION = "mutex"       # z_A + z_B ≤ 1
    EQUIVALENCE = "equivalence"      # z_A = z_B


@dataclass
class Market:
    """Represents a prediction market with multiple outcomes."""
    market_id: str
    conditions: List[str]  # e.g., ["Yes", "No"] or ["A", "B", "C"]
    prices: Dict[str, float] = field(default_factory=dict)  # condition -> price

    def __post_init__(self):
        # Initialize prices to 0 if not provided
        for cond in self.conditions:
            if cond not in self.prices:
                self.prices[cond] = 0.0


@dataclass
class DependencyConstraint:
    """Constraint between two market conditions."""
    constraint_type: ConstraintType
    market_a: str
    condition_a: str
    market_b: str
    condition_b: str

    @property
    def cond_a(self) -> Tuple[str, str]:
        return (self.market_a, self.condition_a)

    @property
    def cond_b(self) -> Tuple[str, str]:
        return (self.market_b, self.condition_b)


@dataclass
class ArbitrageResult:
    """Result of arbitrage detection."""
    has_arbitrage: bool
    min_cost: float
    profit: float
    positions: Dict[Tuple[str, str], float]  # (market_id, condition) -> quantity
    solver_status: str
    solve_time: float = 0.0

    def __str__(self) -> str:
        if self.has_arbitrage:
            return (
                f"ARBITRAGE FOUND: Cost=${self.min_cost:.4f}, "
                f"Profit=${self.profit:.4f} ({self.profit*100:.2f}%)"
            )
        return f"No arbitrage (min_cost=${self.min_cost:.4f})"


class GurobiIPSolver:
    """
    Integer Programming solver using Gurobi.

    Implements the IP formulation from Kroer et al. for detecting
    arbitrage across dependent prediction markets.
    """

    def __init__(self, silent: bool = True):
        """
        Initialize solver.

        Args:
            silent: Suppress Gurobi output
        """
        if not HAS_GUROBI:
            raise ImportError(
                "Gurobi required for IP solver. "
                "Install with: pip install gurobipy"
            )

        self.silent = silent
        self._env = None

    def _get_env(self) -> "gp.Env":
        """Get or create Gurobi environment."""
        if self._env is None:
            self._env = gp.Env(empty=True)
            if self.silent:
                self._env.setParam('OutputFlag', 0)
            self._env.start()
        return self._env

    def build_model(
        self,
        markets: Dict[str, Market],
        dependencies: List[DependencyConstraint],
        prices: Optional[Dict[Tuple[str, str], float]] = None
    ) -> "gp.Model":
        """
        Build Gurobi model for arbitrage detection.

        Args:
            markets: Dict of market_id -> Market
            dependencies: List of dependency constraints
            prices: Optional override for prices {(market_id, cond): price}

        Returns:
            Gurobi Model ready to optimize
        """
        env = self._get_env()
        model = gp.Model(env=env)

        # Create binary variable for each condition
        z = {}
        for market_id, market in markets.items():
            for cond in market.conditions:
                z[(market_id, cond)] = model.addVar(
                    vtype=GRB.BINARY,
                    name=f"z_{market_id}_{cond}"
                )

        model.update()

        # Constraint 1: Exactly one condition per market must be TRUE
        for market_id, market in markets.items():
            model.addConstr(
                gp.quicksum(z[(market_id, c)] for c in market.conditions) == 1,
                name=f"one_outcome_{market_id}"
            )

        # Constraint 2: Dependency constraints
        for i, dep in enumerate(dependencies):
            if dep.constraint_type == ConstraintType.IMPLICATION:
                # z_A ≤ z_B (if A is true, B must be true)
                if dep.cond_a in z and dep.cond_b in z:
                    model.addConstr(
                        z[dep.cond_a] <= z[dep.cond_b],
                        name=f"impl_{i}"
                    )

            elif dep.constraint_type == ConstraintType.MUTUAL_EXCLUSION:
                # z_A + z_B ≤ 1 (both cannot be true)
                if dep.cond_a in z and dep.cond_b in z:
                    model.addConstr(
                        z[dep.cond_a] + z[dep.cond_b] <= 1,
                        name=f"mutex_{i}"
                    )

            elif dep.constraint_type == ConstraintType.EQUIVALENCE:
                # z_A = z_B (both must have same value)
                if dep.cond_a in z and dep.cond_b in z:
                    model.addConstr(
                        z[dep.cond_a] == z[dep.cond_b],
                        name=f"equiv_{i}"
                    )

        # Get prices
        if prices is None:
            prices = {}
            for market_id, market in markets.items():
                for cond, price in market.prices.items():
                    prices[(market_id, cond)] = price

        # Objective: minimize cost
        cost = gp.quicksum(
            prices.get(key, 0) * z[key]
            for key in z.keys()
        )
        model.setObjective(cost, GRB.MINIMIZE)

        # Store variables for later access
        model._z = z
        model._markets = markets

        return model

    def solve(
        self,
        markets: Dict[str, Market],
        dependencies: List[DependencyConstraint],
        prices: Optional[Dict[Tuple[str, str], float]] = None,
        arbitrage_threshold: float = 0.98
    ) -> ArbitrageResult:
        """
        Solve for arbitrage.

        Args:
            markets: Dict of market_id -> Market
            dependencies: List of dependency constraints
            prices: Optional price overrides
            arbitrage_threshold: Cost must be below this for arbitrage

        Returns:
            ArbitrageResult with solution details
        """
        import time
        start_time = time.time()

        model = self.build_model(markets, dependencies, prices)
        model.optimize()

        solve_time = time.time() - start_time

        if model.status == GRB.OPTIMAL:
            min_cost = model.objVal
            positions = {
                k: v.X
                for k, v in model._z.items()
                if v.X > 0.5  # Binary variable is 1
            }

            return ArbitrageResult(
                has_arbitrage=min_cost < arbitrage_threshold,
                min_cost=min_cost,
                profit=max(0, 1.0 - min_cost),
                positions=positions,
                solver_status="optimal",
                solve_time=solve_time
            )

        elif model.status == GRB.INFEASIBLE:
            return ArbitrageResult(
                has_arbitrage=False,
                min_cost=float('inf'),
                profit=0,
                positions={},
                solver_status="infeasible",
                solve_time=solve_time
            )

        else:
            return ArbitrageResult(
                has_arbitrage=False,
                min_cost=float('inf'),
                profit=0,
                positions={},
                solver_status=f"status_{model.status}",
                solve_time=solve_time
            )

    def solve_with_forced_condition(
        self,
        markets: Dict[str, Market],
        dependencies: List[DependencyConstraint],
        force_condition: Tuple[str, str],
        force_value: bool
    ) -> ArbitrageResult:
        """
        Solve with a specific condition forced to TRUE or FALSE.

        Used by InitFW (Algorithm 3) to determine if a condition
        can be TRUE or FALSE given the constraints.

        Args:
            markets: Dict of market_id -> Market
            dependencies: List of dependency constraints
            force_condition: (market_id, condition) to force
            force_value: True to force condition=1, False to force condition=0

        Returns:
            ArbitrageResult (feasible status indicates if forcing is possible)
        """
        model = self.build_model(markets, dependencies)

        # Add forcing constraint
        z = model._z
        if force_condition in z:
            if force_value:
                model.addConstr(z[force_condition] == 1, name="force_true")
            else:
                model.addConstr(z[force_condition] == 0, name="force_false")

        model.optimize()

        if model.status == GRB.OPTIMAL:
            positions = {
                k: v.X
                for k, v in z.items()
                if v.X > 0.5
            }
            return ArbitrageResult(
                has_arbitrage=False,  # Not checking for arb, just feasibility
                min_cost=model.objVal,
                profit=0,
                positions=positions,
                solver_status="optimal"
            )
        else:
            return ArbitrageResult(
                has_arbitrage=False,
                min_cost=float('inf'),
                profit=0,
                positions={},
                solver_status="infeasible"
            )

    def close(self) -> None:
        """Release Gurobi environment."""
        if self._env is not None:
            self._env.close()
            self._env = None


class ScipyIPSolver:
    """
    Fallback IP solver using scipy's milp.

    Less efficient than Gurobi but doesn't require a license.
    """

    def __init__(self):
        """Initialize solver."""
        if not HAS_SCIPY:
            raise ImportError("scipy required. Install with: pip install scipy")

    def solve(
        self,
        markets: Dict[str, Market],
        dependencies: List[DependencyConstraint],
        prices: Optional[Dict[Tuple[str, str], float]] = None,
        arbitrage_threshold: float = 0.98
    ) -> ArbitrageResult:
        """
        Solve for arbitrage using scipy.optimize.milp.

        Args:
            markets: Dict of market_id -> Market
            dependencies: List of dependency constraints
            prices: Optional price overrides
            arbitrage_threshold: Cost must be below this for arbitrage

        Returns:
            ArbitrageResult with solution details
        """
        import time
        start_time = time.time()

        # Build variable index
        var_index = {}
        idx = 0
        for market_id, market in markets.items():
            for cond in market.conditions:
                var_index[(market_id, cond)] = idx
                idx += 1

        n_vars = len(var_index)

        # Objective: minimize cost
        c = np.zeros(n_vars)
        if prices is None:
            prices = {}
            for market_id, market in markets.items():
                for cond, price in market.prices.items():
                    prices[(market_id, cond)] = price

        for key, price in prices.items():
            if key in var_index:
                c[var_index[key]] = price

        # Build constraints
        A_eq = []
        b_eq = []
        A_ub = []
        b_ub = []

        # Constraint 1: Exactly one condition per market
        for market_id, market in markets.items():
            row = np.zeros(n_vars)
            for cond in market.conditions:
                row[var_index[(market_id, cond)]] = 1
            A_eq.append(row)
            b_eq.append(1)

        # Constraint 2: Dependencies
        for dep in dependencies:
            if dep.cond_a not in var_index or dep.cond_b not in var_index:
                continue

            i_a = var_index[dep.cond_a]
            i_b = var_index[dep.cond_b]

            if dep.constraint_type == ConstraintType.IMPLICATION:
                # z_A ≤ z_B  =>  z_A - z_B ≤ 0
                row = np.zeros(n_vars)
                row[i_a] = 1
                row[i_b] = -1
                A_ub.append(row)
                b_ub.append(0)

            elif dep.constraint_type == ConstraintType.MUTUAL_EXCLUSION:
                # z_A + z_B ≤ 1
                row = np.zeros(n_vars)
                row[i_a] = 1
                row[i_b] = 1
                A_ub.append(row)
                b_ub.append(1)

            elif dep.constraint_type == ConstraintType.EQUIVALENCE:
                # z_A = z_B  =>  z_A - z_B = 0
                row = np.zeros(n_vars)
                row[i_a] = 1
                row[i_b] = -1
                A_eq.append(row)
                b_eq.append(0)

        # Convert to arrays
        A_eq = np.array(A_eq) if A_eq else None
        b_eq = np.array(b_eq) if b_eq else None
        A_ub = np.array(A_ub) if A_ub else None
        b_ub = np.array(b_ub) if b_ub else None

        # Variable bounds (binary: 0 or 1)
        bounds = Bounds(lb=np.zeros(n_vars), ub=np.ones(n_vars))

        # Solve using milp
        constraints = []
        if A_eq is not None:
            constraints.append(LinearConstraint(A_eq, b_eq, b_eq))
        if A_ub is not None:
            constraints.append(LinearConstraint(A_ub, -np.inf, b_ub))

        # Integer constraints (all binary)
        integrality = np.ones(n_vars)

        try:
            result = milp(
                c=c,
                constraints=constraints,
                bounds=bounds,
                integrality=integrality
            )

            solve_time = time.time() - start_time

            if result.success:
                min_cost = result.fun
                positions = {
                    key: 1.0
                    for key, idx in var_index.items()
                    if result.x[idx] > 0.5
                }

                return ArbitrageResult(
                    has_arbitrage=min_cost < arbitrage_threshold,
                    min_cost=min_cost,
                    profit=max(0, 1.0 - min_cost),
                    positions=positions,
                    solver_status="optimal",
                    solve_time=solve_time
                )
            else:
                return ArbitrageResult(
                    has_arbitrage=False,
                    min_cost=float('inf'),
                    profit=0,
                    positions={},
                    solver_status=result.message,
                    solve_time=solve_time
                )

        except Exception as e:
            return ArbitrageResult(
                has_arbitrage=False,
                min_cost=float('inf'),
                profit=0,
                positions={},
                solver_status=f"error: {e}",
                solve_time=time.time() - start_time
            )


def get_solver(prefer_gurobi: bool = True) -> Any:
    """
    Get the best available solver.

    Args:
        prefer_gurobi: Prefer Gurobi if available

    Returns:
        IP solver instance
    """
    if prefer_gurobi and HAS_GUROBI:
        logger.info("Using Gurobi solver")
        return GurobiIPSolver()
    elif HAS_SCIPY:
        logger.info("Using scipy MILP solver (Gurobi not available)")
        return ScipyIPSolver()
    else:
        raise ImportError(
            "No solver available. Install gurobipy or scipy."
        )


# Convenience function for testing
def main():
    """Test the IP solver."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Example: Two related markets
    markets = {
        "trump_pa": Market(
            market_id="trump_pa",
            conditions=["Yes", "No"],
            prices={"Yes": 0.55, "No": 0.48}  # Sum = 1.03, potential arb
        ),
        "rep_pa": Market(
            market_id="rep_pa",
            conditions=["Yes", "No"],
            prices={"Yes": 0.60, "No": 0.42}  # Sum = 1.02
        ),
        "duke": Market(
            market_id="duke",
            conditions=["Yes", "No"],
            prices={"Yes": 0.15, "No": 0.87}  # Sum = 1.02
        ),
        "unc": Market(
            market_id="unc",
            conditions=["Yes", "No"],
            prices={"Yes": 0.12, "No": 0.90}  # Sum = 1.02
        ),
    }

    # Dependencies
    dependencies = [
        # Trump wins PA → Republican wins PA
        DependencyConstraint(
            constraint_type=ConstraintType.IMPLICATION,
            market_a="trump_pa", condition_a="Yes",
            market_b="rep_pa", condition_b="Yes"
        ),
        # Duke wins and UNC wins are mutually exclusive
        DependencyConstraint(
            constraint_type=ConstraintType.MUTUAL_EXCLUSION,
            market_a="duke", condition_a="Yes",
            market_b="unc", condition_b="Yes"
        ),
    ]

    # Get solver
    solver = get_solver()

    # Solve
    print("\nSolving for arbitrage...\n")
    result = solver.solve(markets, dependencies)

    print(result)
    print(f"\nPositions: {result.positions}")
    print(f"Solve time: {result.solve_time*1000:.2f}ms")

    # Test with forced conditions
    print("\n--- Testing forced conditions ---")

    # Can Trump=Yes be true?
    result_trump_yes = solver.solve_with_forced_condition(
        markets, dependencies, ("trump_pa", "Yes"), True
    )
    print(f"Trump=Yes feasible: {result_trump_yes.solver_status == 'optimal'}")

    # Can Trump=Yes AND Rep=No both be true?
    # This should be infeasible due to implication
    extra_deps = dependencies + [
        DependencyConstraint(
            constraint_type=ConstraintType.EQUIVALENCE,
            market_a="rep_pa", condition_a="No",
            market_b="rep_pa", condition_b="No"  # Force No
        )
    ]

    if hasattr(solver, 'close'):
        solver.close()


if __name__ == "__main__":
    main()
