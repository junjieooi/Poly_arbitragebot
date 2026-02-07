"""
Phase 2 Runner: Dependency Detection & IP Formulation

This script:
1. Fetches active markets from Polymarket
2. Uses Claude to find logical dependencies between markets
3. Builds IP models and solves for arbitrage

Usage:
    python -m src.run_phase2 [--markets N] [--validate]

Environment Variables:
    ANTHROPIC_API_KEY - Claude API key (required)
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import aiohttp

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.detection.dependency_finder import (
    DependencyFinder,
    MarketInfo,
    Dependency,
    ConstraintType as DepConstraintType
)
from src.optimization.ip_solver import (
    get_solver,
    Market,
    DependencyConstraint,
    ConstraintType as IPConstraintType,
    ArbitrageResult
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def fetch_markets_with_details(limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch active markets from Polymarket Gamma API."""
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": min(limit * 2, 100),
        "order": "liquidityNum",
        "ascending": "false"
    }

    markets = []

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=30) as resp:
                if resp.status != 200:
                    logger.error(f"API returned status {resp.status}")
                    return []

                data = await resp.json()

                for market in data:
                    liquidity = float(market.get('liquidityNum', 0) or 0)
                    if liquidity < 1000:
                        continue

                    if not market.get('acceptingOrders', True):
                        continue

                    try:
                        clob_token_ids = json.loads(market.get('clobTokenIds', '[]'))
                        outcome_prices = json.loads(market.get('outcomePrices', '[]'))
                        outcomes = json.loads(market.get('outcomes', '[]'))
                    except (json.JSONDecodeError, TypeError):
                        continue

                    if len(clob_token_ids) < 2 or len(outcome_prices) < 2:
                        continue

                    markets.append({
                        'market_id': market.get('conditionId', ''),
                        'question': market.get('question', ''),
                        'description': market.get('description', ''),
                        'outcomes': outcomes,
                        'outcome_prices': [float(p) for p in outcome_prices],
                        'token_ids': clob_token_ids,
                        'liquidity': liquidity,
                        'end_date': market.get('endDate', '')
                    })

                    if len(markets) >= limit:
                        break

        except Exception as e:
            logger.error(f"Error fetching markets: {e}")

    return markets


def convert_dependency_to_constraint(dep: Dependency) -> DependencyConstraint:
    """Convert DependencyFinder Dependency to IP solver constraint."""
    # Map constraint types
    type_map = {
        DepConstraintType.IMPLICATION: IPConstraintType.IMPLICATION,
        DepConstraintType.MUTUAL_EXCLUSION: IPConstraintType.MUTUAL_EXCLUSION,
        DepConstraintType.EQUIVALENCE: IPConstraintType.EQUIVALENCE,
    }

    return DependencyConstraint(
        constraint_type=type_map.get(dep.constraint_type, IPConstraintType.IMPLICATION),
        market_a=dep.market_a_id,
        condition_a=dep.condition_a,
        market_b=dep.market_b_id,
        condition_b=dep.condition_b
    )


def markets_to_ip_format(
    api_markets: List[Dict[str, Any]]
) -> Dict[str, Market]:
    """Convert API market data to IP solver format."""
    markets = {}

    for m in api_markets:
        market_id = m['market_id']
        outcomes = m.get('outcomes', ['Yes', 'No'])
        prices = m.get('outcome_prices', [0.5, 0.5])

        # Build price dict
        price_dict = {}
        for i, outcome in enumerate(outcomes):
            if i < len(prices):
                price_dict[outcome] = prices[i]

        markets[market_id] = Market(
            market_id=market_id,
            conditions=outcomes,
            prices=price_dict
        )

    return markets


