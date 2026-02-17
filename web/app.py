import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
    "chats": {},  # chat_id -> {title, total, completed, status}
    "active_downloads": {},  # task_id -> {description, total, completed, created_at}
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

def _get_stale_task_timeout() -> float:
    """Таймаут (сек) для признания задачи зависшей. Из config или 120 по умолчанию."""
    try:
        from utils.config import ConfigManager
        cfg = ConfigManager().load()
        val = (cfg.get("download_settings") or {}).get("stale_task_timeout", 120)
        return float(val) if val is not None else 120.0
    except Exception:
        return 120.0


async def update_download(task_id: Any, description: str = None, total: int = None, completed: int = None):
    tid = str(task_id)
    if tid not in PROGRESS_STATE["active_downloads"]:
        PROGRESS_STATE["active_downloads"][tid] = {
            "description": "", "total": 0, "completed": 0,
            "created_at": time.monotonic(),
        }

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


async def _cleanup_stale_tasks() -> None:
    """Удалить зависшие задачи (completed=0 дольше stale_task_timeout сек)."""
    timeout = _get_stale_task_timeout()
    now = time.monotonic()
    to_remove = []
    for tid, dl in PROGRESS_STATE["active_downloads"].items():
        if tid in _CLEANUP_SCHEDULED:
            continue
        created = dl.get("created_at", now)
        cmp_val = int(dl.get("completed") or 0)
        tot_val = int(dl.get("total") or 0)
        if tot_val > 0 and cmp_val < tot_val and (now - created) >= timeout:
            to_remove.append(tid)
    for tid in to_remove:
        PROGRESS_STATE["active_downloads"].pop(tid, None)
    if to_remove:
        await manager.broadcast(json.dumps(PROGRESS_STATE))


@app.on_event("startup")
async def _start_stale_cleanup_loop():
    """Фоновая задача: периодическая чистка зависших загрузок."""
    async def _loop():
        while True:
            await asyncio.sleep(30)
            try:
                await _cleanup_stale_tasks()
            except Exception as e:
                logger.warning("Ошибка чистки зависших задач: %s", e)

    asyncio.create_task(_loop())


@app.get("/api/status")
async def get_status():
    return PROGRESS_STATE

def _fetch_stats_sync(db) -> dict:
    """Синхронное получение статистики — выполняется в executor, чтобы не блокировать event loop."""
    client = db._get_client()
    # Дедупликация по (chat_id, message_id) для корректного подсчета сообщений
    chats_data = client.execute("""
        SELECT chat_id, any(chat_title) AS title, count() AS message_count,
               sum(file_size) AS total_size, any(username) AS username
        FROM (
            SELECT m.chat_id, m.message_id, any(m.chat_title) AS chat_title,
                   max(m.file_size) AS file_size, any(c.username) AS username
            FROM messages m
            LEFT JOIN chats c ON m.chat_id = c.chat_id
            GROUP BY m.chat_id, m.message_id
        )
        GROUP BY chat_id
        ORDER BY message_count DESC
    """)
    history_data = client.execute("""
        SELECT toDate(date) as d, count() as c
        FROM messages
        WHERE date > subtractDays(now(), 30)
        GROUP BY d ORDER BY d
    """)
    return {
        "chats": [
            {"chat_id": r[0], "title": (r[1] or "").strip() or f"Chat {r[0]}",
             "count": r[2], "size": r[3] or 0, "username": (r[4] or "").strip() if len(r) > 4 else ""}
            for r in chats_data
        ],
        "history": [{"date": str(r[0]), "count": r[1]} for r in history_data],
    }


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
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, _fetch_stats_sync, db)
        return {"enabled": True, "connected": True, **data}
    except Exception as e:
        msg = str(e)
        error_type = "unknown"
        lower = msg.lower()
        if "doesn't exist" in lower or "unknown table" in lower or "no such table" in lower:
            error_type = "schema_missing"
        elif "connect" in lower or "connection refused" in lower or "timed out" in lower:
            error_type = "connection"
        return {"enabled": True, "connected": False, "error": msg, "error_type": error_type, "chats": [], "history": []}


@app.get("/api/logs")
async def get_logs(
    q: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Логи приложения из ClickHouse с поиском по тексту и фильтром по уровню."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"items": [], "total": 0}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    items, total = await loop.run_in_executor(
        None,
        lambda: db.get_logs(q=q, level=level, limit=limit, offset=offset),
    )
    return {"items": items, "total": total}


