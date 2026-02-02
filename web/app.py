import asyncio
import json
import logging
from typing import Dict, List, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = logging.getLogger("web_dashboard")

app = FastAPI(title="Telegram Media Downloader Dashboard")

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Глобальное состояние
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                # В случае ошибки удаления (например, клиент уже отключился)
                pass

manager = ConnectionManager()

# Глобальное состояние
PROGRESS_STATE = {
    "overall": {"total": 0, "completed": 0, "status": "Idle", "speed": 0},
    "chats": {}, # chat_id -> {title, total, completed, status}
    "active_downloads": {} # task_id -> {description, total, completed}
}

async def update_overall(total: int = None, completed: int = None, status: str = None):
    if total is not None: PROGRESS_STATE["overall"]["total"] = total
    if completed is not None: PROGRESS_STATE["overall"]["completed"] = completed
    if status is not None: PROGRESS_STATE["overall"]["status"] = status
    await manager.broadcast(json.dumps(PROGRESS_STATE))

async def update_chat(chat_id: int, title: str = None, total: int = None, completed: int = None, status: str = None):
    if chat_id not in PROGRESS_STATE["chats"]:
        PROGRESS_STATE["chats"][chat_id] = {"title": "", "total": 0, "completed": 0, "status": "Pending"}

    chat = PROGRESS_STATE["chats"][chat_id]
    if title is not None: chat["title"] = title
    if total is not None: chat["total"] = total
    if completed is not None: chat["completed"] = completed
    if status is not None: chat["status"] = status
    await manager.broadcast(json.dumps(PROGRESS_STATE))

async def update_download(task_id: Any, description: str = None, total: int = None, completed: int = None):
    tid = str(task_id)
    if tid not in PROGRESS_STATE["active_downloads"]:
        PROGRESS_STATE["active_downloads"][tid] = {"description": "", "total": 0, "completed": 0}

    dl = PROGRESS_STATE["active_downloads"][tid]
    if description is not None: dl["description"] = description
    if total is not None: dl["total"] = total
    if completed is not None: dl["completed"] = completed

    # Регулярно чистить старые таски? Или по завершении.
    await manager.broadcast(json.dumps(PROGRESS_STATE))

@app.get("/api/status")
async def get_status():
    return PROGRESS_STATE

@app.get("/api/stats")
async def get_stats():
    """Получить статистику из ClickHouse."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"enabled": False, "error": "ClickHouse is disabled"}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    client = db._get_client()

    try:
        # Статика по чатам
        chats_data = client.execute("SELECT title, message_count, total_size FROM chats ORDER BY message_count DESC")
        # Статистика по дням (последние 30 дней)
        history_data = client.execute("""
            SELECT toDate(date) as d, count() as c
            FROM messages
            WHERE date > subtractDays(now(), 30)
            GROUP BY d ORDER BY d
        """)

        return {
            "enabled": True,
            "chats": [{"title": r[0], "count": r[1], "size": r[2]} for r in chats_data],
            "history": [{"date": str(r[0]), "count": r[1]} for r in history_data]
        }
    except Exception as e:
        return {"enabled": True, "error": str(e)}

@app.websocket("/ws/progress")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Отправляем текущее состояние сразу после подключения
        await websocket.send_text(json.dumps(PROGRESS_STATE))
        while True:
            # Просто держим соединение открытым
            # Клиент может присылать подтверждения или команды (в будущем)
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Монтирование статических файлов (должно быть ПОСЛЕ всех остальных маршрутов)
import os
static_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(static_path):
    app.mount("/", StaticFiles(directory=static_path, html=True), name="static")

def run_server(host="0.0.0.0", port=8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="error")
