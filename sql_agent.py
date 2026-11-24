"""
Text-to-SQL AI agent, schema extractor, and read-only safety guardrail module.

Uses the official `google-genai` SDK with `gemini-2.5-flash` and Pydantic
structured outputs to translate plain-English business questions into validated,
read-only SQLite queries.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Literal, Optional

import pandas as pd
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from db_setup import DEFAULT_DB_PATH

MODEL_NAME = "gemini-3.8-flash"

# Keywords and SQL syntax patterns forbidden in read-only analytics queries.
FORBIDDEN_KEYWORD_PATTERN = re.compile(
    r"\b("
    r"DROP|DELETE|UPDATE|INSERT|ALTER|ATTACH|DETACH|PRAGMA|"
    r"EXEC|EXECUTE|CREATE|REPLACE|TRUNCATE|VACUUM|REINDEX|"
    r"GRANT|REVOKE|LOAD_EXTENSION"
    r")\b",
    re.IGNORECASE,
)

FORBIDDEN_COMMENT_OR_MULTI_PATTERN = re.compile(r"(--|/\*|\*/|;)")


class SQLSafetyError(ValueError):
    """Raised when a generated or supplied SQL query violates read-only guardrails."""


class SQLQueryPlan(BaseModel):
    """Structured output schema returned by Gemini for every analytics question."""

    sql_query: str = Field(
        description="A single valid, read-only SQLite SELECT (or WITH ... SELECT) query matching the exact schema."
    )
    explanation: str = Field(
        description="A concise 1-sentence plain-English explanation of how the SQL query answers the user's question."
    )
    recommended_chart: Literal["bar", "line", "pie", "table"] = Field(
        description="Best visualization type for the returned result set: 'bar', 'line', 'pie', or 'table'."
    )
    x_col: Optional[str] = Field(
        default=None,
        description="Exact output column name from the SELECT clause to use for the X-axis / category labels, if applicable.",
    )
    y_col: Optional[str] = Field(
        default=None,
        description="Exact numeric output column name from the SELECT clause to use for the Y-axis / values, if applicable.",
    )


def _open_readonly_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open SQLite in strict read-only URI mode as a second layer of defense."""
    resolved = Path(db_path).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"SQLite database not found at: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10.0)


def validate_sql_query(sql: str) -> str:
    """
    Validate that a SQL query is strictly read-only and safe to execute.

    Guardrails enforced:
    1. Must be non-empty and start with SELECT or WITH.
    2. Strips an optional single trailing semicolon, then blocks any remaining
       semicolons (multi-statement injection) and SQL comments (`--`, `/* */`).
    3. Blocks all mutating, DDL, or administrative SQLite keywords.
    """
    if not sql or not sql.strip():
        raise SQLSafetyError("Generated SQL query is empty.")

    cleaned = sql.strip()

    # Remove markdown code fences if a model ever wraps the SQL unexpectedly
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # Allow a single harmless trailing semicolon, but reject any internal semicolons
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()

    if not re.match(r"^(SELECT|WITH)\b", cleaned, re.IGNORECASE):
        raise SQLSafetyError(
            "Only read-only SELECT or WITH queries are permitted."
        )

    comment_or_multi_match = FORBIDDEN_COMMENT_OR_MULTI_PATTERN.search(cleaned)
    if comment_or_multi_match:
        token = comment_or_multi_match.group(1)
        raise SQLSafetyError(
            f"Blocked unsafe SQL token '{token}' (comments and multi-statement queries are prohibited)."
        )

    keyword_match = FORBIDDEN_KEYWORD_PATTERN.search(cleaned)
    if keyword_match:
        keyword = keyword_match.group(1).upper()
        raise SQLSafetyError(
            f"Blocked prohibited SQL keyword '{keyword}'. Only read-only analytics queries are allowed."
        )

    return cleaned


