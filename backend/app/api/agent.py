from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import psycopg2
from psycopg2 import sql
from langchain.agents import create_agent

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
                "[MOCK DATA — set SUPABASE_DB_URL to query your real project]\n"
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

print(list_tables())

@mcp.tool
def get_column_stats(col_name: str, table_name: str):
    """
    Gets key numerical stat
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
        MAX({col}) AS max FROM {table};
        """).format(col=sql.Identifier(col_name), table=sql.Identifier(table_name))
    with psycopg2.connect(SUPABASE_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            count, mean, stddev, min_val, max_val = cur.fetchone()
            return (
                f"Stats for {col_name} in {table_name}:\n"
                f"count: {count}\n"
                f"mean: {mean}\n"
                f"stddev: {stddev}\n"
                f"min: {min_val}\n"
                f"max: {max_val}"
            )

print(get_column_stats("amount", "orders"))