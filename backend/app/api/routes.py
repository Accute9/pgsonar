from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
import asyncio
import json
import logging
from .agent import stream_agent
from .mcp_tools import get_schema

load_dotenv()
log = logging.getLogger("pgsonar")


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "https://pgsonar-frontend.vercel.app"],
    allow_credentials=True,
    allow_origin_regex="http://127.0.0.1:.*",
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/")
def index():
    return {"message": "API is running"}

@app.get("/schema")
def schema():
    tables, is_mock = get_schema()
    return {
        "is_mock": is_mock,
        "tables": [
            {"name": name, "columns": [{"name": c, "type": t} for c, t in cols]}
            for name, cols in tables
        ],
    }

@app.get("/scan")
async def scan():
    def sse(event, payload):
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"
    async def generator():
        try:
            async for event, payload in stream_agent("Check the orders table for anomalies."):
                yield sse(event, payload)
        except Exception:
            # The response has already started, so the status is 200 and a 500 is impossible.
            # Log the details server-side and tell the page with an error event instead.
            log.exception("scan failed")
            yield sse("error", {"message": "The scan failed unexpectedly. Please try again."})
    return StreamingResponse(
        generator(), 
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
