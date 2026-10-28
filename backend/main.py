import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backend.sessions import build_echo, build_error, new_session_id

logger = logging.getLogger("voice_coach")
app = FastAPI(title="Voice Coach")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session_id = new_session_id()
    logger.info("session %s connected", session_id)
    await websocket.send_json({"type": "session", "session_id": session_id})
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(build_error(session_id, "invalid JSON"))
                continue
            if not isinstance(message, dict):
                await websocket.send_json(build_error(session_id, "JSON must be an object"))
                continue
            await websocket.send_json(build_echo(session_id, message))
    except WebSocketDisconnect:
        logger.info("session %s disconnected", session_id)
