import asyncio
import json
import logging
import os
import threading
import time
import uuid
from queue import Empty, PriorityQueue, Queue
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = logging.getLogger("web_dashboard")

# --- Резолвер username → chat_id и chat_id → title (фоновый поток + применение на main) ---
RESOLVE_JOBS: Dict[str, Dict[str, Any]] = {}
_RESOLVE_JOBS_LOCK = threading.Lock()
_RESOLVER_QUEUE: Queue = Queue()  # ("username",...) | ("chat_id",...) | ("profile_photo",...) | ("full_profile", chat_id, result_holder)
_RESOLVER_PRIORITY_QUEUE: PriorityQueue = PriorityQueue()
_PROFILE_PHOTO_JOB_DATA: Dict[str, bytes] = {}
_PROFILE_PHOTO_LOCK = threading.Lock()
_PENDING_APPLY_QUEUE: Queue = Queue()  # Элементы: (job_id, chat_id, title, username) или (job_id, chat_id, title, None) для title-only
_RESOLVER_THREAD: Optional[threading.Thread] = None
_RESOLVER_LOOP: Optional[asyncio.AbstractEventLoop] = None
_RESOLVER_JOB_DEADLINE_SEC = 5.0
_LINK_CACHE_TTL_SEC = 3000.0
_LINK_CACHE_LOCK = threading.Lock()
_USERNAME_CHAT_CACHE: Dict[str, tuple[float, Optional[int]]] = {}
_MESSAGE_EXISTS_CACHE: Dict[tuple[int, int], tuple[float, bool]] = {}
_RESOLVER_STATS: Dict[str, Any] = {
    "total": 0,
    "done": 0,
    "error": 0,
    "deadline_exceeded": 0,
    "retries": 0,
    "latency_ms_total": 0.0,
}


def _resolver_task_priority(task_type: str) -> int:
    if task_type in {"username", "chat_id"}:
        return 0
    if task_type == "profile_photo_job":
        return 1
    return 2


def _enqueue_resolver_task(item: Any) -> None:
    task_type = str(item[0]) if item else ""
    _RESOLVER_PRIORITY_QUEUE.put((_resolver_task_priority(task_type), time.monotonic(), item))


def _create_resolve_job(meta: Dict[str, Any]) -> str:
    job_id = str(uuid.uuid4())
    now_ts = time.time()
    with _RESOLVE_JOBS_LOCK:
        RESOLVE_JOBS[job_id] = {
            "status": "pending",
            "stage": "queued",
            "created_at": now_ts,
            "deadline_at": now_ts + _RESOLVER_JOB_DEADLINE_SEC,
            "retries": 0,
            **meta,
        }
    return job_id


def _record_resolver_latency(job_id: Optional[str]) -> None:
    if not job_id:
        return
    with _RESOLVE_JOBS_LOCK:
        job = RESOLVE_JOBS.get(job_id) or {}
    created = job.get("created_at")
    if not created:
        return
    latency_ms = max(0.0, (time.time() - float(created)) * 1000.0)
    _RESOLVER_STATS["latency_ms_total"] = float(_RESOLVER_STATS.get("latency_ms_total", 0.0)) + latency_ms


def _cache_get_username_chat_id(username: str) -> Optional[Optional[int]]:
    key = (username or "").strip().lower()
    if not key:
        return None
    now = time.time()
    with _LINK_CACHE_LOCK:
        rec = _USERNAME_CHAT_CACHE.get(key)
        if not rec:
            return None
        expires_at, value = rec
        if expires_at < now:
            _USERNAME_CHAT_CACHE.pop(key, None)
            return None
        return value


def _cache_set_username_chat_id(username: str, chat_id: Optional[int]) -> None:
    key = (username or "").strip().lower()
    if not key:
        return
    with _LINK_CACHE_LOCK:
        _USERNAME_CHAT_CACHE[key] = (time.time() + _LINK_CACHE_TTL_SEC, chat_id)


def _cache_get_message_exists(chat_id: int, message_id: int) -> Optional[bool]:
    now = time.time()
    key = (int(chat_id), int(message_id))
    with _LINK_CACHE_LOCK:
        rec = _MESSAGE_EXISTS_CACHE.get(key)
        if not rec:
            return None
        expires_at, value = rec
        if expires_at < now:
            _MESSAGE_EXISTS_CACHE.pop(key, None)
            return None
        return value


def _cache_set_message_exists(chat_id: int, message_id: int, exists: bool) -> None:
    key = (int(chat_id), int(message_id))
    with _LINK_CACHE_LOCK:
        _MESSAGE_EXISTS_CACHE[key] = (time.time() + _LINK_CACHE_TTL_SEC, bool(exists))

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
    """Фоновая задача: периодическая чистка зависших загрузок; запуск резолвера username."""
    global _RESOLVER_THREAD

    async def _cleanup_loop():
        while True:
            await asyncio.sleep(30)
            try:
                await _cleanup_stale_tasks()
            except Exception as e:
                logger.warning("Ошибка чистки зависших задач: %s", e)

    def _get_pending():
        return _PENDING_APPLY_QUEUE.get(timeout=1.0)

    async def _apply_pending_loop():
        loop = asyncio.get_event_loop()
        while True:
            try:
                item = await loop.run_in_executor(None, _get_pending)
                job_id, chat_id, title, username = item
                await loop.run_in_executor(
                    None,
                    lambda j=job_id, c=chat_id, t=title, u=username: _apply_resolved_chat(j, c, t, u),
                )
            except Empty:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Ошибка применения резолва: %s", e)

    _RESOLVER_THREAD = threading.Thread(target=_resolve_username_worker, daemon=True)
    _RESOLVER_THREAD.start()
    asyncio.create_task(_cleanup_loop())
    asyncio.create_task(_apply_pending_loop())