def get_database_schema(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, dict[str, Any]]:
    """
    Inspect the SQLite database using `PRAGMA table_info` and `PRAGMA foreign_key_list`.

    Returns:
        Dictionary keyed by table name containing columns, foreign keys, and row counts.
    """
    schema: dict[str, dict[str, Any]] = {}
    with _open_readonly_connection(db_path) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name;
                """
            ).fetchall()
        ]
        for table in tables:
            cols = conn.execute(f"PRAGMA table_info('{table}');").fetchall()
            fks = conn.execute(f"PRAGMA foreign_key_list('{table}');").fetchall()
            row_count = conn.execute(f"SELECT COUNT(*) FROM '{table}';").fetchone()[0]
            schema[table] = {
                "row_count": row_count,
                "columns": [
                    {
                        "name": col[1],
                        "type": col[2],
                        "notnull": bool(col[3]),
                        "pk": bool(col[5]),
                    }
                    for col in cols
                ],
                "foreign_keys": [
                    {
                        "from_col": fk[3],
                        "ref_table": fk[2],
                        "ref_col": fk[4],
                    }
                    for fk in fks
                ],
            }
    return schema


def format_schema_for_prompt(db_path: Path | str = DEFAULT_DB_PATH) -> str:
    """Build a compact, high-signal schema description for the LLM prompt."""
    schema = get_database_schema(db_path)
    lines: list[str] = []

    for table, info in schema.items():
        lines.append(f"Table: {table} ({info['row_count']} rows)")
        for col in info["columns"]:
            pk_tag = " [PK]" if col["pk"] else ""
            lines.append(f"  - {col['name']} ({col['type']}){pk_tag}")
        for fk in info["foreign_keys"]:
            lines.append(f"  - FK: {fk['from_col']} -> {fk['ref_table']}.{fk['ref_col']}")

    # Include exact categorical domain values so the LLM never guesses casing
    with _open_readonly_connection(db_path) as conn:
        regions = [r[0] for r in conn.execute("SELECT DISTINCT region FROM orders ORDER BY region;")]
        categories = [r[0] for r in conn.execute("SELECT DISTINCT category FROM products ORDER BY category;")]
        statuses = [r[0] for r in conn.execute("SELECT DISTINCT status FROM orders ORDER BY status;")]
        segments = [r[0] for r in conn.execute("SELECT DISTINCT segment FROM customers ORDER BY segment;")]
        date_bounds = conn.execute("SELECT MIN(order_date), MAX(order_date) FROM orders;").fetchone()

    lines.extend(
        [
            "",
            "Domain Reference Values:",
            f"  - orders.region: {regions}",
            f"  - orders.status: {statuses}",
            f"  - products.category: {categories}",
            f"  - customers.segment: {segments}",
            f"  - orders.order_date range: {date_bounds[0]} to {date_bounds[1]} (ISO YYYY-MM-DD)",
            "",
            "Business Definitions:",
            "  - Gross revenue = order_details.quantity * order_details.unit_price",
            "  - Net revenue = order_details.line_total (already equals quantity * unit_price * (1 - discount_pct))",
            "  - Unless the user asks about all statuses or returns/pending orders, exclude Returned orders (or filter status = 'Completed') when computing realized revenue.",
            "  - Return rate (%) = ROUND(100.0 * SUM(CASE WHEN status = 'Returned' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2)",
        ]
    )
    return "\n".join(lines)


def generate_sql_plan(
    question: str,
    api_key: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> SQLQueryPlan:
    """
    Translate a plain-English analytics question into a validated `SQLQueryPlan`
    using Gemini 2.5 Flash structured outputs.
    """
    if not api_key or not api_key.strip():
        raise ValueError("A valid Gemini API key is required.")
    if not question or not question.strip():
        raise ValueError("Please enter a business question to analyze.")

    schema_context = format_schema_for_prompt(db_path)

    system_instruction = (
        "You are a senior analytics engineer and Text-to-SQL copilot for a retail business. "
        "Translate the user's business question into a single, accurate, read-only SQLite query.\n\n"
        "Strict Rules:\n"
        "1. Dialect is SQLite. Use strftime('%Y-%m', order_date) for monthly grouping, ROUND(..., 2) for currency/percentages, and NULLIF(..., 0) to prevent division by zero.\n"
        "2. Output ONLY a single SELECT or WITH ... SELECT statement. Never include comments (-- or /* */), semicolons inside the query, or any write/DDL keywords.\n"
        "3. Use clear, readable snake_case column aliases (e.g., net_revenue, completed_orders, return_rate_pct).\n"
        "4. Set x_col and y_col to the EXACT column alias names returned by your SELECT statement.\n"
        "5. Choose recommended_chart thoughtfully:\n"
        "   - 'line' for time-series trends (e.g., monthly revenue)\n"
        "   - 'bar' for categorical comparisons or top-N rankings\n"
        "   - 'pie' for part-to-whole share breakdowns with <= 6 categories\n"
        "   - 'table' for detailed multi-column listings or single-row summaries\n\n"
        f"Database Schema:\n{schema_context}"
    )

    client = genai.Client(api_key=api_key.strip())
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=question.strip(),
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=SQLQueryPlan,
            temperature=0.0,
        ),
    )

    if response.parsed is not None:
        plan = (
            response.parsed
            if isinstance(response.parsed, SQLQueryPlan)
            else SQLQueryPlan.model_validate(response.parsed)
        )
    elif response.text:
        plan = SQLQueryPlan.model_validate_json(response.text)
    else:
        raise RuntimeError("Gemini returned an empty response. Please try rephrasing your question.")

    # Enforce strict read-only safety validation before returning the plan
    plan.sql_query = validate_sql_query(plan.sql_query)
    return plan


def execute_readonly_query(
    sql: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> pd.DataFrame:
    """
    Validate and execute a read-only SQL query against the SQLite database.

    Uses both regex validation and SQLite read-only URI mode (`mode=ro`).
    """
    safe_sql = validate_sql_query(sql)
    with _open_readonly_connection(db_path) as conn:
        return pd.read_sql_query(safe_sql, conn)
