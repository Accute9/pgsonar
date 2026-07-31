from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import psycopg2
from psycopg2 import sql

mcp = FastMCP("supabase-anomaly-checks")
load_dotenv()

@mcp.tool
def list_tables():
    """
    List all data tables in datbase
    """
    SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")
    if not SUPABASE_DB_URL:
        return (
                "[MOCK DATA — set SUPABASE_DB_URL to query your real pr oject]\n"
                "orders(id: int, user_id: int, amount: numeric, created_at: timestamptz)\n"
                "users(id: int, email: text, signup_date: date)"
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
        tables.setdefault(table_name, []).append(f"{column_name}: {data_type}")
 
    return "\n".join(f"{t}({', '.join(cols)})" for t, cols in tables.items())

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
                f"max: {max_val}\n",
                f"q1: {q1}\n",
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

