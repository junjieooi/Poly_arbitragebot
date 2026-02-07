"""
LLM Dependency Finder for Polymarket Markets.

Uses Claude API to analyze pairs of prediction markets and detect logical
dependencies between them:

1. Implication: If A is true, B must be true
   Example: "Trump wins PA" → "Republican wins PA"

2. Mutual Exclusion: A and B cannot both be true
   Example: "Duke wins championship" ∧ "Cornell wins championship"

The detected dependencies are used to build constraint matrices for the
integer programming arbitrage solver.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
    anthropic = None

# Configure logging
logger = logging.getLogger(__name__)


class ConstraintType(Enum):
    """Type of logical constraint between markets."""
    NONE = "none"
    IMPLICATION = "implication"  # A → B
    MUTUAL_EXCLUSION = "mutual_exclusion"  # ¬(A ∧ B)
    EQUIVALENCE = "equivalence"  # A ↔ B


@dataclass
class MarketInfo:
    """Information about a prediction market."""
    market_id: str
    question: str
    outcomes: List[str] = field(default_factory=lambda: ["Yes", "No"])
    condition_id: str = ""
    description: str = ""
    end_date: Optional[str] = None

    def to_prompt_text(self) -> str:
        """Format market info for LLM prompt."""
        text = f"Question: {self.question}\n"
        text += f"Outcomes: {', '.join(self.outcomes)}\n"
        if self.description:
            text += f"Description: {self.description[:500]}\n"
        if self.end_date:
            text += f"Resolution Date: {self.end_date}\n"
        return text


@dataclass
class Dependency:
    """Detected dependency between two markets."""
    id: Optional[int] = None
    market_a_id: str = ""
    market_b_id: str = ""
    constraint_type: ConstraintType = ConstraintType.NONE
    condition_a: str = ""  # e.g., "Yes" or "No"
    condition_b: str = ""
    valid_combinations: List[Tuple[str, str]] = field(default_factory=list)
    explanation: str = ""
    confidence: float = 0.0
    validated: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_constraint(self) -> Dict[str, Any]:
        """Convert to constraint format for IP solver."""
        return {
            "type": self.constraint_type.value,
            "cond_a": (self.market_a_id, self.condition_a),
            "cond_b": (self.market_b_id, self.condition_b),
            "valid_combinations": self.valid_combinations
        }


class RateLimiter:
    """Token bucket rate limiter for API calls."""

    def __init__(self, requests_per_minute: int = 50):
        """
        Initialize rate limiter.

        Args:
            requests_per_minute: Maximum requests per minute
        """
        self.requests_per_minute = requests_per_minute
        self.tokens = requests_per_minute
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request can be made."""
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update

            # Refill tokens based on elapsed time
            self.tokens = min(
                self.requests_per_minute,
                self.tokens + elapsed * (self.requests_per_minute / 60.0)
            )
            self.last_update = now

            if self.tokens < 1:
                # Wait for token to become available
                wait_time = (1 - self.tokens) * (60.0 / self.requests_per_minute)
                logger.debug(f"Rate limited, waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                self.tokens = 1

            self.tokens -= 1


class DependencyDatabase:
    """SQLite database for storing detected dependencies."""

    def __init__(self, db_path: str = "dependencies.db"):
        """Initialize database."""
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Create database schema."""
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row

        cursor = self._conn.cursor()

        # Dependencies table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dependencies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_a_id TEXT NOT NULL,
                market_b_id TEXT NOT NULL,
                constraint_type TEXT NOT NULL,
                condition_a TEXT NOT NULL,
                condition_b TEXT NOT NULL,
                valid_combinations TEXT NOT NULL,
                explanation TEXT,
                confidence REAL DEFAULT 0.0,
                validated INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(market_a_id, market_b_id, condition_a, condition_b)
            )
        """)

        # Markets cache table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                market_id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                outcomes TEXT NOT NULL,
                description TEXT,
                end_date TEXT,
                fetched_at TEXT NOT NULL
            )
        """)

        # Analysis log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_a_id TEXT NOT NULL,
                market_b_id TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                result TEXT,
                error TEXT
            )
        """)

        self._conn.commit()
        logger.info(f"Dependency database initialized: {self.db_path}")

    def save_dependency(self, dep: Dependency) -> int:
        """Save a dependency to the database."""
        cursor = self._conn.cursor()

        try:
            cursor.execute("""
                INSERT OR REPLACE INTO dependencies (
                    market_a_id, market_b_id, constraint_type,
                    condition_a, condition_b, valid_combinations,
                    explanation, confidence, validated, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dep.market_a_id,
                dep.market_b_id,
                dep.constraint_type.value,
                dep.condition_a,
                dep.condition_b,
                json.dumps(dep.valid_combinations),
                dep.explanation,
                dep.confidence,
                1 if dep.validated else 0,
                dep.created_at.isoformat()
            ))
            self._conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.warning(f"Dependency already exists: {dep.market_a_id} -> {dep.market_b_id}")
            return -1

    def get_dependencies(
        self,
        validated_only: bool = False,
        constraint_type: Optional[ConstraintType] = None
    ) -> List[Dependency]:
        """Get all dependencies from database."""
        cursor = self._conn.cursor()

        query = "SELECT * FROM dependencies WHERE 1=1"
        params = []

        if validated_only:
            query += " AND validated = 1"

        if constraint_type:
            query += " AND constraint_type = ?"
            params.append(constraint_type.value)

        cursor.execute(query, params)

        dependencies = []
        for row in cursor.fetchall():
            dep = Dependency(
                id=row["id"],
                market_a_id=row["market_a_id"],
                market_b_id=row["market_b_id"],
                constraint_type=ConstraintType(row["constraint_type"]),
                condition_a=row["condition_a"],
                condition_b=row["condition_b"],
                valid_combinations=json.loads(row["valid_combinations"]),
                explanation=row["explanation"],
                confidence=row["confidence"],
                validated=bool(row["validated"]),
                created_at=datetime.fromisoformat(row["created_at"])
            )
            dependencies.append(dep)

        return dependencies

    def mark_validated(self, dep_id: int, validated: bool = True) -> None:
        """Mark a dependency as validated."""
        cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE dependencies SET validated = ? WHERE id = ?",
            (1 if validated else 0, dep_id)
        )
        self._conn.commit()

    def log_analysis(
        self,
        market_a_id: str,
        market_b_id: str,
        result: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """Log an analysis attempt."""
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT INTO analysis_log (market_a_id, market_b_id, analyzed_at, result, error)
            VALUES (?, ?, ?, ?, ?)
        """, (
            market_a_id,
            market_b_id,
            datetime.utcnow().isoformat(),
            result,
            error
        ))
        self._conn.commit()

    def was_analyzed(self, market_a_id: str, market_b_id: str) -> bool:
        """Check if a pair was already analyzed."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM analysis_log
            WHERE (market_a_id = ? AND market_b_id = ?)
               OR (market_a_id = ? AND market_b_id = ?)
        """, (market_a_id, market_b_id, market_b_id, market_a_id))
        return cursor.fetchone()[0] > 0

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


