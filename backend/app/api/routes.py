from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
import asyncio
import json
import logging
from agent import stream_agent

load_dotenv()
log = logging.getLogger("pgsonar")


app = FastAPI()


@app.get("/")
def index():
    return {"message": "API is running"}

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
