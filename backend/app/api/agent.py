import asyncio
from fastmcp import FastMCP
from langchain.agents import create_agent

mcp = FastMCP("supabase-anomaly-checks")

@mcp.tool
def list_tables():
    url = "https://your-supabase-url.supabase.co/rest/v1/tables"