"""
Frank-Wolfe Optimization Engine for Arbitrage Extraction.

Implements Algorithms 2 & 3 from Kroer et al. paper:
- InitFW (Algorithm 3): Initialize active set and interior point
- Barrier Frank-Wolfe (Algorithm 2): Compute optimal arbitrage-removing trades

Key Concepts:
- Bregman Divergence: Measures "distance" between price vectors
- Frank-Wolfe Gap: Convergence measure for the optimization
- α-extraction: Guaranteed to capture α fraction of available arbitrage

Mathematical Background:
- For LMSR (Logarithmic Market Scoring Rule):
  - Bregman divergence D(μ||p) = KL divergence = Σ μ_i * ln(μ_i / p_i)
  - Gradient ∇R(μ) = ln(μ) + 1
  - Guaranteed profit ≥ D - gap (Proposition 4.1)
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
from scipy.optimize import minimize

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class FWResult:
    """Result of Frank-Wolfe optimization."""
    converged: bool
    iterations: int
    optimal_mu: np.ndarray  # Optimal probability vector
    guaranteed_profit: float
    bregman_divergence: float
    final_gap: float
    alpha_captured: float  # Fraction of arbitrage captured
    solve_time: float = 0.0

    def __str__(self) -> str:
        if self.converged:
            return (
                f"FW Converged in {self.iterations} iters: "
                f"Profit=${self.guaranteed_profit:.4f}, "
                f"α={self.alpha_captured:.1%} captured"
            )
        return f"FW did not converge after {self.iterations} iterations"


@dataclass
class PayoffVector:
    """A vertex of the marginal polytope (valid outcome assignment)."""
    values: np.ndarray  # Binary vector of outcomes
    market_outcomes: Dict[str, str]  # market_id -> selected outcome

    def __hash__(self):
        return hash(tuple(self.values))


class FrankWolfeOptimizer:
    """
    Barrier Frank-Wolfe optimizer for arbitrage extraction.

    Implements Algorithm 2 from Kroer et al. paper.

    The algorithm finds the optimal trade that removes arbitrage from
    the market while maximizing guaranteed profit.

    Key Features:
    - Uses IP oracle to find descent directions
    - Adaptive epsilon contraction for numerical stability
    - Guaranteed α-extraction of available arbitrage
    """

    # Small epsilon for numerical stability
    EPS = 1e-10

    def __init__(
        self,
        ip_solver: Any,
        alpha: float = 0.9,
        max_iterations: int = 150,
        initial_epsilon: float = 0.1
    ):
        """
        Initialize Frank-Wolfe optimizer.

        Args:
            ip_solver: Integer programming solver for finding vertices
            alpha: Target fraction of arbitrage to extract (default 0.9 = 90%)
            max_iterations: Maximum optimization iterations
            initial_epsilon: Initial contraction parameter
        """
        self.ip_solver = ip_solver
        self.alpha = alpha
        self.max_iterations = max_iterations
        self.initial_epsilon = initial_epsilon

    def init_fw(
        self,
        markets: Dict[str, Any],
        partial_outcome: Dict[Tuple[str, str], int],
        dependencies: List[Any]
    ) -> Tuple[List[PayoffVector], np.ndarray, Dict[Tuple[str, str], int]]:
        """
        Algorithm 3 (InitFW) from Kroer et al.

        Initializes the Frank-Wolfe algorithm by:
        1. Finding all vertices of the marginal polytope
        2. Computing an interior point
        3. Extending partial outcomes where forced

        Args:
            markets: Dict of market_id -> Market objects
            partial_outcome: Already settled securities {(market, cond): 0/1}
            dependencies: List of dependency constraints

        Returns:
            Z_0: Initial active set of payoff vectors
            u: Interior point (all coordinates in (0,1))
            sigma_hat: Extended partial outcome
        """
        logger.info("Running InitFW (Algorithm 3)")

        Z_0 = []
        sigma_hat = partial_outcome.copy()

        # Build variable index
        var_index = {}
        idx = 0
        for market_id, market in markets.items():
            for cond in market.conditions:
                var_index[(market_id, cond)] = idx
                idx += 1

        n_vars = len(var_index)

        # For each unsettled security, check if it can be TRUE or FALSE
        unsettled = [
            key for key in var_index.keys()
            if key not in partial_outcome
        ]

        for key in unsettled:
            market_id, cond = key

            # Question 1: Can this condition be TRUE?
            result_true = self.ip_solver.solve_with_forced_condition(
                markets, dependencies, key, True
            )
            has_true = result_true.solver_status == "optimal"

            # Question 2: Can this condition be FALSE?
            result_false = self.ip_solver.solve_with_forced_condition(
                markets, dependencies, key, False
            )
            has_false = result_false.solver_status == "optimal"

            if has_true and has_false:
                # Uncertain - add both solution vectors to active set
                if has_true and result_true.positions:
                    vec = self._positions_to_vector(result_true.positions, var_index, n_vars)
                    Z_0.append(PayoffVector(
                        values=vec,
                        market_outcomes={k[0]: k[1] for k, v in result_true.positions.items() if v > 0.5}
                    ))

                if has_false and result_false.positions:
                    vec = self._positions_to_vector(result_false.positions, var_index, n_vars)
                    Z_0.append(PayoffVector(
                        values=vec,
                        market_outcomes={k[0]: k[1] for k, v in result_false.positions.items() if v > 0.5}
                    ))

            elif has_true:
                # Must be TRUE
                sigma_hat[key] = 1
                logger.debug(f"Forced TRUE: {key}")

            elif has_false:
                # Must be FALSE
                sigma_hat[key] = 0
                logger.debug(f"Forced FALSE: {key}")

            else:
                logger.warning(f"Infeasible market condition: {key}")

        # Remove duplicates from Z_0
        seen = set()
        unique_Z = []
        for pv in Z_0:
            h = hash(tuple(pv.values))
            if h not in seen:
                seen.add(h)
                unique_Z.append(pv)
        Z_0 = unique_Z

        # If we don't have enough vertices, add a default solution
        if len(Z_0) < 2:
            result = self.ip_solver.solve(markets, dependencies)
            if result.positions:
                vec = self._positions_to_vector(result.positions, var_index, n_vars)
                Z_0.append(PayoffVector(
                    values=vec,
                    market_outcomes={k[0]: k[1] for k, v in result.positions.items() if v > 0.5}
                ))

        # Compute interior point as average of vertices
        if Z_0:
            u = np.mean([pv.values for pv in Z_0], axis=0)
            # Ensure u is strictly interior (all values in (0,1))
            u = np.clip(u, self.EPS, 1 - self.EPS)
        else:
            # Fallback: uniform distribution
            u = np.ones(n_vars) * 0.5

        logger.info(f"InitFW complete: {len(Z_0)} vertices, {len(sigma_hat)} settled")

        return Z_0, u, sigma_hat

    def _positions_to_vector(
        self,
        positions: Dict[Tuple[str, str], float],
        var_index: Dict[Tuple[str, str], int],
        n_vars: int
    ) -> np.ndarray:
        """Convert positions dict to numpy vector."""
        vec = np.zeros(n_vars)
        for key, val in positions.items():
            if key in var_index and val > 0.5:
                vec[var_index[key]] = 1.0
        return vec

    def optimize(
        self,
        markets: Dict[str, Any],
        dependencies: List[Any],
        current_prices: np.ndarray,
        partial_outcome: Optional[Dict] = None
    ) -> FWResult:
        """
        Algorithm 2 (Barrier Frank-Wolfe) from Kroer et al.

        Finds optimal trade that removes arbitrage while maximizing profit.

        Args:
            markets: Dict of market_id -> Market objects
            dependencies: List of dependency constraints
            current_prices: Current market prices as numpy array
            partial_outcome: Already settled securities

        Returns:
            FWResult with optimal solution
        """
        import time
        start_time = time.time()

        if partial_outcome is None:
            partial_outcome = {}

        # Build variable index for price mapping
        var_index = {}
        idx = 0
        for market_id, market in markets.items():
            for cond in market.conditions:
                var_index[(market_id, cond)] = idx
                idx += 1

        n_vars = len(var_index)

        # Ensure prices are valid
        p_current = np.clip(current_prices, self.EPS, 1 - self.EPS)

        # Step 1: Initialize
        Z_t, u, sigma_hat = self.init_fw(markets, partial_outcome, dependencies)

        if len(Z_t) == 0:
            return FWResult(
                converged=False,
                iterations=0,
                optimal_mu=p_current,
                guaranteed_profit=0,
                bregman_divergence=0,
                final_gap=float('inf'),
                alpha_captured=0,
                solve_time=time.time() - start_time
            )

        epsilon_t = self.initial_epsilon
        best_profit = -np.inf
        best_mu = None

        for t in range(self.max_iterations):
            # Step 2a: Solve convex subproblem over conv(Z_t)
            mu_t = self._solve_convex_subproblem(Z_t, p_current, epsilon_t, u)

            # Step 2b: Compute gradient
            gradient = self._compute_gradient_lmsr(mu_t)

            # Step 2c: IP oracle - find descent vertex
            z_new = self._solve_ip_oracle(gradient, markets, dependencies, sigma_hat, var_index)

            if z_new is not None:
                # Add to active set
                Z_t.append(z_new)

            # Step 2d: Compute Frank-Wolfe gap
            gap = self._compute_fw_gap(mu_t, z_new, gradient) if z_new else 0

            # Step 2e: Compute Bregman divergence
            D = self._bregman_divergence_kl(mu_t, p_current)

            # Proposition 4.1: Guaranteed profit
            guaranteed_profit = max(0, D - gap)

            if guaranteed_profit > best_profit:
                best_profit = guaranteed_profit
                best_mu = mu_t.copy()

            # Step 2f: Adaptive epsilon
            if z_new is not None:
                g_u = self._compute_gap_at_interior(u, gradient, Z_t)
                if g_u < 0 and gap / (-4 * g_u) < epsilon_t:
                    epsilon_t = min(gap / (-4 * g_u), epsilon_t / 2)
                    epsilon_t = max(epsilon_t, 1e-6)  # Don't go too small

            # Logging
            if t % 10 == 0:
                logger.info(
                    f"Iter {t}: D={D:.4f}, gap={gap:.4f}, "
                    f"profit={guaranteed_profit:.4f}, ε={epsilon_t:.4f}"
                )

            # Stopping conditions
            if gap <= (1 - self.alpha) * D and D > 0:
                logger.info(
                    f"Converged at iteration {t}: "
                    f"capturing {100 * (D - gap) / D:.1f}% of arbitrage"
                )
                return FWResult(
                    converged=True,
                    iterations=t + 1,
                    optimal_mu=best_mu if best_mu is not None else mu_t,
                    guaranteed_profit=best_profit,
                    bregman_divergence=D,
                    final_gap=gap,
                    alpha_captured=(D - gap) / D if D > 0 else 0,
                    solve_time=time.time() - start_time
                )

            if D < 0.01:
                logger.info("Market nearly arbitrage-free")
                return FWResult(
                    converged=True,
                    iterations=t + 1,
                    optimal_mu=mu_t,
                    guaranteed_profit=0,
                    bregman_divergence=D,
                    final_gap=gap,
                    alpha_captured=0,
                    solve_time=time.time() - start_time
                )

        # Did not converge
        return FWResult(
            converged=False,
            iterations=self.max_iterations,
            optimal_mu=best_mu if best_mu is not None else p_current,
            guaranteed_profit=best_profit,
            bregman_divergence=self._bregman_divergence_kl(best_mu, p_current) if best_mu is not None else 0,
            final_gap=gap,
            alpha_captured=self.alpha,
            solve_time=time.time() - start_time
        )

    def _solve_convex_subproblem(
        self,
        vertices: List[PayoffVector],
        p_current: np.ndarray,
        epsilon: float,
        u: np.ndarray
    ) -> np.ndarray:
        """
        Solve the convex subproblem over conv(vertices).

        Finds μ that minimizes Bregman divergence D(μ||p) subject to
        μ being in the epsilon-contracted polytope.
        """
        if len(vertices) == 0:
            return p_current.copy()

        if len(vertices) == 1:
            # Single vertex - contract towards interior
            return (1 - epsilon) * vertices[0].values + epsilon * u

        # Stack vertices into matrix
        V = np.array([v.values for v in vertices])  # shape: (n_vertices, n_vars)
        n_vertices = len(vertices)

        # We want to minimize D(V @ lambda, p) over lambda in simplex
        # where lambda are convex combination weights

        def objective(lam):
            mu = V.T @ lam  # Convex combination
            mu = (1 - epsilon) * mu + epsilon * u  # Contract
            mu = np.clip(mu, self.EPS, 1 - self.EPS)
            return self._bregman_divergence_kl(mu, p_current)

        # Initial guess: uniform weights
        lam0 = np.ones(n_vertices) / n_vertices

        # Constraints: lambda >= 0, sum(lambda) = 1
        constraints = [
            {'type': 'eq', 'fun': lambda x: np.sum(x) - 1}
        ]
        bounds = [(0, 1) for _ in range(n_vertices)]

        try:
            result = minimize(
                objective,
                lam0,
                method='SLSQP',
                bounds=bounds,
                constraints=constraints,
                options={'maxiter': 100, 'ftol': 1e-8}
            )

            if result.success:
                lam = result.x
                mu = V.T @ lam
                mu = (1 - epsilon) * mu + epsilon * u
                return np.clip(mu, self.EPS, 1 - self.EPS)
        except Exception as e:
            logger.warning(f"Subproblem optimization failed: {e}")

        # Fallback: return interior point
        return u.copy()

    def _compute_gradient_lmsr(self, mu: np.ndarray) -> np.ndarray:
        """
        Compute gradient of LMSR regularizer.

        For LMSR: ∇R(μ) = ln(μ) + 1
        """
        mu_safe = np.clip(mu, self.EPS, 1 - self.EPS)
        return np.log(mu_safe) + 1

    def _solve_ip_oracle(
        self,
        gradient: np.ndarray,
        markets: Dict[str, Any],
        dependencies: List[Any],
        sigma_hat: Dict,
        var_index: Dict
    ) -> Optional[PayoffVector]:
        """
        IP Oracle: Find vertex that minimizes inner product with gradient.

        This finds a descent direction for the Frank-Wolfe algorithm.
        """
        # Build prices from gradient (negated because we minimize)
        prices = {}
        for key, idx in var_index.items():
            prices[key] = gradient[idx]

        # Solve IP with gradient as cost
        result = self.ip_solver.solve(markets, dependencies, prices=prices)

        if result.solver_status == "optimal" and result.positions:
            vec = self._positions_to_vector(result.positions, var_index, len(gradient))
            return PayoffVector(
                values=vec,
                market_outcomes={k[0]: k[1] for k, v in result.positions.items() if v > 0.5}
            )

        return None

    def _compute_fw_gap(
        self,
        mu: np.ndarray,
        z_new: Optional[PayoffVector],
        gradient: np.ndarray
    ) -> float:
        """
        Compute Frank-Wolfe gap: g(μ) = max_z <∇R(μ), μ - z>

        The gap measures how far we are from optimality.
        """
        if z_new is None:
            return 0.0

        # Gap = <gradient, mu - z_new>
        return np.dot(gradient, mu - z_new.values)

    def _compute_gap_at_interior(
        self,
        u: np.ndarray,
        gradient: np.ndarray,
        vertices: List[PayoffVector]
    ) -> float:
        """Compute gap at interior point u."""
        if not vertices:
            return 0.0

        # Find vertex that minimizes inner product with gradient
        min_val = float('inf')
        for v in vertices:
            val = np.dot(gradient, v.values)
            min_val = min(min_val, val)

        return np.dot(gradient, u) - min_val

    def _bregman_divergence_kl(self, mu: np.ndarray, p: np.ndarray) -> float:
        """
        Compute Bregman divergence (KL divergence) for LMSR.

        D(μ||p) = Σ μ_i * ln(μ_i / p_i) - Σ μ_i + Σ p_i

        For probability vectors this simplifies to KL divergence.
        """
        mu_safe = np.clip(mu, self.EPS, 1 - self.EPS)
        p_safe = np.clip(p, self.EPS, 1 - self.EPS)

        # KL divergence
        kl = np.sum(mu_safe * np.log(mu_safe / p_safe))

        return max(0, kl)


def compute_optimal_trade(
    markets: Dict[str, Any],
    dependencies: List[Any],
    ip_solver: Any,
    alpha: float = 0.9
) -> Tuple[FWResult, Dict[str, float]]:
    """
    Convenience function to compute optimal arbitrage trade.

    Args:
        markets: Dict of market_id -> Market objects
        dependencies: List of dependency constraints
        ip_solver: IP solver instance
        alpha: Target arbitrage extraction (0.9 = 90%)

    Returns:
        (FWResult, trade_dict) where trade_dict maps (market_id, cond) -> trade_amount
    """
    # Build price vector from markets
    var_index = {}
    prices = []
    idx = 0
    for market_id, market in markets.items():
        for cond in market.conditions:
            var_index[(market_id, cond)] = idx
            prices.append(market.prices.get(cond, 0.5))
            idx += 1

    p_current = np.array(prices)

    # Run Frank-Wolfe
    optimizer = FrankWolfeOptimizer(ip_solver, alpha=alpha)
    result = optimizer.optimize(markets, dependencies, p_current)

    # Compute trades (difference between optimal and current)
    trades = {}
    if result.optimal_mu is not None:
        for key, idx in var_index.items():
            diff = result.optimal_mu[idx] - p_current[idx]
            if abs(diff) > 0.01:  # Only significant trades
                trades[key] = diff

    return result, trades


# Test code
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    from src.optimization.ip_solver import Market, DependencyConstraint, ConstraintType, get_solver

    # Example markets
    markets = {
        "m1": Market("m1", ["Yes", "No"], {"Yes": 0.55, "No": 0.48}),
        "m2": Market("m2", ["Yes", "No"], {"Yes": 0.45, "No": 0.58}),
    }

    constraints = [
        DependencyConstraint(
            ConstraintType.IMPLICATION,
            "m1", "Yes", "m2", "Yes"
        )
    ]

    solver = get_solver(prefer_gurobi=False)

    result, trades = compute_optimal_trade(markets, constraints, solver)
    print(f"\n{result}")
    print(f"Recommended trades: {trades}")
