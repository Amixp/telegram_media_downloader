import asyncio
import json
import logging
import time
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
    "overall": {"total": 0, "completed": 0, "status": "Idle", "speed": 0, "eta_seconds": None},
    "chats": {}, # chat_id -> {title, total, completed, status}
    "active_downloads": {} # task_id -> {description, total, completed}
}

# Чтобы "Active Threads" не разрастался бесконечно, удаляем завершённые задачи с небольшой задержкой
_CLEANUP_DELAY_SEC = 2.0
_CLEANUP_SCHEDULED: set[str] = set()

# Глобальный расчёт скорости/ETA по активным загрузкам
_DL_LAST_COMPLETED: Dict[str, int] = {}
_SPEED_EMA_BPS: float = 0.0
_SPEED_LAST_TS: float = time.monotonic()

def _recalc_speed_and_eta(tid: str, completed_val: int) -> None:
    """Пересчитать скорость (EMA) и ETA на основе дельты байт."""
    global _SPEED_EMA_BPS, _SPEED_LAST_TS  # pylint: disable=global-statement

    now = time.monotonic()
    prev_completed = _DL_LAST_COMPLETED.get(tid, completed_val)
    delta = completed_val - prev_completed
    if delta < 0:
        delta = 0
    _DL_LAST_COMPLETED[tid] = completed_val

    dt = now - _SPEED_LAST_TS
    if dt <= 0:
        dt = 1e-6

    inst_bps = float(delta) / dt
    alpha = 0.25
    if _SPEED_EMA_BPS <= 0:
        _SPEED_EMA_BPS = inst_bps
    else:
        _SPEED_EMA_BPS = (_SPEED_EMA_BPS * (1.0 - alpha)) + (inst_bps * alpha)
    _SPEED_LAST_TS = now

    # Скорость в MB/s
    PROGRESS_STATE["overall"]["speed"] = round(_SPEED_EMA_BPS / (1024.0 * 1024.0), 2)

    # ETA по суммарному remaining_bytes всех активных задач
    remaining = 0
    for v in PROGRESS_STATE["active_downloads"].values():
        try:
            t = int(v.get("total") or 0)
            c = int(v.get("completed") or 0)
        except Exception:
            continue
        if t > 0 and c < t:
            remaining += (t - c)

    if remaining > 0 and _SPEED_EMA_BPS > 1e-6:
        PROGRESS_STATE["overall"]["eta_seconds"] = int(remaining / _SPEED_EMA_BPS)
    else:
        PROGRESS_STATE["overall"]["eta_seconds"] = None

async def _cleanup_finished_download(tid: str) -> None:
    await asyncio.sleep(_CLEANUP_DELAY_SEC)
    PROGRESS_STATE["active_downloads"].pop(tid, None)
    _CLEANUP_SCHEDULED.discard(tid)
    await manager.broadcast(json.dumps(PROGRESS_STATE))

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
    if total is not None:
        dl["total"] = total
    if completed is not None:
        dl["completed"] = completed
        # Обновляем скорость/ETA только когда есть completed (дельта байт)
        try:
            _recalc_speed_and_eta(tid, int(completed))
        except Exception:
            pass

    # Регулярно чистить старые таски? Или по завершении.
    await manager.broadcast(json.dumps(PROGRESS_STATE))

    # Если задача завершена — запланировать удаление из active_downloads (чтобы список был только "активный")
    try:
        dl_total = int(dl.get("total") or 0)
        dl_completed = int(dl.get("completed") or 0)
    except Exception:
        dl_total = 0
        dl_completed = 0

    if dl_total > 0 and dl_completed >= dl_total and tid not in _CLEANUP_SCHEDULED:
        _CLEANUP_SCHEDULED.add(tid)
        asyncio.create_task(_cleanup_finished_download(tid))

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
        return {"enabled": False, "connected": False, "error": "ClickHouse is disabled", "chats": [], "history": []}

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
            "connected": True,
            "chats": [{"title": r[0], "count": r[1], "size": r[2]} for r in chats_data],
            "history": [{"date": str(r[0]), "count": r[1]} for r in history_data]
        }
    except Exception as e:
        msg = str(e)
        error_type = "unknown"
        lower = msg.lower()
        if "doesn't exist" in lower or "unknown table" in lower or "no such table" in lower:
            error_type = "schema_missing"
        elif "connect" in lower or "connection refused" in lower or "timed out" in lower:
            error_type = "connection"
        return {"enabled": True, "connected": False, "error": msg, "error_type": error_type, "chats": [], "history": []}

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