@app.get("/api/status")
async def get_status():
    return PROGRESS_STATE

def _fetch_stats_sync(db) -> dict:
    """Синхронное получение статистики — выполняется в executor, чтобы не блокировать event loop."""
    client = db._get_client()
    # Получаем все чаты из таблицы chats и статистику сообщений через LEFT JOIN
    # Это позволяет включить чаты с 0 сообщений
    chats_data = client.execute("""
        SELECT
            c.chat_id,
            any(c.title) AS title,
            coalesce(msg_stats.message_count, 0) AS message_count,
            coalesce(msg_stats.total_size, 0) AS total_size,
            any(coalesce(c.username, '')) AS username
        FROM chats c
        LEFT JOIN (
            SELECT
                chat_id,
                count() AS message_count,
                sum(file_size) AS total_size
            FROM (
                SELECT chat_id, message_id, max(file_size) AS file_size
                FROM messages
                GROUP BY chat_id, message_id
            )
            GROUP BY chat_id
        ) msg_stats ON c.chat_id = msg_stats.chat_id
        GROUP BY c.chat_id, msg_stats.message_count, msg_stats.total_size
        ORDER BY message_count DESC, c.chat_id DESC
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
        # Запустить резолв title для чатов с дефолтным именем (в фоне, не блокируя ответ)
        chats_to_resolve = []
        # Проверить активные резолвы для chat_id, чтобы не дублировать
        active_resolves = set()
        with _RESOLVE_JOBS_LOCK:
            for job_data in RESOLVE_JOBS.values():
                if job_data.get("status") in ("pending", "processing") and "chat_id" in job_data:
                    active_resolves.add(job_data["chat_id"])
        for chat in data.get("chats", []):
            chat_id = chat.get("chat_id")
            title = chat.get("title", "")
            # Если имя дефолтное (пустое или "Chat {chat_id}") и нет активного резолва — добавить в очередь
            if chat_id and (
                not title or title.strip() == "" or title.strip() == f"Chat {chat_id}"
            ) and chat_id not in active_resolves:
                chats_to_resolve.append(chat_id)
        # Запускаем резолв для всех найденных чатов (в фоне)
        for chat_id in chats_to_resolve[:10]:  # Ограничение: не более 10 за раз
            job_id = _create_resolve_job({"chat_id": chat_id})
            _enqueue_resolver_task(("chat_id", job_id, chat_id))
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
        return {"description": "", "profile_link": "", "username": ""}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    meta = await loop.run_in_executor(None, lambda: db.get_chat_meta(chat_id))
    if meta is None:
        meta = {}
    return {
        "description": meta.get("description", ""),
        "profile_link": meta.get("profile_link", ""),
        "username": meta.get("username", ""),
    }


@app.post("/api/chat/{chat_id}/profile-photo/request")
async def request_chat_profile_photo(chat_id: int):
    """Создать фоновую задачу получения фото профиля чата."""
    job_id = _create_resolve_job({"job_type": "profile_photo", "chat_id": chat_id})
    _enqueue_resolver_task(("profile_photo_job", job_id, chat_id))
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/chat/profile-photo/{job_id}")
async def get_chat_profile_photo_by_job(job_id: str):
    """Получить фото профиля по готовой задаче profile_photo."""
    with _RESOLVE_JOBS_LOCK:
        job = RESOLVE_JOBS.get(job_id)
    if not job:
        return Response(status_code=404, content=b"")
    if job.get("status") != "done":
        return Response(status_code=409, content=b"")
    with _PROFILE_PHOTO_LOCK:
        data = _PROFILE_PHOTO_JOB_DATA.get(job_id)
    if not data:
        return Response(status_code=404, content=b"")
    return Response(content=data, media_type="image/jpeg")


@app.post("/api/chat/{chat_id}/full-info/request")
async def request_chat_full_info(chat_id: int):
    """Создать фоновую задачу получения расширенной информации профиля чата."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    base = {"description": "", "profile_link": "", "username": ""}
    if ch_config.get("enabled"):
        from utils.clickhouse_db import ClickHouseMetadataDB
        db = ClickHouseMetadataDB(ch_config)
        loop = asyncio.get_event_loop()
        meta = await loop.run_in_executor(None, lambda: db.get_chat_meta(chat_id))
        if meta:
            base["description"] = meta.get("description", "")
            base["profile_link"] = meta.get("profile_link", "")
            base["username"] = meta.get("username", "")

    job_id = _create_resolve_job({"job_type": "full_profile", "chat_id": chat_id, **base})
    _enqueue_resolver_task(("full_profile_job", job_id, chat_id, base))
    return {"job_id": job_id, "status": "pending", "base": base}


@app.get("/api/chats/by-username/{username}")
async def get_chat_by_username(username: str):
    """Найти chat_id по username."""
    cached = _cache_get_username_chat_id(username)
    if cached is not None:
        return {"chat_id": cached}
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
    _cache_set_username_chat_id(username, int(chat_id) if chat_id is not None else None)
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


@app.get("/api/messages/search")
async def search_messages_all_chats(
    q: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Нечеткий поиск сообщений по тексту во всех чатах."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"messages": [], "total": 0}
    if not (q or "").strip():
        return {"messages": [], "total": 0}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    messages, total = await loop.run_in_executor(
        None,
        lambda: db.search_messages_all_chats(q.strip(), offset=offset, limit=limit),
    )
    return {"messages": messages, "total": total}


@app.post("/api/chat/{chat_id}/clear")
async def clear_chat_messages(chat_id: int):
    """Очистить все сообщения чата в ClickHouse (метаданные чата не трогает)."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="ClickHouse disabled")
    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: db.clear_chat_messages(chat_id))
    return {"ok": True}


@app.post("/api/chat/{chat_id}/remove")
async def remove_chat_from_archive_api(chat_id: int):
    """Удалить чат из архива (config, JSONL, HTML, ClickHouse; медиа по умолчанию не удалять)."""
    from fastapi import HTTPException
    try:
        from remove_chat_from_archive import remove_chat_from_archive
        from utils.config import ConfigManager
    except ImportError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    cm = ConfigManager()
    try:
        cm.load()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Config load: {e}") from e
    config_path = getattr(cm, "config_path", None) or "config.yaml"
    code = remove_chat_from_archive(
        config_path=config_path,
        chat_ids=[chat_id],
        delete_media=False,
        dry_run=False,
        yes=True,
    )
    if code != 0:
        raise HTTPException(status_code=500, detail="remove_chat_from_archive failed")
    return {"ok": True}


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


@app.get("/api/chat/{chat_id}/message/{message_id}/exists")
async def message_exists(chat_id: int, message_id: int):
    """Проверить, есть ли сообщение в архиве (для локализации ссылок)."""
    cached = _cache_get_message_exists(chat_id, message_id)
    if cached is not None:
        return {"exists": cached}
    from utils.config import ConfigManager
    config = ConfigManager().load()
    ch_config = config.get("clickhouse", {})
    if not ch_config.get("enabled"):
        return {"exists": False}

    from utils.clickhouse_db import ClickHouseMetadataDB
    db = ClickHouseMetadataDB(ch_config)
    loop = asyncio.get_event_loop()
    exists = await loop.run_in_executor(
        None,
        lambda: db.message_exists(chat_id, message_id),
    )
    _cache_set_message_exists(chat_id, message_id, bool(exists))
    return {"exists": exists}


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


def _resolve_username_worker() -> None:
    """Фоновый поток: резолв username → chat_id через Telethon."""
    global _RESOLVER_LOOP
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _RESOLVER_LOOP = loop

    try:
        from utils.config import ConfigManager
        from utils.proxy import get_proxy_config
        from telethon import TelegramClient
        from telethon.utils import get_peer_id
        from utils.meta import APP_VERSION, DEVICE_MODEL, LANG_CODE, SYSTEM_VERSION
    except ImportError as e:
        logger.warning("Резолвер username недоступен (нет зависимостей): %s", e)
        return

    async def _resolver_ensure_authorized(cl):
        """Подключиться и проверить авторизацию без запроса телефона/кода в консоли."""
        await cl.connect()
        if not await cl.is_user_authorized():
            await cl.disconnect()
            raise RuntimeError(
                "Основная сессия Telegram (media_downloader) не авторизована."
            )

    def _has_active_downloads() -> bool:
        """Есть ли сейчас активные загрузки файлов."""
        try:
            active = PROGRESS_STATE.get("active_downloads") or {}
            return len(active) > 0
        except Exception:
            return False

    def _is_sqlite_locked_error(exc: Exception) -> bool:
        return "database is locked" in str(exc).lower()

    def _is_username_not_found_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "no user has" in text
            or "username is not in use" in text
            or "username_not_occupied" in text
        )

    def _job_deadline_exceeded(job_id: Optional[str]) -> bool:
        if not job_id:
            return False
        with _RESOLVE_JOBS_LOCK:
            job = RESOLVE_JOBS.get(job_id) or {}
        deadline_at = float(job.get("deadline_at") or 0.0)
        return deadline_at > 0 and time.time() > deadline_at

    def _set_job_stage(job_id: Optional[str], stage: str) -> None:
        if not job_id:
            return
        with _RESOLVE_JOBS_LOCK:
            job = RESOLVE_JOBS.get(job_id)
            if not job:
                return
            job["stage"] = stage
            if stage == "running" and not job.get("started_at"):
                job["started_at"] = time.time()

    def _set_deadline_exceeded(job_id: Optional[str], error_text: str = "Достигнут дедлайн 5 сек") -> None:
        if not job_id:
            return
        with _RESOLVE_JOBS_LOCK:
            job = RESOLVE_JOBS.get(job_id)
            if not job:
                return
            job["status"] = "deadline_exceeded"
            job["stage"] = "deadline_exceeded"
            job["error"] = error_text

    while True:
        try:
            try:
                _, _, item = _RESOLVER_PRIORITY_QUEUE.get(timeout=1.0)
            except Empty:
                item = _RESOLVER_QUEUE.get(timeout=1.0)
            if item is None:
                break
            task_type = item[0]
            job_id = item[1] if len(item) > 1 and isinstance(item[1], str) else None
            if _job_deadline_exceeded(job_id):
                _set_deadline_exceeded(job_id)
                _RESOLVER_STATS["deadline_exceeded"] = int(_RESOLVER_STATS.get("deadline_exceeded", 0)) + 1
                continue
            _set_job_stage(job_id, "running")
            _RESOLVER_STATS["total"] = int(_RESOLVER_STATS.get("total", 0)) + 1
            if task_type == "username":
                job_id, _, username, title_from_request = item
                username = (username or "").strip().lstrip("@")
                if not username:
                    with _RESOLVE_JOBS_LOCK:
                        RESOLVE_JOBS[job_id] = {
                            "status": "error",
                            "error": "Пустой username",
                            "username": username,
                            "title": title_from_request or "",
                        }
                    continue

                client = None
                try:
                    cm = ConfigManager()
                    config = cm.load()
                    api_id = config.get("api_id")
                    api_hash = config.get("api_hash")
                    if not api_id or not api_hash:
                        with _RESOLVE_JOBS_LOCK:
                            RESOLVE_JOBS[job_id] = {
                                "status": "error",
                                "error": "В конфиге не заданы api_id/api_hash",
                                "username": username,
                                "title": title_from_request or "",
                            }
                        continue
                    proxy_config = get_proxy_config(config)
                    download_settings = config.get("download_settings") or {}
                    request_retries = download_settings.get("request_retries", 15)
                    session_name = "media_downloader"
                    client = TelegramClient(
                        session_name,
                        api_id=api_id,
                        api_hash=api_hash,
                        proxy=proxy_config,
                        device_model=DEVICE_MODEL,
                        system_version=SYSTEM_VERSION,
                        app_version=APP_VERSION,
                        lang_code=LANG_CODE,
                        request_retries=request_retries,
                    )

                    async def _resolve_user():
                        await _resolver_ensure_authorized(client)
                        entity = await client.get_entity(username)
                        return entity

                    entity = loop.run_until_complete(_resolve_user())
                    chat_id = get_peer_id(entity)
                    title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or username
                    title = (title or "").strip() or f"@{username}"
                    with _RESOLVE_JOBS_LOCK:
                        RESOLVE_JOBS[job_id] = {
                            "status": "done",
                            "stage": "done",
                            "chat_id": chat_id,
                            "title": title,
                            "username": username,
                        }
                    _cache_set_username_chat_id(username, int(chat_id))
                    _RESOLVER_STATS["done"] = int(_RESOLVER_STATS.get("done", 0)) + 1
                    _record_resolver_latency(job_id)
                    _PENDING_APPLY_QUEUE.put((job_id, chat_id, title, username))
                except Exception as e:
                    if _is_sqlite_locked_error(e):
                        if _job_deadline_exceeded(job_id):
                            _set_deadline_exceeded(job_id)
                            continue
                        with _RESOLVE_JOBS_LOCK:
                            if job_id in RESOLVE_JOBS:
                                RESOLVE_JOBS[job_id]["stage"] = "retrying"
                                RESOLVE_JOBS[job_id]["retries"] = int(RESOLVE_JOBS[job_id].get("retries") or 0) + 1
                        _RESOLVER_STATS["retries"] = int(_RESOLVER_STATS.get("retries", 0)) + 1
                        _enqueue_resolver_task(item)
                        time.sleep(0.15)
                        continue
                    if _is_username_not_found_error(e):
                        logger.warning("Резолв username %s: username не найден", username)
                        _cache_set_username_chat_id(username, None)
                        with _RESOLVE_JOBS_LOCK:
                            RESOLVE_JOBS[job_id] = {
                                "status": "error",
                                "stage": "error",
                                "error": f"Username @{username} не найден",
                                "username": username,
                                "title": title_from_request or "",
                            }
                        _RESOLVER_STATS["error"] = int(_RESOLVER_STATS.get("error", 0)) + 1
                        continue
                    logger.exception("Резолв username %s: %s", username, e)
                    with _RESOLVE_JOBS_LOCK:
                        RESOLVE_JOBS[job_id] = {
                            "status": "error",
                            "stage": "error",
                            "error": str(e),
                            "username": username,
                            "title": title_from_request or "",
                        }
                    _RESOLVER_STATS["error"] = int(_RESOLVER_STATS.get("error", 0)) + 1
                finally:
                    if client:
                        try:

                            async def _disconnect():
                                await client.disconnect()

                            loop.run_until_complete(_disconnect())
                        except Exception:
                            pass
            elif task_type == "chat_id":
                job_id, _, chat_id = item
                client = None
                try:
                    cm = ConfigManager()
                    config = cm.load()
                    api_id = config.get("api_id")
                    api_hash = config.get("api_hash")
                    if not api_id or not api_hash:
                        with _RESOLVE_JOBS_LOCK:
                            RESOLVE_JOBS[job_id] = {
                                "status": "error",
                                "error": "В конфиге не заданы api_id/api_hash",
                                "chat_id": chat_id,
                            }
                        continue
                    proxy_config = get_proxy_config(config)
                    download_settings = config.get("download_settings") or {}
                    request_retries = download_settings.get("request_retries", 15)
                    session_name = "media_downloader"
                    client = TelegramClient(
                        session_name,
                        api_id=api_id,
                        api_hash=api_hash,
                        proxy=proxy_config,
                        device_model=DEVICE_MODEL,
                        system_version=SYSTEM_VERSION,
                        app_version=APP_VERSION,
                        lang_code=LANG_CODE,
                        request_retries=request_retries,
                    )

                    async def _resolve_chat():
                        await _resolver_ensure_authorized(client)
                        entity = await client.get_entity(chat_id)
                        return entity

                    entity = loop.run_until_complete(_resolve_chat())
                    title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or f"Chat {chat_id}"
                    title = (title or "").strip() or f"Chat {chat_id}"
                    username = getattr(entity, "username", None) or ""
                    username = (username or "").strip()
                    with _RESOLVE_JOBS_LOCK:
                        RESOLVE_JOBS[job_id] = {
                            "status": "done",
                            "stage": "done",
                            "chat_id": chat_id,
                            "title": title,
                            "username": username or None,
                        }
                    _RESOLVER_STATS["done"] = int(_RESOLVER_STATS.get("done", 0)) + 1
                    _record_resolver_latency(job_id)
                    _PENDING_APPLY_QUEUE.put((job_id, chat_id, title, username or None))
                except Exception as e:
                    if _is_sqlite_locked_error(e):
                        if _job_deadline_exceeded(job_id):
                            _set_deadline_exceeded(job_id)
                            continue
                        with _RESOLVE_JOBS_LOCK:
                            if job_id in RESOLVE_JOBS:
                                RESOLVE_JOBS[job_id]["stage"] = "retrying"
                                RESOLVE_JOBS[job_id]["retries"] = int(RESOLVE_JOBS[job_id].get("retries") or 0) + 1
                        _RESOLVER_STATS["retries"] = int(_RESOLVER_STATS.get("retries", 0)) + 1
                        _enqueue_resolver_task(item)
                        time.sleep(0.15)
                        continue
                    logger.exception("Резолв chat_id %s: %s", chat_id, e)
                    with _RESOLVE_JOBS_LOCK:
                        RESOLVE_JOBS[job_id] = {
                            "status": "error",
                            "stage": "error",
                            "error": str(e),
                            "chat_id": chat_id,
                        }
                    _RESOLVER_STATS["error"] = int(_RESOLVER_STATS.get("error", 0)) + 1
                finally:
                    if client:
                        try:

                            async def _disconnect():
                                await client.disconnect()

                            loop.run_until_complete(_disconnect())
                        except Exception:
                            pass
            elif task_type == "profile_photo_job":
                job_id, _, chat_id = item
                client = None
                try:
                    from io import BytesIO
                    cm = ConfigManager()
                    config = cm.load()
                    api_id = config.get("api_id")
                    api_hash = config.get("api_hash")
                    if not api_id or not api_hash:
                        with _RESOLVE_JOBS_LOCK:
                            RESOLVE_JOBS[job_id] = {
                                "status": "error",
                                "job_type": "profile_photo",
                                "chat_id": chat_id,
                                "error": "В конфиге не заданы api_id/api_hash",
                            }
                        continue
                    proxy_config = get_proxy_config(config)
                    download_settings = config.get("download_settings") or {}
                    request_retries = download_settings.get("request_retries", 15)
                    session_name = "media_downloader"
                    client = TelegramClient(
                        session_name,
                        api_id=api_id,
                        api_hash=api_hash,
                        proxy=proxy_config,
                        device_model=DEVICE_MODEL,
                        system_version=SYSTEM_VERSION,
                        app_version=APP_VERSION,
                        lang_code=LANG_CODE,
                        request_retries=request_retries,
                    )

                    async def _get_profile_photo_job():
                        await _resolver_ensure_authorized(client)
                        entity = await client.get_entity(chat_id)
                        buf = BytesIO()
                        await client.download_profile_photo(entity, file=buf)
                        return buf.getvalue()

                    photo_bytes = loop.run_until_complete(_get_profile_photo_job())
                    if photo_bytes:
                        with _PROFILE_PHOTO_LOCK:
                            _PROFILE_PHOTO_JOB_DATA[job_id] = photo_bytes
                        with _RESOLVE_JOBS_LOCK:
                            RESOLVE_JOBS[job_id] = {
                                "status": "done",
                                "stage": "done",
                                "job_type": "profile_photo",
                                "chat_id": chat_id,
                            }
                        _RESOLVER_STATS["done"] = int(_RESOLVER_STATS.get("done", 0)) + 1
                        _record_resolver_latency(job_id)
                    else:
                        with _RESOLVE_JOBS_LOCK:
                            RESOLVE_JOBS[job_id] = {
                                "status": "error",
                                "stage": "error",
                                "job_type": "profile_photo",
                                "chat_id": chat_id,
                                "error": "Нет фото",
                            }
                        _RESOLVER_STATS["error"] = int(_RESOLVER_STATS.get("error", 0)) + 1
                except Exception as e:
                    if _is_sqlite_locked_error(e):
                        if _job_deadline_exceeded(job_id):
                            _set_deadline_exceeded(job_id)
                            continue
                        with _RESOLVE_JOBS_LOCK:
                            if job_id in RESOLVE_JOBS:
                                RESOLVE_JOBS[job_id]["stage"] = "retrying"
                                RESOLVE_JOBS[job_id]["retries"] = int(RESOLVE_JOBS[job_id].get("retries") or 0) + 1
                        _RESOLVER_STATS["retries"] = int(_RESOLVER_STATS.get("retries", 0)) + 1
                        _enqueue_resolver_task(item)
                        time.sleep(0.15)
                        continue
                    logger.exception("Резолв profile_photo_job chat_id %s: %s", chat_id, e)
                    with _RESOLVE_JOBS_LOCK:
                        RESOLVE_JOBS[job_id] = {
                            "status": "error",
                            "stage": "error",
                            "job_type": "profile_photo",
                            "chat_id": chat_id,
                            "error": str(e),
                        }
                    _RESOLVER_STATS["error"] = int(_RESOLVER_STATS.get("error", 0)) + 1
                finally:
                    if client:
                        try:
                            async def _disconnect():
                                await client.disconnect()
                            loop.run_until_complete(_disconnect())
                        except Exception:
                            pass
            elif task_type == "full_profile_job":
                job_id, _, chat_id, base = item
                client = None
                try:
                    from telethon.tl.types import Channel
                    from telethon.tl.functions import channels, messages

                    cm = ConfigManager()
                    config = cm.load()
                    api_id = config.get("api_id")
                    api_hash = config.get("api_hash")
                    if not api_id or not api_hash:
                        with _RESOLVE_JOBS_LOCK:
                            RESOLVE_JOBS[job_id] = {
                                "status": "error",
                                "job_type": "full_profile",
                                "chat_id": chat_id,
                                "error": "В конфиге не заданы api_id/api_hash",
                                **(base or {}),
                            }
                        continue
                    proxy_config = get_proxy_config(config)
                    download_settings = config.get("download_settings") or {}
                    request_retries = download_settings.get("request_retries", 15)
                    session_name = "media_downloader"
                    client = TelegramClient(
                        session_name,
                        api_id=api_id,
                        api_hash=api_hash,
                        proxy=proxy_config,
                        device_model=DEVICE_MODEL,
                        system_version=SYSTEM_VERSION,
                        app_version=APP_VERSION,
                        lang_code=LANG_CODE,
                        request_retries=request_retries,
                    )

                    async def _get_full_profile_job():
                        await _resolver_ensure_authorized(client)
                        entity = await client.get_entity(chat_id)
                        out = {
                            "about": "",
                            "participants_count": None,
                            "admins_count": None,
                            "kicked_count": None,
                            "banned_count": None,
                            "linked_chat_id": None,
                            "invite_link": None,
                            "pinned_msg_id": None,
                            "online_count": None,
                        }
                        from telethon.tl.types import Chat
                        if isinstance(entity, Channel):
                            full = await client(channels.GetFullChannelRequest(channel=entity))
                            fc = full.full_chat
                            out["about"] = (getattr(fc, "about", None) or "").strip()
                            out["participants_count"] = getattr(fc, "participants_count", None)
                            out["admins_count"] = getattr(fc, "admins_count", None)
                            out["kicked_count"] = getattr(fc, "kicked_count", None)
                            out["banned_count"] = getattr(fc, "banned_count", None)
                            out["linked_chat_id"] = getattr(fc, "linked_chat_id", None)
                            out["pinned_msg_id"] = getattr(fc, "pinned_msg_id", None)
                            out["online_count"] = getattr(fc, "online_count", None)
                            ei = getattr(fc, "exported_invite", None)
                            if ei is not None and getattr(ei, "link", None):
                                out["invite_link"] = (ei.link or "").strip()
                        elif isinstance(entity, Chat):
                            full = await client(messages.GetFullChatRequest(chat_id=entity.id))
                            fc = full.full_chat
                            out["about"] = (getattr(fc, "about", None) or "").strip()
                            out["pinned_msg_id"] = getattr(fc, "pinned_msg_id", None)
                            participants = getattr(fc, "participants", None)
                            if participants is not None:
                                out["participants_count"] = getattr(participants, "participants_count", None) or len(getattr(participants, "participants", []))
                            ei = getattr(fc, "exported_invite", None)
                            if ei is not None and getattr(ei, "link", None):
                                out["invite_link"] = (ei.link or "").strip()
                        return out

                    live = loop.run_until_complete(_get_full_profile_job())
                    with _RESOLVE_JOBS_LOCK:
                        RESOLVE_JOBS[job_id] = {
                            "status": "done",
                            "stage": "done",
                            "job_type": "full_profile",
                            "chat_id": chat_id,
                            **(base or {}),
                            "about": live.get("about") or (base or {}).get("description", ""),
                            "participants_count": live.get("participants_count"),
                            "admins_count": live.get("admins_count"),
                            "kicked_count": live.get("kicked_count"),
                            "banned_count": live.get("banned_count"),
                            "linked_chat_id": live.get("linked_chat_id"),
                            "invite_link": live.get("invite_link"),
                            "pinned_msg_id": live.get("pinned_msg_id"),
                            "online_count": live.get("online_count"),
                        }
                    _RESOLVER_STATS["done"] = int(_RESOLVER_STATS.get("done", 0)) + 1
                    _record_resolver_latency(job_id)
                except Exception as e:
                    if _is_sqlite_locked_error(e):
                        if _job_deadline_exceeded(job_id):
                            _set_deadline_exceeded(job_id)
                            continue
                        with _RESOLVE_JOBS_LOCK:
                            if job_id in RESOLVE_JOBS:
                                RESOLVE_JOBS[job_id]["stage"] = "retrying"
                                RESOLVE_JOBS[job_id]["retries"] = int(RESOLVE_JOBS[job_id].get("retries") or 0) + 1
                        _RESOLVER_STATS["retries"] = int(_RESOLVER_STATS.get("retries", 0)) + 1
                        _enqueue_resolver_task(item)
                        time.sleep(0.15)
                        continue
                    logger.exception("Резолв full_profile_job chat_id %s: %s", chat_id, e)
                    with _RESOLVE_JOBS_LOCK:
                        RESOLVE_JOBS[job_id] = {
                            "status": "error",
                            "stage": "error",
                            "job_type": "full_profile",
                            "chat_id": chat_id,
                            "error": str(e),
                            **(base or {}),
                        }
                    _RESOLVER_STATS["error"] = int(_RESOLVER_STATS.get("error", 0)) + 1
                finally:
                    if client:
                        try:
                            async def _disconnect():
                                await client.disconnect()
                            loop.run_until_complete(_disconnect())
                        except Exception:
                            pass
            elif task_type == "profile_photo":
                chat_id, result_holder = item[1], item[2]
                client = None
                try:
                    from io import BytesIO
                    cm = ConfigManager()
                    config = cm.load()
                    api_id = config.get("api_id")
                    api_hash = config.get("api_hash")
                    if not api_id or not api_hash:
                        result_holder["error"] = "В конфиге не заданы api_id/api_hash"
                        result_holder["event"].set()
                        continue
                    proxy_config = get_proxy_config(config)
                    download_settings = config.get("download_settings") or {}
                    request_retries = download_settings.get("request_retries", 15)
                    session_name = "media_downloader"
                    client = TelegramClient(
                        session_name,
                        api_id=api_id,
                        api_hash=api_hash,
                        proxy=proxy_config,
                        device_model=DEVICE_MODEL,
                        system_version=SYSTEM_VERSION,
                        app_version=APP_VERSION,
                        lang_code=LANG_CODE,
                        request_retries=request_retries,
                    )

                    async def _get_profile_photo():
                        await _resolver_ensure_authorized(client)
                        entity = await client.get_entity(chat_id)
                        buf = BytesIO()
                        await client.download_profile_photo(entity, file=buf)
                        return buf.getvalue()

                    photo_bytes = loop.run_until_complete(_get_profile_photo())
                    if photo_bytes:
                        result_holder["result"] = photo_bytes
                    else:
                        result_holder["error"] = "Нет фото"
                    result_holder["event"].set()
                except Exception as e:
                    logger.exception("Резолв profile_photo chat_id %s: %s", chat_id, e)
                    result_holder["error"] = str(e)
                    result_holder["event"].set()
                finally:
                    if client:
                        try:
                            async def _disconnect():
                                await client.disconnect()
                            loop.run_until_complete(_disconnect())
                        except Exception:
                            pass
            elif task_type == "full_profile":
                chat_id, result_holder = item[1], item[2]
                client = None
                try:
                    from telethon.tl.types import Channel
                    from telethon.tl.functions import channels, messages

                    cm = ConfigManager()
                    config = cm.load()
                    api_id = config.get("api_id")
                    api_hash = config.get("api_hash")
                    if not api_id or not api_hash:
                        result_holder["error"] = "В конфиге не заданы api_id/api_hash"
                        result_holder["event"].set()
                        continue
                    proxy_config = get_proxy_config(config)
                    download_settings = config.get("download_settings") or {}
                    request_retries = download_settings.get("request_retries", 15)
                    session_name = "media_downloader"
                    client = TelegramClient(
                        session_name,
                        api_id=api_id,
                        api_hash=api_hash,
                        proxy=proxy_config,
                        device_model=DEVICE_MODEL,
                        system_version=SYSTEM_VERSION,
                        app_version=APP_VERSION,
                        lang_code=LANG_CODE,
                        request_retries=request_retries,
                    )

                    async def _get_full_profile():
                        await _resolver_ensure_authorized(client)
                        entity = await client.get_entity(chat_id)
                        out = {
                            "about": "",
                            "participants_count": None,
                            "admins_count": None,
                            "kicked_count": None,
                            "banned_count": None,
                            "linked_chat_id": None,
                            "invite_link": None,
                            "pinned_msg_id": None,
                            "online_count": None,
                        }
                        from telethon.tl.types import Chat
                        if isinstance(entity, Channel):
                            full = await client(channels.GetFullChannelRequest(channel=entity))
                            fc = full.full_chat
                            out["about"] = (getattr(fc, "about", None) or "").strip()
                            out["participants_count"] = getattr(fc, "participants_count", None)
                            out["admins_count"] = getattr(fc, "admins_count", None)
                            out["kicked_count"] = getattr(fc, "kicked_count", None)
                            out["banned_count"] = getattr(fc, "banned_count", None)
                            out["linked_chat_id"] = getattr(fc, "linked_chat_id", None)
                            out["pinned_msg_id"] = getattr(fc, "pinned_msg_id", None)
                            out["online_count"] = getattr(fc, "online_count", None)
                            ei = getattr(fc, "exported_invite", None)
                            if ei is not None and getattr(ei, "link", None):
                                out["invite_link"] = (ei.link or "").strip()
                        elif isinstance(entity, Chat):
                            full = await client(messages.GetFullChatRequest(chat_id=entity.id))
                            fc = full.full_chat
                            out["about"] = (getattr(fc, "about", None) or "").strip()
                            out["pinned_msg_id"] = getattr(fc, "pinned_msg_id", None)
                            participants = getattr(fc, "participants", None)
                            if participants is not None:
                                out["participants_count"] = getattr(participants, "participants_count", None) or len(getattr(participants, "participants", []))
                            ei = getattr(fc, "exported_invite", None)
                            if ei is not None and getattr(ei, "link", None):
                                out["invite_link"] = (ei.link or "").strip()
                        return out

                    data = loop.run_until_complete(_get_full_profile())
                    result_holder["result"] = data
                    result_holder["event"].set()
                except Exception as e:
                    logger.exception("Резолв full_profile chat_id %s: %s", chat_id, e)
                    result_holder["error"] = str(e)
                    result_holder["event"].set()
                finally:
                    if client:
                        try:
                            async def _disconnect():
                                await client.disconnect()
                            loop.run_until_complete(_disconnect())
                        except Exception:
                            pass
        except Empty:
            continue
        except Exception as e:
            logger.exception("Резолвер: %s", e)


def _apply_resolved_chat(job_id: str, chat_id: int, title: str, username: Optional[str]) -> None:
    """Добавить резолвленный чат в ClickHouse и/или config (вызывать из main thread).

    Если username=None — только обновление title (чат уже есть в БД, сохраняем текущие message_count/total_size).
    Если username задан — полное добавление чата.
    """
    try:
        from utils.config import ConfigManager
        cm = ConfigManager()
        config = cm.load()
        ch_config = config.get("clickhouse", {})
        if ch_config.get("enabled"):
            from utils.clickhouse_db import ClickHouseMetadataDB
            db = ClickHouseMetadataDB(ch_config)
            if username is None:
                # Обновление только title — получить текущие message_count и total_size из БД
                try:
                    client = db._get_client()
                    rows = client.execute(
                        "SELECT message_count, total_size FROM chats WHERE chat_id = %(chat_id)s LIMIT 1",
                        {"chat_id": chat_id},
                    )
                    if rows:
                        msg_count = int(rows[0][0]) if rows[0][0] else 0
                        total_sz = int(rows[0][1]) if len(rows[0]) > 1 and rows[0][1] else 0
                    else:
                        msg_count, total_sz = 0, 0
                except Exception:
                    msg_count, total_sz = 0, 0
            else:
                msg_count, total_sz = 0, 0
            # Обновляем через INSERT в ReplacingMergeTree (сохраняем текущие счетчики при обновлении title)
            db._insert_chat(chat_id, title, msg_count, total_sz, "", username or "")
        # Если username задан — добавляем в config (новый чат)
        if username:
            try:
                cm.add_chat_to_download_list(chat_id, title)
                cm.save()
            except Exception as e:
                logger.warning("Не удалось добавить чат в config: %s", e)
    except Exception as e:
        logger.exception("apply_resolved_chat: %s", e)


class AddChatRequest(BaseModel):
    """Тело запроса POST /api/chats/add."""
    chat_id: Optional[int] = None
    title: Optional[str] = None
    username: Optional[str] = None


class ResolveRequest(BaseModel):
    """Тело запроса POST /api/chats/resolve."""
    username: Optional[str] = None
    chat_id: Optional[int] = None
    title: Optional[str] = None


@app.post("/api/chats/resolve")
async def resolve_username(body: ResolveRequest):
    """Запустить фоновый резолв username → chat_id или chat_id → title. Возвращает job_id для опроса статуса."""
    from fastapi import HTTPException
    from utils.config import ConfigManager
    if body.username:
        config = ConfigManager().load()
        auto_add_enabled = bool((config.get("download_settings") or {}).get("auto_add_chats_from_links", False))
        if not auto_add_enabled:
            raise HTTPException(status_code=403, detail="auto_add_chats_from_links disabled")
        username = (body.username or "").strip().lstrip("@")
        if not username:
            raise HTTPException(status_code=400, detail="username обязателен")
        job_id = _create_resolve_job({
            "username": username,
            "title": body.title or f"@{username}",
            "job_type": "resolve_username",
        })
        _enqueue_resolver_task(("username", job_id, username, body.title or f"@{username}"))
    elif body.chat_id:
        job_id = _create_resolve_job({
            "chat_id": body.chat_id,
            "job_type": "resolve_chat_id",
        })
        _enqueue_resolver_task(("chat_id", job_id, body.chat_id))
    else:
        raise HTTPException(status_code=400, detail="Укажите username или chat_id")
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/chats/resolve/{job_id}")
async def resolve_status(job_id: str):
    """Статус задачи резолва: pending | done | error."""
    with _RESOLVE_JOBS_LOCK:
        job = RESOLVE_JOBS.get(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/chats/resolve-metrics")
async def resolve_metrics():
    """Метрики резолвера для SLA-мониторинга."""
    with _RESOLVE_JOBS_LOCK:
        jobs = list(RESOLVE_JOBS.values())
    done_latencies_ms = []
    counts = {"pending": 0, "done": 0, "error": 0, "deadline_exceeded": 0}
    for job in jobs:
        status = str(job.get("status") or "pending")
        if status in counts:
            counts[status] += 1
        created = job.get("created_at")
        started = job.get("started_at")
        if status == "done" and created and started:
            done_latencies_ms.append(max(0.0, (float(started) - float(created)) * 1000.0))
    avg_latency_ms = (sum(done_latencies_ms) / len(done_latencies_ms)) if done_latencies_ms else 0.0
    return {
        "queue_depth": _RESOLVER_PRIORITY_QUEUE.qsize(),
        "jobs_total": len(jobs),
        "counts": counts,
        "avg_schedule_latency_ms": round(avg_latency_ms, 2),
        "worker_stats": _RESOLVER_STATS,
    }


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
        # Если ClickHouse включен - добавить в БД, иначе в config.yaml
        ch_config = config.get("clickhouse", {})
        if ch_config.get("enabled"):
            from utils.clickhouse_db import ClickHouseMetadataDB
            db = ClickHouseMetadataDB(ch_config)

            # Проверить, есть ли чат в БД
            loop = asyncio.get_event_loop()
            exists = await loop.run_in_executor(None, lambda: db.chat_exists(chat_id))

            if not exists:
                # Добавить чат в БД
                await loop.run_in_executor(
                    None,
                    lambda: db.ensure_chat_in_db(chat_id, body.title or body.username or "")
                )
                return {"added": True, "message": "Чат добавлен в БД"}
            else:
                return {"added": False, "message": "Чат уже в БД"}
        else:
            # Fallback: добавить в config.yaml
            added = cm.add_chat_to_download_list(chat_id, body.title or body.username)
            cm.save()
            return {"added": added, "message": "Чат добавлен в config" if added else "Чат уже в config"}
    except Exception as e:
        return {"added": False, "message": str(e)}


@app.get("/api/settings")
async def get_settings():
    """Публичные настройки для фронта (например open_file_manager)."""
    from utils.config import ConfigManager
    config = ConfigManager().load()
    web_cfg = config.get("web") or {}
    download_settings = config.get("download_settings") or {}
    return {
        "open_file_manager": bool(web_cfg.get("open_file_manager")),
        "auto_add_chats_from_links": bool(download_settings.get("auto_add_chats_from_links", False)),
    }


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