async def run_phase2(
    num_markets: int = 20,
    max_pairs: int = 50,
    validate: bool = False
):
    """
    Run Phase 2: Dependency detection and IP arbitrage solving.

    Args:
        num_markets: Number of markets to analyze
        max_pairs: Maximum market pairs to analyze for dependencies
        validate: Interactively validate dependencies
    """
    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY environment variable required")
        print("\nSet your Anthropic API key:")
        print("  export ANTHROPIC_API_KEY=your_key_here")
        return

    print("\n" + "=" * 60)
    print("PHASE 2: Dependency Detection & IP Formulation")
    print("=" * 60)

    # Step 1: Fetch markets
    print(f"\n[1/4] Fetching {num_markets} active markets...")
    api_markets = await fetch_markets_with_details(num_markets)

    if not api_markets:
        print("No markets found. Exiting.")
        return

    print(f"  Found {len(api_markets)} markets with liquidity > $1000")

    # Show sample markets
    print("\n  Sample markets:")
    for i, m in enumerate(api_markets[:5]):
        q = m['question'][:50] + '...' if len(m['question']) > 50 else m['question']
        print(f"    {i+1}. {q}")
        print(f"       Prices: {m['outcome_prices']}, Liquidity: ${m['liquidity']:,.0f}")

    # Step 2: Find dependencies using Claude
    print(f"\n[2/4] Analyzing market pairs for dependencies...")

    # Convert to MarketInfo for dependency finder
    market_infos = [
        MarketInfo(
            market_id=m['market_id'],
            question=m['question'],
            outcomes=m.get('outcomes', ['Yes', 'No']),
            description=m.get('description', '')[:500],
            end_date=m.get('end_date')
        )
        for m in api_markets
    ]

    finder = DependencyFinder(
        db_path="data/phase2_dependencies.db",
        requests_per_minute=40  # Stay under rate limit
    )

    try:
        dependencies = await finder.analyze_all_pairs(
            market_infos,
            max_pairs=max_pairs
        )

        print(f"\n  Found {len(dependencies)} dependencies")

        # Show found dependencies
        if dependencies:
            print("\n  Detected dependencies:")
            for i, dep in enumerate(dependencies[:10]):
                # Find market questions
                m_a = next((m for m in api_markets if m['market_id'] == dep.market_a_id), None)
                m_b = next((m for m in api_markets if m['market_id'] == dep.market_b_id), None)

                q_a = m_a['question'][:40] if m_a else dep.market_a_id[:20]
                q_b = m_b['question'][:40] if m_b else dep.market_b_id[:20]

                print(f"\n    {i+1}. {dep.constraint_type.value.upper()}")
                print(f"       A: \"{q_a}...\" ({dep.condition_a})")
                print(f"       B: \"{q_b}...\" ({dep.condition_b})")
                print(f"       Confidence: {dep.confidence:.2f}")
                print(f"       Reason: {dep.explanation[:80]}...")

        # Validation step
        if validate and dependencies:
            print("\n[2b] Manual validation of dependencies...")
            print("  Enter 'y' to validate, 'n' to reject, 's' to skip:")

            for dep in dependencies[:20]:  # First 20
                m_a = next((m for m in api_markets if m['market_id'] == dep.market_a_id), None)
                m_b = next((m for m in api_markets if m['market_id'] == dep.market_b_id), None)

                print(f"\n  {dep.constraint_type.value}: ")
                print(f"    A: {m_a['question'] if m_a else dep.market_a_id}")
                print(f"    B: {m_b['question'] if m_b else dep.market_b_id}")
                print(f"    {dep.explanation}")

                response = input("  Validate? [y/n/s]: ").strip().lower()
                if response == 'y':
                    finder.db.mark_validated(dep.id, True)
                    print("    ✓ Validated")
                elif response == 'n':
                    finder.db.mark_validated(dep.id, False)
                    print("    ✗ Rejected")
                else:
                    print("    - Skipped")

        # Step 3: Build IP model and solve
        print(f"\n[3/4] Building IP model and solving for arbitrage...")

        # Convert markets to IP format
        ip_markets = markets_to_ip_format(api_markets)

        # Convert dependencies to constraints
        constraints = [
            convert_dependency_to_constraint(dep)
            for dep in dependencies
            if dep.constraint_type != DepConstraintType.NONE
        ]

        print(f"  Markets: {len(ip_markets)}")
        print(f"  Constraints: {len(constraints)}")

        # Get solver
        solver = get_solver(prefer_gurobi=True)

        # Solve
        result = solver.solve(
            ip_markets,
            constraints,
            arbitrage_threshold=0.98
        )

        print(f"\n  {result}")
        print(f"  Solve time: {result.solve_time*1000:.2f}ms")

        if result.has_arbitrage:
            print("\n  *** ARBITRAGE OPPORTUNITY DETECTED ***")
            print(f"  Minimum cost: ${result.min_cost:.4f}")
            print(f"  Guaranteed profit: ${result.profit:.4f} ({result.profit*100:.2f}%)")
            print("\n  Winning portfolio:")
            for (market_id, condition), value in result.positions.items():
                if value > 0.5:
                    m = next((m for m in api_markets if m['market_id'] == market_id), None)
                    if m:
                        print(f"    - {m['question'][:50]}...")
                        print(f"      Outcome: {condition}")
                        idx = m['outcomes'].index(condition) if condition in m['outcomes'] else 0
                        price = m['outcome_prices'][idx] if idx < len(m['outcome_prices']) else 0
                        print(f"      Price: ${price:.4f}")

        # Step 4: Summary
        print(f"\n[4/4] Summary")
        print("=" * 60)

        finder_stats = finder.get_stats()
        print(f"\nDependency Finder Stats:")
        print(f"  Pairs analyzed: {finder_stats['analyses_performed']}")
        print(f"  Dependencies found: {finder_stats['dependencies_found']}")
        print(f"  By type: {finder_stats['by_type']}")

        print(f"\nIP Solver Result:")
        print(f"  Status: {result.solver_status}")
        print(f"  Arbitrage found: {result.has_arbitrage}")
        print(f"  Min cost: ${result.min_cost:.4f}")
        print(f"  Profit: ${result.profit:.4f}")

        # Save results
        results = {
            "markets_analyzed": len(api_markets),
            "dependencies_found": len(dependencies),
            "arbitrage_detected": result.has_arbitrage,
            "min_cost": result.min_cost,
            "profit": result.profit,
            "positions": {f"{k[0]}:{k[1]}": v for k, v in result.positions.items()},
            "dependencies": [
                {
                    "type": d.constraint_type.value,
                    "market_a": d.market_a_id,
                    "condition_a": d.condition_a,
                    "market_b": d.market_b_id,
                    "condition_b": d.condition_b,
                    "confidence": d.confidence,
                    "explanation": d.explanation
                }
                for d in dependencies
            ]
        }

        results_path = Path("data/phase2_results.json")
        results_path.parent.mkdir(parents=True, exist_ok=True)
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {results_path}")

    finally:
        finder.close()
        if hasattr(solver, 'close'):
            solver.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Phase 2: Dependency Detection & IP Arbitrage"
    )
    parser.add_argument(
        '--markets', type=int, default=20,
        help='Number of markets to analyze (default: 20)'
    )
    parser.add_argument(
        '--pairs', type=int, default=50,
        help='Maximum market pairs to analyze (default: 50)'
    )
    parser.add_argument(
        '--validate', action='store_true',
        help='Interactively validate detected dependencies'
    )

    args = parser.parse_args()

    asyncio.run(run_phase2(
        num_markets=args.markets,
        max_pairs=args.pairs,
        validate=args.validate
    ))


if __name__ == "__main__":
    main()
