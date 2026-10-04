from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import psycopg2
from psycopg2 import sql

mcp = FastMCP("supabase-anomaly-checks")
load_dotenv()

def get_schema():
    """
    Returns ([(table_name, [(column_name, data_type), ...]), ...], is_mock) for the public schema.
    Structured form of list_tables(), so callers can render it or format it for the model.
    """
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    if not SUPABASE_DB_URL:
        return (
            [
                ("orders", [("id", "int"), ("user_id", "int"), ("amount", "numeric"), ("created_at", "timestamptz")]),
                ("users", [("id", "int"), ("email", "text"), ("signup_date", "date")]),
            ],
            True,
        )
    query = """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position;
    """
    with psycopg2.connect(SUPABASE_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
    tables = {}
    for table_name, column_name, data_type in rows:
        tables.setdefault(table_name, []).append((column_name, data_type))
    return list(tables.items()), False


def format_schema(tables, is_mock=False):
    text = "\n".join(f"{t}({', '.join(f'{c}: {dt}' for c, dt in cols)})" for t, cols in tables)
    if is_mock:
        text = "[MOCK DATA — set SUPABASE_DB_URL to query your real project]\n" + text
    return text


@mcp.tool
def list_tables():
    """
    List all data tables in datbase
    """
    return format_schema(*get_schema())

# print(list_tables())

@mcp.tool
def get_column_stats(col_name: str, table_name: str):
    """
    Gets key numerical statistics for given column in a table
    """
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    if not SUPABASE_DB_URL:
        return (
                "[MOCK DATA — set SUPABASE_DB_URL to query your real project]\n"
                f"Stats for {col_name} in {table_name}:\n"
                "count: 1000\n"
                "mean: 50.5\n"
                "stddev: 10.2\n"
                "min: 1\n"
                "max: 100"
            )
    query = sql.SQL("""
        SELECT COUNT({col}) AS count, AVG({col}) AS mean,
             STDDEV({col}) AS stddev, MIN({col}) AS min,
        MAX({col}) AS max, PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {col}) AS q1, PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {col}) AS q3 FROM {table};
        """).format(col=sql.Identifier(col_name), table=sql.Identifier(table_name))
    with psycopg2.connect(SUPABASE_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            count, mean, stddev, min_val, max_val, q1, q3 = cur.fetchone()
            return (
                f"Stats for {col_name} in {table_name}:\n"
                f"count: {count}\n"
                f"mean: {mean}\n"
                f"stddev: {stddev}\n"
                f"min: {min_val}\n"
                f"max: {max_val}\n"
                f"q1: {q1}\n"
                f"q3: {q3}"
            )

# print(get_column_stats("amount", "orders"))

@mcp.tool
def check_iqr_outlier(col_name: str, table_name: str):
    """
    Checks for outliers in a given column of a table using the IQR method
    """
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    if not SUPABASE_DB_URL:
        return (
                "[MOCK DATA — set SUPABASE_DB_URL to query your real project]\n"
                f"Outliers for {col_name} in {table_name}:\n"
                "Lower bound: 20\n"
                "Upper bound: 80\n"
                "Outliers: [5, 90, 100]"
            )
    query = sql.SQL("""
        WITH stats AS (
            SELECT
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {col}) AS q1,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {col}) AS q3
            FROM {table}
        )
        SELECT q1, q3 FROM stats;
    """).format(col=sql.Identifier(col_name), table=sql.Identifier(table_name))
    with psycopg2.connect(SUPABASE_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            q1, q3 = cur.fetchone()
            iqr = q3 - q1
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr
            outlier_query = sql.SQL("""
                SELECT {col} FROM {table}
                WHERE {col} < %s OR {col} > %s;
            """).format(col=sql.Identifier(col_name), table=sql.Identifier(table_name))
            cur.execute(outlier_query, (lower_bound, upper_bound))
            outliers = [row[0] for row in cur.fetchall()]
            return (
                f"Outliers for {col_name} in {table_name}:\n"
                f"Lower bound: {lower_bound}\n"
                f"Upper bound: {upper_bound}\n"
                f"Outliers: {outliers}"
            )

# print(check_iqr_outlier("amount", "orders"))

@mcp.tool
def check_zscore_outlier(col_name: str, table_name: str, threshold: float = 3.0):
    """
    Checks for outliers in a given column of a table using the Z-score method
    """
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    if not SUPABASE_DB_URL:
        return (
                "[MOCK DATA — set SUPABASE_DB_URL to query your real project]\n"
                f"Outliers for {col_name} in {table_name}:\n"
                f"Threshold: {threshold}\n"
                "Outliers: [5, 90, 100]"
            )
    query = sql.SQL("""
            SELECT AVG({col}) AS mean, STDDEV({col}) AS stddev FROM {table}
        """).format(col=sql.Identifier(col_name), table=sql.Identifier(table_name))
    with psycopg2.connect(SUPABASE_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            mean, stddev = cur.fetchone()
            if not stddev:
                return (
                    f"Cannot compute z-scores for {col_name} in {table_name}: "
                    "standard deviation is zero or undefined (no variance in the data)."
                )
            zscore_query = sql.SQL("""
                SELECT {col} FROM {table}
                WHERE ABS(({col} - %s) / %s) > %s;
            """).format(col=sql.Identifier(col_name), table=sql.Identifier(table_name))
            cur.execute(zscore_query, (mean, stddev, threshold))
            outliers = [row[0] for row in cur.fetchall()]
            return (
                f"Outliers for {col_name} in {table_name}:\n"
                f"Threshold: {threshold}\n"
                f"Outliers: {outliers}"
            )

# print(check_zscore_outlier("amount", "orders", 3.0))

@mcp.tool
def check_row_count_trend(table_name: str, date_col: str, window_days: int):
    """
    Checks for trends in row counts over time for a given table and date column
    """
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    if not SUPABASE_DB_URL:
        return (
                "[MOCK DATA — set SUPABASE_DB_URL to query your real project]\n"
                f"Row count trend for {table_name} based on {date_col}:\n"
                "2023-01-01: 100\n"
                "2023-01-02: 120\n"
                "2023-01-03: 90\n"
                "2023-01-04: 150\n"
            )
    query = sql.SQL("""
        SELECT DATE_TRUNC('day', {date_col}) AS day, COUNT(*) AS row_count
        FROM {table}
        WHERE {date_col} >= NOW() - INTERVAL %s
        GROUP BY day
        ORDER BY day;
    """).format(
        date_col=sql.Identifier(date_col),
        table=sql.Identifier(table_name),
    )
    with psycopg2.connect(SUPABASE_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (f"{window_days} days",))
            rows = cur.fetchall()
            trend = "\n".join(f"{row[0].date()}: {row[1]}" for row in rows)
            return (
                f"Row count trend for {table_name} based on {date_col}:\n{trend}"
            )

# print(check_row_count_trend("orders", "created_at", 7))

@mcp.tool
def freshness_check(table_name: str, date_col: str, freshness_threshold_days: int):
    """
    Checks if the data in a given table is fresh based on the latest date in a specified column
    """
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    if not SUPABASE_DB_URL:
        return (
                "[MOCK DATA — set SUPABASE_DB_URL to query your real project]\n"
                f"Freshness check for {table_name} based on {date_col}:\n"
                "Latest date: 2023-01-04\n"
                "Freshness threshold: 7 days\n"
                "Data is fresh."
            )
    query = sql.SQL("""
        SELECT MAX({date_col}) AS latest_date
        FROM {table};
    """).format(
        date_col=sql.Identifier(date_col),
        table=sql.Identifier(table_name),
    )
    with psycopg2.connect(SUPABASE_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT NOW();")
            current_time = cur.fetchone()[0]
            cur.execute(query)
            latest_date = cur.fetchone()[0]
            if not latest_date:
                return (
                    f"No data found in {table_name} for freshness check."
                )
            days_since_latest = (current_time - latest_date).days
            is_fresh = days_since_latest <= freshness_threshold_days
            return (
                f"Freshness check for {table_name} based on {date_col}:\n"
                f"Latest date: {latest_date.date()}\n"
                f"Freshness threshold: {freshness_threshold_days} days\n"
                f"Data is {'fresh' if is_fresh else 'stale'}."
            )

@mcp.tool
def check_recent_schema_changes(table: str, around_timestamp: str, window_hours: int = 36):
    """
    Checks for recent schema changes in a given table within a specified time window
    """
    query = sql.SQL("""
        SELECT event_time, command_tag, object_type, object_identity
        FROM schema_change_log
        WHERE object_identity LIKE %s
          AND event_time BETWEEN %s::timestamptz - INTERVAL %s
                              AND %s::timestamptz + INTERVAL %s
        ORDER BY event_time;
        """)
    params = (f"public.{table}%", around_timestamp, f"{window_hours} hours",
              around_timestamp, f"{window_hours} hours")

    conn = psycopg2.connect(os.environ.get("SUPABASE_DB_URL"))
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_rls_status():
    """
    Returns ([(table_name, rls_enabled), ...], is_mock) for the public schema.
    Structured form of check_rls(), so callers can render it or format it for the model.
    """
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    if not SUPABASE_DB_URL:
        return [("orders", False), ("users", True)], True
    query = """
        SELECT relname, relrowsecurity
        FROM pg_class
        WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace
        ORDER BY relname;
    """
    with psycopg2.connect(SUPABASE_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
    return [(table, bool(enabled)) for table, enabled in rows], False


def format_rls_status(rows, is_mock=False):
    if not rows:
        return "No tables found in public schema."
    lines = [f"{table}: RLS {'enabled' if enabled else 'disabled'}" for table, enabled in rows]
    missing = [table for table, enabled in rows if not enabled]
    summary = "\n".join(lines)
    if missing:
        summary += f"\n\nTables without RLS: {', '.join(missing)}"
    if is_mock:
        summary = "[MOCK DATA — set SUPABASE_DB_URL to query your real project]\n" + summary
    return summary


def check_rls():
    """
    Checks whether Row-Level Security (RLS) is enabled for each table in the public schema
    """
    return format_rls_status(*get_rls_status())


# if __name__ == "__main__":
#     # Example usage
#     print(list_tables())
#     print(get_column_stats("amount", "orders"))
#     print(check_iqr_outlier("amount", "orders"))
#     print(check_zscore_outlier("amount", "orders", 3.0))
#     print(check_row_count_trend("orders", "created_at", 7))
#     print(freshness_check("orders", "created_at", 7))
#     print(check_recent_schema_changes("orders", "2026-08-05T00:00:00Z"))