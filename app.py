"""
Text-to-SQL Business Analytics Copilot (Streamlit Web Application).

Translates plain-English business questions into validated, read-only SQLite
queries via Gemini 2.5 Flash, executes them safely in read-only URI mode, and
renders summary KPIs, interactive Plotly charts, and downloadable data tables.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from db_setup import DEFAULT_DB_PATH, init_db
from sql_agent import (
    MODEL_NAME,
    SQLQueryPlan,
    SQLSafetyError,
    execute_readonly_query,
    get_database_schema,
    generate_sql_plan,
)

SAMPLE_QUESTIONS: list[str] = [
    "Which product category generated the highest net revenue?",
    "Show monthly revenue trends over the past year",
    "Top 5 customers by total spend",
    "Which region has the highest return rate?",
    "Compare sales rep performance by total completed orders",
]

st.set_page_config(
    page_title="Text-to-SQL Analytics Copilot",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _load_local_env_key(env_var: str = "GEMINI_API_KEY") -> str:
    """
    Resolve GEMINI_API_KEY from environment variables, Streamlit secrets,
    or a local `.env` file (without requiring extra dependencies).
    """
    if value := os.getenv(env_var):
        return value.strip()

    try:
        if env_var in st.secrets:
            return str(st.secrets[env_var]).strip()
    except Exception:
        pass

    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            if key.strip() == env_var:
                cleaned = val.strip().strip('"').strip("'")
                if cleaned and cleaned != "your_api_key_here":
                    return cleaned
    return ""


@st.cache_resource(show_spinner="Initializing retail analytics database...")
def ensure_database() -> Path:
    """Ensure `retail_analytics.db` exists and is seeded on startup."""
    return init_db(DEFAULT_DB_PATH)


@st.cache_data(show_spinner=False)
def load_schema_metadata(db_path_str: str) -> dict:
    """Cache live PRAGMA table_info metadata for the sidebar schema viewer."""
    return get_database_schema(db_path_str)


def _format_metric_value(value: float, col_name: str) -> str:
    """Format numeric KPI values cleanly based on column naming hints."""
    lower = col_name.lower()
    if any(token in lower for token in ("rate", "pct", "percent", "margin", "ratio")):
        return f"{value:,.2f}%"
    if any(token in lower for token in ("rev", "spend", "sales", "total", "price", "cost", "amount", "quota", "profit", "aov")):
        return f"₱{value:,.2f}"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _resolve_axes(
    df: pd.DataFrame,
    suggested_x: Optional[str],
    suggested_y: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """
    Match Gemini's suggested X/Y columns against the actual DataFrame columns,
    falling back gracefully to detected categorical and numeric columns.
    """
    cols = list(df.columns)
    lower_map = {c.lower(): c for c in cols}

    x_col = None
    if suggested_x:
        x_col = suggested_x if suggested_x in cols else lower_map.get(suggested_x.lower())

    y_col = None
    if suggested_y:
        y_col = suggested_y if suggested_y in cols else lower_map.get(suggested_y.lower())

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    non_numeric_cols = [c for c in cols if c not in numeric_cols]

    if y_col is None or y_col not in numeric_cols:
        y_col = numeric_cols[0] if numeric_cols else None

    if x_col is None or x_col == y_col:
        if non_numeric_cols:
            x_col = non_numeric_cols[0]
        else:
            other_cols = [c for c in cols if c != y_col]
            x_col = other_cols[0] if other_cols else None

    return x_col, y_col


def build_plotly_chart(df: pd.DataFrame, plan: SQLQueryPlan) -> Optional[go.Figure]:
    """Build an interactive Plotly figure matching `plan.recommended_chart`."""
    if df.empty or plan.recommended_chart == "table" or len(df) < 2:
        return None

    x_col, y_col = _resolve_axes(df, plan.x_col, plan.y_col)
    if not x_col or not y_col:
        return None

    x_label = x_col.replace("_", " ").title()
    y_label = y_col.replace("_", " ").title()
    chart_type = plan.recommended_chart

    if chart_type == "line":
        fig = px.line(
            df,
            x=x_col,
            y=y_col,
            markers=True,
            title=f"{y_label} by {x_label}",
            labels={x_col: x_label, y_col: y_label},
        )
        fig.update_traces(line=dict(width=3))
    elif chart_type == "pie":
        fig = px.pie(
            df,
            names=x_col,
            values=y_col,
            hole=0.4,
            title=f"{y_label} Share by {x_label}",
        )
        fig.update_traces(textposition="inside", textinfo="percent+label")
    else:
        fig = px.bar(
            df,
            x=x_col,
            y=y_col,
            color=x_col if df[x_col].nunique() <= 10 else None,
            text_auto=".2s",
            title=f"{y_label} by {x_label}",
            labels={x_col: x_label, y_col: y_label},
        )
        fig.update_layout(showlegend=False)

    fig.update_layout(
        template="plotly_white",
        margin=dict(l=20, r=20, t=55, b=20),
        height=420,
        hovermode="x unified" if chart_type == "line" else "closest",
    )
    return fig


def render_summary_metrics(df: pd.DataFrame, plan: SQLQueryPlan) -> None:
    """Render a row of key summary metrics (`st.metric`) when numeric data is returned."""
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if df.empty or not numeric_cols:
        return

    x_col, y_col = _resolve_axes(df, plan.x_col, plan.y_col)
    primary_metric = y_col if y_col in numeric_cols else numeric_cols[0]
    metric_label = primary_metric.replace("_", " ").title()

    series = df[primary_metric].dropna()
    if series.empty:
        return

    is_rate = any(
        token in primary_metric.lower()
        for token in ("rate", "pct", "percent", "avg", "average", "ratio", "margin")
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Rows Returned", f"{len(df):,}")

    if is_rate:
        col2.metric(f"Average {metric_label}", _format_metric_value(float(series.mean()), primary_metric))
    else:
        col2.metric(f"Total {metric_label}", _format_metric_value(float(series.sum()), primary_metric))

    col3.metric(f"Mean {metric_label}", _format_metric_value(float(series.mean()), primary_metric))

    max_idx = series.idxmax()
    top_val = _format_metric_value(float(series.loc[max_idx]), primary_metric)
    top_label = str(df.loc[max_idx, x_col]) if x_col and x_col in df.columns else None
    col4.metric(
        f"Highest {metric_label}",
        top_val,
        delta=top_label if top_label and len(df) > 1 else None,
        delta_color="off",
    )


def render_sidebar(db_path: Path) -> tuple[str, Optional[str]]:
    """Render sidebar controls: API key, sample questions, and live schema viewer."""
    selected_sample: Optional[str] = None

    with st.sidebar:
        st.title("📊 Analytics Copilot")
        st.caption(f"Powered by `{MODEL_NAME}` • Read-Only SQLite")

        default_key = _load_local_env_key("GEMINI_API_KEY")
        api_key = st.text_input(
            "Gemini API Key",
            value=st.session_state.get("gemini_api_key", default_key),
            type="password",
            placeholder="AIza...",
            help="Loaded automatically from GEMINI_API_KEY env var if set, or paste your own key here.",
        )
        st.session_state["gemini_api_key"] = api_key

        if api_key:
            st.success("API key configured", icon="✅")
        else:
            st.warning("Enter your Gemini API key to run queries.", icon="🔑")

        st.divider()
        st.subheader("Sample Business Questions")
        st.caption("Click any prompt below to run it immediately:")
        for idx, sample in enumerate(SAMPLE_QUESTIONS):
            if st.button(sample, key=f"sample_q_{idx}", use_container_width=True):
                selected_sample = sample

        st.divider()
        with st.expander("Live Database Schema (`PRAGMA table_info`)", expanded=False):
            schema = load_schema_metadata(str(db_path))
            for table_name, info in schema.items():
                st.markdown(f"**`{table_name}`** — `{info['row_count']:,}` rows")
                cols_df = pd.DataFrame(
                    [
                        {
                            "Column": c["name"],
                            "Type": c["type"],
                            "Key": "PK" if c["pk"] else "",
                        }
                        for c in info["columns"]
                    ]
                )
                st.dataframe(cols_df, hide_index=True, use_container_width=True)

        st.divider()
        st.caption(
            "🛡️ **Safety Guardrails Active**: Regex validation blocks non-SELECT/WITH "
            "statements, DDL/DML keywords, comments, and multi-statements. SQLite opens "
            "in strict `?mode=ro` read-only URI mode."
        )

    return api_key, selected_sample


def run_analysis(question: str, api_key: str, db_path: Path) -> None:
    """Execute the full Text-to-SQL pipeline and store results in session state."""
    if not api_key or not api_key.strip():
        st.error("Please provide a valid Gemini API key in the sidebar before running an analysis.")
        return

    if not question or not question.strip():
        st.warning("Please enter a business question first.")
        return

    with st.spinner("Generating read-only SQL query and analyzing results..."):
        try:
            plan = generate_sql_plan(question=question, api_key=api_key, db_path=db_path)
            df = execute_readonly_query(sql=plan.sql_query, db_path=db_path)
            st.session_state["last_result"] = {
                "question": question.strip(),
                "plan": plan,
                "df": df,
                "error": None,
            }
        except SQLSafetyError as exc:
            st.session_state["last_result"] = {
                "question": question.strip(),
                "plan": None,
                "df": None,
                "error": f"🛡️ Safety Guardrail Blocked Query: {exc}",
            }
        except sqlite3.Error as exc:
            st.session_state["last_result"] = {
                "question": question.strip(),
                "plan": None,
                "df": None,
                "error": f"SQLite Execution Error: {exc}",
            }
        except Exception as exc:
            st.session_state["last_result"] = {
                "question": question.strip(),
                "plan": None,
                "df": None,
                "error": f"API or Query Generation Error: {exc}",
            }


def main() -> None:
    db_path = ensure_database()
    api_key, selected_sample = render_sidebar(db_path)

    # Header
    st.title("Text-to-SQL Business Analytics Copilot")
    st.markdown(
        "Ask plain-English questions about your retail sales, customers, regions, and sales reps. "
        "Every query is translated into transparent, read-only SQL and visualized automatically."
    )

    if selected_sample:
        st.session_state["question_input"] = selected_sample

    with st.form("query_form", clear_on_submit=False):
        question = st.text_input(
            "Ask a business question",
            key="question_input",
            placeholder="e.g., Which product category generated the highest net revenue?",
        )
        submitted = st.form_submit_button("Run Analysis", type="primary")

    if selected_sample:
        run_analysis(selected_sample, api_key, db_path)
    elif submitted:
        run_analysis(question, api_key, db_path)

    # Render latest analysis result
    result = st.session_state.get("last_result")
    if not result:
        st.info(
            "👆 Type a question above or click one of the **Sample Business Questions** in the sidebar to begin."
        )
        return

    if result["error"]:
        st.error(result["error"])
        return

    plan: SQLQueryPlan = result["plan"]
    df: pd.DataFrame = result["df"]

    st.success(f"**Insight Logic:** {plan.explanation}", icon="💡")

    with st.expander("Generated SQL & Logic", expanded=False):
        st.code(plan.sql_query, language="sql")
        meta_cols = st.columns(3)
        meta_cols[0].caption(f"**Chart Type:** `{plan.recommended_chart}`")
        meta_cols[1].caption(f"**X Column:** `{plan.x_col or 'N/A'}`")
        meta_cols[2].caption(f"**Y Column:** `{plan.y_col or 'N/A'}`")

    if df.empty:
        st.warning("The query executed safely, but returned 0 rows. Try broadening your question.")
        return

    render_summary_metrics(df, plan)
    st.divider()

    fig = build_plotly_chart(df, plan)
    if fig is not None:
        chart_col, table_col = st.columns([1.35, 1.0], gap="large")
        with chart_col:
            st.subheader("Interactive Visualization")
            st.plotly_chart(fig, use_container_width=True)
        with table_col:
            st.subheader("Query Results")
            st.dataframe(df, use_container_width=True, hide_index=True, height=370)
            st.download_button(
                label="Download CSV",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name="analytics_results.csv",
                mime="text/csv",
                use_container_width=True,
            )
    else:
        st.subheader("Query Results")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            label="Download CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="analytics_results.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
