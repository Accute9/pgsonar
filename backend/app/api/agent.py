from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import psycopg2
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