@app.get("/api/files")
async def get_files(
    chat_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Список файлов загрузки (путь, статус) с поиском и фильтрами."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"items": [], "total": 0}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    items, total = await loop.run_in_executor(
        None,
        lambda: db.get_files(chat_id=chat_id, status=status, q=q, limit=limit, offset=offset),
    )
    return {"items": items, "total": total}


@app.get("/api/chat/{chat_id}/info")
async def get_chat_info(chat_id: int):
    """Метаданные чата: description, profile_link."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"description": "", "profile_link": ""}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    meta = await loop.run_in_executor(None, lambda: db.get_chat_meta(chat_id))
    return {"description": meta.get("description", ""), "profile_link": meta.get("profile_link", "")}


@app.get("/api/chats/by-username/{username}")
async def get_chat_by_username(username: str):
    """Найти chat_id по username."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"chat_id": None}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    chat_id = await loop.run_in_executor(
        None,
        lambda: db.get_chat_id_by_username(username)
    )
    return {"chat_id": chat_id}


@app.get("/api/chat/{chat_id}/messages")
async def get_chat_messages(
    chat_id: int,
    offset: int = Query(0, ge=0, le=10_000_000),
    limit: int = Query(100, ge=1, le=500),
):
    """Пагинированная выдача сообщений чата (динамическая загрузка)."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"messages": [], "total": 0}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    messages, total = await loop.run_in_executor(
        None,
        lambda: db.get_messages_page(chat_id, offset=offset, limit=limit),
    )
    return {"messages": messages, "total": total}


def _get_base_directory() -> str:
    """Абсолютный путь base_directory из конфига."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    base = (config.get("download_settings") or {}).get("base_directory") or ""
    if not base or not base.strip():
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = base.strip()
    if not os.path.isabs(base):
        config_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base = os.path.abspath(os.path.join(config_dir, base))
    else:
        base = os.path.abspath(base)
    return base


def _path_under_base(file_path: str, base_directory: str) -> bool:
    """Проверить, что file_path находится внутри base_directory (без ..)."""
    if not file_path or not base_directory:
        return False
    abs_file = os.path.abspath(file_path)
    abs_base = os.path.abspath(base_directory)
    try:
        return os.path.commonpath([abs_file, abs_base]) == abs_base and abs_file.startswith(abs_base)
    except ValueError:
        return False


def _guess_media_type(file_path: str, ch_media_type: str) -> str:
    """Определить MIME-тип: из CH (photo/video) или по расширению."""
    if ch_media_type:
        mt = ch_media_type.lower()
        if mt == "photo":
            return "image/jpeg"
        if mt in ("video", "video_note"):
            return "video/mp4"
        if mt in ("audio", "voice"):
            return "audio/mpeg"
    import mimetypes
    guessed, _ = mimetypes.guess_type(file_path)
    return guessed or "application/octet-stream"


@app.get("/api/chat/{chat_id}/message/{message_id}/file")
async def get_message_file(chat_id: int, message_id: int):
    """Раздача файла сообщения по chat_id и message_id. Путь проверяется относительно base_directory."""
    from fastapi import HTTPException
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        raise HTTPException(status_code=404, detail="ClickHouse disabled")

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    file_path, ch_media_type = await loop.run_in_executor(
        None,
        lambda: db.get_message_file_path_and_media_type(chat_id, message_id),
    )
    if not file_path:
        raise HTTPException(status_code=404, detail="Message or file not found")

    base_dir = _get_base_directory()
    if not _path_under_base(file_path, base_dir):
        raise HTTPException(status_code=404, detail="File path not allowed")

    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    filename = os.path.basename(file_path)
    media_type = _guess_media_type(file_path, ch_media_type)
    is_inline = media_type.startswith("image/") or media_type.startswith("video/")
    disposition = "inline" if is_inline else "attachment"

    # FileResponse сам формирует Content-Disposition из filename,
    # корректно кодируя не-ASCII символы через RFC 5987 (filename*=utf-8''...)
    return FileResponse(
        file_path,
        filename=filename,
        media_type=media_type,
        content_disposition_type=disposition,
    )


