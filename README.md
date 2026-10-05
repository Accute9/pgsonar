# pgsonar

pgsonar is an AI data-quality agent for Postgres. It connects to a Postgres database (such as a Supabase project), investigates tables for anomalies, checks Row-Level Security coverage, and streams its reasoning and findings to a web UI as it works.

## How it works

1. **RLS pre-check.** Before the model runs, the backend checks which public tables have Row-Level Security enabled. This runs deterministically, so it cannot be skipped.
2. **Planner.** A Gemini call reads the task and the tool catalog, then writes a short, ordered investigation strategy (at most six bullets).
3. **Agent loop.** A second Gemini call acts on the plan, calling MCP tools against the database. Tool calls and results are streamed to the browser as they happen.
4. **Report.** When the model stops calling tools, its plain-English summary is sent as the final report.

The agent is built with LangGraph. The tools are exposed through a FastMCP server and loaded into the graph with `langchain-mcp-adapters`.

## Tools

Defined in `backend/app/api/mcp_tools.py`:

| Tool | Purpose |
| --- | --- |
| `list_tables` | Lists public tables and their columns |
| `get_column_stats` | Count, mean, stddev, min, max, and quartiles for a column |
| `check_iqr_outlier` | Outliers using the interquartile range method |
| `check_zscore_outlier` | Outliers using a z-score threshold (default 3.0) |
| `check_row_count_trend` | Daily row counts over a window of days |
| `freshness_check` | Whether the latest date in a column is within a threshold |
| `check_recent_schema_changes` | Looks up DDL changes near a timestamp in `schema_change_log` |

## Project layout

```
backend/
  requirements.txt
  app/api/
    routes.py          FastAPI app: /, /schema, /scan (SSE)
    agent.py           LangGraph planner and agent loop, event translation
    mcp_tools.py       FastMCP server and database tools
    supabase/
      db_populate.py   Seeds a demo database with planted anomalies
frontend/
  index.html           Dashboard markup
  app.js               SSE client, trace and findings rendering, mock replay
  styles.css
```

## Requirements

- Python 3.11+
- A Postgres database (a Supabase project works)
- A Google Gemini API key

## Setup

Create a `.env` file in the directory you run the backend from:

```
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.5-flash-lite   # optional, this is the default
SUPABASE_DB_URL=postgresql://user:password@host:5432/postgres
```

Install the backend dependencies:

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt
```

If `SUPABASE_DB_URL` is not set, every tool returns clearly labelled mock data. This lets you run the UI without a database.

To seed a demo database with planted anomalies (about 60 normal users plus a 25-signup burst; 300 orders with six extreme amounts and a 35-order volume spike):

```bash
python backend/app/api/supabase/db_populate.py
```

## Running

Start the API from the `backend/` directory:

```bash
uvicorn app.api.routes:app --reload
```

Then open `frontend/index.html` in a browser. Serving it with a static server such as VS Code Live Server on `127.0.0.1:5500` works with the CORS configuration.

The UI calls the backend at the URL set by `API` at the top of `frontend/app.js`. It is currently set to the hosted backend, so change it to `http://localhost:8000` to use a local server.

Options in the UI:

- **Run demo scan** starts a scan and streams it live. Press again to stop.
- **View schema** shows the public tables and columns.
- **Mock data** (or `?mock=1` in the URL) replays a scripted scan with no backend. Add `&autorun=1` to start it immediately.

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Health check |
| GET | `/schema` | Public tables and columns, with an `is_mock` flag |
| GET | `/scan` | Runs the agent on "Check the orders table for anomalies." and streams Server-Sent Events |

Events from `/scan`: `rls`, `plan`, `tool_call`, `tool_result`, `summary`, `done`, and `error`. The event contract is documented at the top of `frontend/app.js`.

## Notes

- `check_recent_schema_changes` reads from a `schema_change_log` table, which is not created by `db_populate.py`. You need to set it up in your database (for example with an event trigger that records DDL) for that tool to return results.
- Tool output is truncated to 4000 characters before it is sent to the browser.
- The agent is capped at 15 model turns.