class DependencyFinder:
    """
    Uses Claude API to find logical dependencies between prediction markets.

    The finder analyzes pairs of markets and detects:
    1. Implications: If market A resolves YES, market B must also resolve YES
    2. Mutual Exclusions: Markets A and B cannot both resolve YES

    These dependencies are used to build constraints for the IP arbitrage solver.
    """

    # Prompt template for Claude
    ANALYSIS_PROMPT = """Analyze these two prediction markets for logical dependencies:

Market A: {market_a_question}
{market_a_details}

Market B: {market_b_question}
{market_b_details}

Return ONLY valid JSON (no markdown, no explanation outside JSON):
{{
  "dependent": true or false,
  "constraint_type": "implication" | "mutual_exclusion" | "equivalence" | "none",
  "condition_a": "Yes" or "No" (which outcome of Market A is involved),
  "condition_b": "Yes" or "No" (which outcome of Market B is involved),
  "valid_combinations": [["Yes", "Yes"], ["Yes", "No"], ["No", "Yes"], ["No", "No"]],
  "confidence": 0.0 to 1.0,
  "explanation": "brief explanation of the dependency"
}}

Rules for constraint_type:
- "implication": If condition_a is true, condition_b must be true
  Example: "Trump wins Pennsylvania" (Yes) → "Republican wins Pennsylvania" (Yes)

- "mutual_exclusion": condition_a and condition_b cannot both be true
  Example: "Duke wins championship" (Yes) cannot coexist with "Cornell wins championship" (Yes)

- "equivalence": condition_a and condition_b are logically equivalent
  Example: "Bitcoin > $100k" (Yes) ↔ "BTC exceeds $100,000" (Yes)

- "none": No logical dependency exists

For valid_combinations:
- List ALL outcome pairs that are logically possible
- Format: [["A_outcome", "B_outcome"], ...]
- Must be exhaustive (cover all possible world states)

For confidence:
- 1.0: Absolute logical certainty (definitional relationship)
- 0.8-0.9: Very high confidence (clear causal relationship)
- 0.5-0.7: Moderate confidence (probable but not certain)
- <0.5: Low confidence (speculative)

Only return the JSON object, nothing else."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        db_path: str = "data/dependencies.db",
        requests_per_minute: int = 50
    ):
        """
        Initialize dependency finder.

        Args:
            api_key: Anthropic API key (or set ANTHROPIC_API_KEY env var)
            model: Claude model to use
            db_path: Path to SQLite database
            requests_per_minute: Rate limit for API calls
        """
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package required. Install with: pip install anthropic")

        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY required")

        self.model = model
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.rate_limiter = RateLimiter(requests_per_minute)

        # Create data directory
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = DependencyDatabase(db_path)

        # Statistics
        self._analyses_performed = 0
        self._dependencies_found = 0
        self._errors = 0

    async def analyze_pair(
        self,
        market_a: MarketInfo,
        market_b: MarketInfo,
        skip_if_analyzed: bool = True
    ) -> Optional[Dependency]:
        """
        Analyze a pair of markets for dependencies.

        Args:
            market_a: First market
            market_b: Second market
            skip_if_analyzed: Skip if already analyzed

        Returns:
            Dependency if found, None otherwise
        """
        # Check if already analyzed
        if skip_if_analyzed and self.db.was_analyzed(market_a.market_id, market_b.market_id):
            logger.debug(f"Skipping already analyzed pair: {market_a.market_id}, {market_b.market_id}")
            return None

        # Rate limit
        await self.rate_limiter.acquire()

        # Build prompt
        prompt = self.ANALYSIS_PROMPT.format(
            market_a_question=market_a.question,
            market_a_details=market_a.to_prompt_text(),
            market_b_question=market_b.question,
            market_b_details=market_b.to_prompt_text()
        )

        try:
            # Call Claude API
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse response
            response_text = response.content[0].text.strip()

            # Try to extract JSON from response
            try:
                # Handle potential markdown code blocks
                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0]
                elif "```" in response_text:
                    response_text = response_text.split("```")[1].split("```")[0]

                result = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON response: {e}")
                logger.debug(f"Response was: {response_text}")
                self.db.log_analysis(
                    market_a.market_id,
                    market_b.market_id,
                    error=f"JSON parse error: {e}"
                )
                self._errors += 1
                return None

            self._analyses_performed += 1

            # Check if dependency found
            if not result.get("dependent", False):
                self.db.log_analysis(
                    market_a.market_id,
                    market_b.market_id,
                    result="no_dependency"
                )
                return None

            # Create dependency object
            constraint_type_str = result.get("constraint_type", "none")
            try:
                constraint_type = ConstraintType(constraint_type_str)
            except ValueError:
                constraint_type = ConstraintType.NONE

            if constraint_type == ConstraintType.NONE:
                self.db.log_analysis(
                    market_a.market_id,
                    market_b.market_id,
                    result="no_dependency"
                )
                return None

            dep = Dependency(
                market_a_id=market_a.market_id,
                market_b_id=market_b.market_id,
                constraint_type=constraint_type,
                condition_a=result.get("condition_a", "Yes"),
                condition_b=result.get("condition_b", "Yes"),
                valid_combinations=result.get("valid_combinations", []),
                explanation=result.get("explanation", ""),
                confidence=float(result.get("confidence", 0.5)),
                validated=False
            )

            # Save to database
            dep.id = self.db.save_dependency(dep)
            self._dependencies_found += 1

            self.db.log_analysis(
                market_a.market_id,
                market_b.market_id,
                result=f"found_{constraint_type.value}"
            )

            logger.info(
                f"Found {constraint_type.value}: {market_a.question[:30]}... "
                f"-> {market_b.question[:30]}... (conf: {dep.confidence:.2f})"
            )

            return dep

        except anthropic.APIError as e:
            logger.error(f"API error analyzing pair: {e}")
            self.db.log_analysis(
                market_a.market_id,
                market_b.market_id,
                error=f"API error: {e}"
            )
            self._errors += 1

            # Handle rate limit errors with backoff
            if "rate" in str(e).lower():
                await asyncio.sleep(60)

            return None

        except Exception as e:
            logger.error(f"Error analyzing pair: {e}")
            self.db.log_analysis(
                market_a.market_id,
                market_b.market_id,
                error=str(e)
            )
            self._errors += 1
            return None

    async def analyze_all_pairs(
        self,
        markets: List[MarketInfo],
        max_pairs: Optional[int] = None
    ) -> List[Dependency]:
        """
        Analyze all pairs of markets for dependencies.

        Args:
            markets: List of markets to analyze
            max_pairs: Maximum number of pairs to analyze

        Returns:
            List of detected dependencies
        """
        dependencies = []
        pairs_analyzed = 0

        # Generate all pairs
        total_pairs = len(markets) * (len(markets) - 1) // 2
        logger.info(f"Analyzing up to {total_pairs} market pairs...")

        for i, market_a in enumerate(markets):
            for market_b in markets[i + 1:]:
                if max_pairs and pairs_analyzed >= max_pairs:
                    break

                dep = await self.analyze_pair(market_a, market_b)
                if dep:
                    dependencies.append(dep)

                pairs_analyzed += 1

                # Progress logging
                if pairs_analyzed % 10 == 0:
                    logger.info(
                        f"Progress: {pairs_analyzed}/{total_pairs} pairs, "
                        f"{len(dependencies)} dependencies found"
                    )

            if max_pairs and pairs_analyzed >= max_pairs:
                break

        logger.info(
            f"Analysis complete: {pairs_analyzed} pairs analyzed, "
            f"{len(dependencies)} dependencies found"
        )

        return dependencies

    def validate_dependency(self, dep: Dependency) -> bool:
        """
        Validate a dependency's logical consistency.

        Checks that valid_combinations cover all possible outcomes
        and are consistent with the constraint type.

        Args:
            dep: Dependency to validate

        Returns:
            True if valid, False otherwise
        """
        # Check valid combinations cover necessary cases
        combos = set(tuple(c) for c in dep.valid_combinations)

        if dep.constraint_type == ConstraintType.IMPLICATION:
            # If A is true, B must be true
            # Invalid: (condition_a=True, condition_b=False)
            invalid_combo = (dep.condition_a, "No" if dep.condition_b == "Yes" else "Yes")
            if invalid_combo in combos:
                logger.warning(f"Invalid implication: {invalid_combo} should not be valid")
                return False

        elif dep.constraint_type == ConstraintType.MUTUAL_EXCLUSION:
            # A and B cannot both be true
            # Invalid: (condition_a=True, condition_b=True)
            invalid_combo = (dep.condition_a, dep.condition_b)
            if invalid_combo in combos:
                logger.warning(f"Invalid mutual exclusion: {invalid_combo} should not be valid")
                return False

        elif dep.constraint_type == ConstraintType.EQUIVALENCE:
            # A and B must have same truth value
            # Valid only: (True, True) and (False, False)
            valid = {
                (dep.condition_a, dep.condition_b),
                ("No" if dep.condition_a == "Yes" else "Yes",
                 "No" if dep.condition_b == "Yes" else "Yes")
            }
            if combos != valid:
                logger.warning(f"Invalid equivalence: expected {valid}, got {combos}")
                return False

        return True

    def get_validated_dependencies(self) -> List[Dependency]:
        """Get all validated dependencies."""
        return self.db.get_dependencies(validated_only=True)

    def get_all_dependencies(self) -> List[Dependency]:
        """Get all dependencies."""
        return self.db.get_dependencies(validated_only=False)

    def get_stats(self) -> Dict[str, Any]:
        """Get finder statistics."""
        deps = self.db.get_dependencies()
        return {
            "analyses_performed": self._analyses_performed,
            "dependencies_found": self._dependencies_found,
            "errors": self._errors,
            "total_in_db": len(deps),
            "validated": sum(1 for d in deps if d.validated),
            "by_type": {
                t.value: sum(1 for d in deps if d.constraint_type == t)
                for t in ConstraintType
            }
        }

    def close(self) -> None:
        """Close resources."""
        self.db.close()


# CLI for manual testing
async def main():
    """Test the dependency finder."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    # Example markets for testing
    test_markets = [
        MarketInfo(
            market_id="m1",
            question="Will Trump win Pennsylvania in 2024?",
            outcomes=["Yes", "No"],
            description="This market resolves Yes if Trump wins PA electoral votes."
        ),
        MarketInfo(
            market_id="m2",
            question="Will Republicans win Pennsylvania in 2024?",
            outcomes=["Yes", "No"],
            description="This market resolves Yes if Republican candidate wins PA."
        ),
        MarketInfo(
            market_id="m3",
            question="Will Duke win the NCAA basketball championship?",
            outcomes=["Yes", "No"],
            description="Resolves Yes if Duke wins March Madness."
        ),
        MarketInfo(
            market_id="m4",
            question="Will UNC win the NCAA basketball championship?",
            outcomes=["Yes", "No"],
            description="Resolves Yes if UNC wins March Madness."
        ),
    ]

    finder = DependencyFinder(db_path="test_dependencies.db")

    try:
        deps = await finder.analyze_all_pairs(test_markets)

        print(f"\nFound {len(deps)} dependencies:")
        for dep in deps:
            print(f"\n  {dep.constraint_type.value}: {dep.market_a_id} -> {dep.market_b_id}")
            print(f"  Conditions: {dep.condition_a} -> {dep.condition_b}")
            print(f"  Confidence: {dep.confidence:.2f}")
            print(f"  Explanation: {dep.explanation}")

        print(f"\nStats: {finder.get_stats()}")

    finally:
        finder.close()


if __name__ == "__main__":
    asyncio.run(main())