@app.get("/api/chat/{chat_id}/message/{message_id}/path")
async def get_message_path(chat_id: int, message_id: int):
    """Вернуть абсолютные пути к файлу и родительской папке (для копирования / открытия папки)."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"file": "", "dir": ""}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    file_path = await loop.run_in_executor(
        None,
        lambda: db.get_message_file_path(chat_id, message_id),
    )
    if not file_path:
        return {"file": "", "dir": ""}

    base_dir = _get_base_directory()
    if not _path_under_base(file_path, base_dir):
        return {"file": "", "dir": ""}

    abs_file = os.path.abspath(file_path)
    abs_dir = os.path.dirname(abs_file)
    return {"file": abs_file, "dir": abs_dir}


@app.post("/api/chat/{chat_id}/message/{message_id}/open_folder")
async def open_message_folder(chat_id: int, message_id: int, request: Request):
    """
    Открыть папку с файлом в проводнике (только при web.open_file_manager: true и запросе с localhost).
    """
    from fastapi import HTTPException
    from utils.config import ConfigManager
    config = ConfigManager().load()
    web_cfg = config.get("web") or {}
    if not web_cfg.get("open_file_manager"):
        raise HTTPException(status_code=403, detail="open_file_manager is disabled")

    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(status_code=403, detail="Only localhost is allowed for open_folder")

    path_data = await get_message_path(chat_id, message_id)
    dir_path = (path_data.get("dir") or "").strip()
    if not dir_path:
        raise HTTPException(status_code=404, detail="Path not found")

    base_dir = _get_base_directory()
    if not _path_under_base(dir_path, base_dir):
        raise HTTPException(status_code=403, detail="Path not allowed")

    import subprocess
    import sys
    try:
        if sys.platform == "win32":
            file_path = (path_data.get("file") or "").strip()
            if file_path:
                subprocess.run(["explorer", "/select," + file_path], check=False, timeout=5)
            else:
                subprocess.run(["explorer", dir_path], check=False, timeout=5)
        else:
            subprocess.run(["xdg-open", dir_path], check=False, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.warning("open_folder failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"ok": True}


class AddChatRequest(BaseModel):
    """Тело запроса POST /api/chats/add."""
    chat_id: Optional[int] = None
    title: Optional[str] = None
    username: Optional[str] = None


@app.post("/api/chats/add")
async def add_chat_to_downloads(body: AddChatRequest):
    """Добавить чат в список загрузок."""
    from utils.config import ConfigManager
    cm = ConfigManager()
    config = cm.load()

    # Если передан username, попробовать найти chat_id
    chat_id = body.chat_id
    if not chat_id and body.username:
        from utils.clickhouse_db import ClickHouseMetadataDB
        ch_config = config.get("clickhouse", {})
        if ch_config.get("enabled"):
            db = ClickHouseMetadataDB(ch_config)
            loop = asyncio.get_event_loop()
            found_id = await loop.run_in_executor(
                None,
                lambda: db.get_chat_id_by_username(body.username)
            )
            if found_id:
                chat_id = found_id

    if not chat_id:
        return {"added": False, "message": "Не указан chat_id или username не найден"}

    try:
        added = cm.add_chat_to_download_list(chat_id, body.title or body.username)
        cm.save()
        return {"added": added, "message": "Чат добавлен" if added else "Чат уже в списке"}
    except Exception as e:
        return {"added": False, "message": str(e)}


@app.get("/api/settings")
async def get_settings():
    """Публичные настройки для фронта (например open_file_manager)."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    web_cfg = config.get("web") or {}
    return {"open_file_manager": bool(web_cfg.get("open_file_manager"))}


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
    except asyncio.CancelledError:
        # Нормальная ситуация при остановке сервера или закрытии соединения
        manager.disconnect(websocket)
        raise

# Монтирование статических файлов (должно быть ПОСЛЕ всех остальных маршрутов)
static_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(static_path):
    app.mount("/", StaticFiles(directory=static_path, html=True), name="static")
else:
    @app.get("/")
    @app.get("/{full_path:path}")
    def _serve_frontend_fallback(full_path: str = ""):
        """Если папка static не собрана — подсказка собрать фронтенд."""
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>TMD Dashboard</title></head><body>"
            "<h1>Фронтенд не собран</h1><p>Выполните в корне проекта:</p>"
            "<pre>cd web/ui && npm install && npm run build</pre>"
            "<p>Затем перезапустите приложение с <code>--web</code>.</p></body></html>",
            status_code=503,
        )

def run_server(host="0.0.0.0", port=8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="error")
