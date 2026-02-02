# Предложения по улучшению Telegram Media Downloader

## 📊 Анализ текущего состояния

### ✅ Сильные стороны проекта

1. **Отличная архитектура**
   - Чистое разделение на модули (`core`, `utils`)
   - Хорошее покрытие тестами (27 тестов)
   - Минимальное дублирование кода

2. **Rich Feature Set**
   - TUI интерфейс с навигацией
   - Параллельные загрузки
   - История сообщений (JSON/TXT/HTML)
   - Валидация файлов и архивов
   - Прокси поддержка
   - Фильтры (дата, отправитель, формат)

3. **User Experience**
   - Интерактивный выбор чатов
   - Русификация интерфейса
   - Детальное логирование
   - Утилиты (export, cleanup, rebuild)

### ⚠️ Области для улучшения

#### 1. Производительность
- Отсутствует индикация прогресса для больших объёмов
- Нет оптимизации для очень больших чатов (100k+ сообщений)
- Отсутствует сжатие архивов

#### 2. Функциональность
- Нет поиска по истории сообщений
- Отсутствует экспорт в другие форматы (PDF, EPUB)
- Нет бэкапа конфигурации
- Отсутствует планировщик задач

#### 3. User Experience
- Отсутствует GUI (только CLI/TUI)
- Нет уведомлений о завершении загрузки
- Отсутствует статистика загрузок

---

## 🚀 Предложения по улучшениям

### Приоритет 1: Критично важные

#### 1.1 Resumable Downloads (возобновляемые загрузки)

**Проблема**: При обрыве загрузки большого файла приходится начинать заново.

**Решение**: Реализовать поддержку частичной загрузки:

```python
# utils/resumable_download.py
class ResumableDownloader:
    def __init__(self, client, cache_dir=\".download_cache\"):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

    async def download_with_resume(self, message, target_path):
        \"\"\"Загрузить файл с возможностью возобновления.\"\"\"
        cache_file = self.cache_dir / f\"{message.id}.part\"
        offset = 0

        if cache_file.exists():
            offset = cache_file.stat().st_size
            logger.info(f\"Возобновление загрузки с {offset} bytes\")

        # Telethon поддерживает offset для download_media
        await self.client.download_media(
            message,
            file=cache_file,
            offset=offset
        )

        # После успешной загрузки переместить в целевую папку
        cache_file.rename(target_path)
```

**Конфиг:**
```yaml
download_settings:
  resumable_downloads: true
  cache_directory: \".download_cache\"
  auto_cleanup_cache: true  # Очищать кэш после успешной загрузки
```

**Преимущества:**
- ⏱️ Экономия времени при обрывах
- 💾 Экономия трафика
- 🔄 Надёжность для медленных/нестабильных соединений

---

#### 1.2 Database Backend для метаданных

**Проблема**: Хранение метаданных в YAML и JSONL медленно для больших объёмов (100k+ сообщений).

**Решение**: Выбор database backend в зависимости от масштаба:

##### Вариант A: SQLite (для малых/средних объёмов до 1M сообщений)

```python
# utils/metadata_db.py
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict

class SQLiteMetadataDB:
    def __init__(self, db_path="metadata.db"):
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER,
                message_id INTEGER PRIMARY KEY,
                date TIMESTAMP,
                text TEXT,
                media_type TEXT,
                file_path TEXT,
                file_size INTEGER,
                downloaded BOOLEAN DEFAULT 0,
                download_date TIMESTAMP,
                sender_id INTEGER
            )
        ''')

        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                last_sync TIMESTAMP,
                message_count INTEGER,
                total_size INTEGER
            )
        ''')

        # Критичные индексы для производительности
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_messages ON messages(chat_id)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_message_date ON messages(date)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_downloaded ON messages(downloaded)')

        # Full-text search индекс
        self.conn.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(message_id, text, content=messages)
        ''')
        self.conn.commit()

    def search_messages(self, query: str, chat_id: Optional[int] = None) -> List[Dict]:
        """Full-text search по сообщениям."""
        if chat_id:
            sql = '''
                SELECT m.* FROM messages m
                JOIN messages_fts fts ON m.message_id = fts.message_id
                WHERE messages_fts MATCH ? AND m.chat_id = ?
            '''
            cursor = self.conn.execute(sql, (query, chat_id))
        else:
            sql = '''
                SELECT m.* FROM messages m
                JOIN messages_fts fts ON m.message_id = fts.message_id
                WHERE messages_fts MATCH ?
            '''
            cursor = self.conn.execute(sql, (query,))

        return [dict(zip([col[0] for col in cursor.description], row))
                for row in cursor.fetchall()]
```

**Преимущества SQLite:**
- ✅ Не требует отдельного сервера
- ✅ Встроенная поддержка FTS5 для полнотекстового поиска
- ✅ Достаточно для большинства случаев (до 1M сообщений)
- ✅ Zero-configuration

**Ограничения:**
- ❌ Замедление при 1M+ записей
- ❌ Нет параллельных записей
- ❌ Ограничения по размеру БД (зависит от файловой системы)

---

##### Вариант B: PostgreSQL (для больших объёмов 1M-100M сообщений)

```python
# utils/postgres_db.py
import asyncpg
from datetime import datetime
from typing import Optional, List, Dict

class PostgreSQLMetadataDB:
    def __init__(self, dsn="postgresql://user:password@localhost/telegram_media"):
        self.dsn = dsn
        self.pool = None

    async def connect(self):
        """Создать пул соединений."""
        self.pool = await asyncpg.create_pool(
            self.dsn,
            min_size=5,
            max_size=20
        )
        await self._init_schema()

    async def _init_schema(self):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    chat_id BIGINT,
                    message_id BIGINT PRIMARY KEY,
                    date TIMESTAMP WITH TIME ZONE,
                    text TEXT,
                    media_type VARCHAR(50),
                    file_path TEXT,
                    file_size BIGINT,
                    downloaded BOOLEAN DEFAULT FALSE,
                    download_date TIMESTAMP WITH TIME ZONE,
                    sender_id BIGINT
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id BIGINT PRIMARY KEY,
                    title TEXT,
                    last_sync TIMESTAMP WITH TIME ZONE,
                    message_count INTEGER,
                    total_size BIGINT
                )
            ''')

            # Оптимизированные индексы
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_messages ON messages(chat_id, date DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_downloaded ON messages(downloaded) WHERE downloaded = FALSE')

            # Full-text search с русским языком
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_messages_text_search
                ON messages USING gin(to_tsvector('russian', text))
            ''')

    async def search_messages(self, query: str, chat_id: Optional[int] = None) -> List[Dict]:
        """Full-text search с поддержкой русского языка."""
        async with self.pool.acquire() as conn:
            if chat_id:
                rows = await conn.fetch('''
                    SELECT * FROM messages
                    WHERE to_tsvector('russian', text) @@ plainto_tsquery('russian', $1)
                    AND chat_id = $2
                    ORDER BY date DESC
                    LIMIT 1000
                ''', query, chat_id)
            else:
                rows = await conn.fetch('''
                    SELECT * FROM messages
                    WHERE to_tsvector('russian', text) @@ plainto_tsquery('russian', $1)
                    ORDER BY date DESC
                    LIMIT 1000
                ''', query)

            return [dict(row) for row in rows]

    async def get_chat_statistics(self, chat_id: int) -> Dict:
        """Агрегированная статистика по чату."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT
                    COUNT(*) as total_messages,
                    COUNT(*) FILTER (WHERE downloaded) as downloaded_count,
                    COALESCE(SUM(file_size), 0) as total_size,
                    MIN(date) as first_message,
                    MAX(date) as last_message
                FROM messages
                WHERE chat_id = $1
            ''', chat_id)

            return dict(row)

    async def bulk_insert_messages(self, messages: List[Dict]):
        """Массовая вставка для производительности."""
        async with self.pool.acquire() as conn:
            await conn.copy_records_to_table(
                'messages',
                records=messages,
                columns=['chat_id', 'message_id', 'date', 'text', 'media_type', 'sender_id']
            )
```

**Преимущества PostgreSQL:**
- ✅ **Отличная производительность** для 1M-100M записей
- ✅ **Full-text search** с поддержкой русского языка
- ✅ **JSONB** для хранения сложных структур
- ✅ **Параллельные запросы** и запись
- ✅ **Репликация** для надёжности
- ✅ **Партиционирование** таблиц по chat_id для ускорения

**Недостатки:**
- ❌ Требует отдельный сервер
- ❌ Сложнее настройка и обслуживание

---

##### Вариант C: ClickHouse (для огромных объёмов 100M+ сообщений и аналитики)

```python
# utils/clickhouse_db.py
from clickhouse_driver import Client

class ClickHouseMetadataDB:
    def __init__(self, host='localhost', port=9000):
        self.client = Client(host=host, port=port)
        self._init_schema()

    def _init_schema(self):
        self.client.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                chat_id Int64,
                message_id Int64,
                date DateTime,
                text String,
                media_type LowCardinality(String),
                file_path String,
                file_size UInt64,
                downloaded UInt8,
                download_date DateTime,
                sender_id Int64,
                INDEX idx_text text TYPE tokenbf_v1(10240, 3, 0) GRANULARITY 1
            ) ENGINE = MergeTree()
            PARTITION BY toYYYYMM(date)
            ORDER BY (chat_id, date, message_id)
        ''')

        self.client.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id Int64,
                title String,
                last_sync DateTime,
                message_count UInt32,
                total_size UInt64
            ) ENGINE = ReplacingMergeTree()
            ORDER BY chat_id
        ''')

    def search_messages(self, query: str, chat_id=None):
        """Полнотекстовый поиск."""
        if chat_id:
            return self.client.execute('''
                SELECT * FROM messages
                WHERE hasToken(text, %(query)s) AND chat_id = %(chat_id)s
                ORDER BY date DESC
                LIMIT 1000
            ''', {'query': query, 'chat_id': chat_id})
        else:
            return self.client.execute('''
                SELECT * FROM messages
                WHERE hasToken(text, %(query)s)
                ORDER BY date DESC
                LIMIT 1000
            ''', {'query': query})

    def get_analytics(self, chat_id: int):
        """Аналитика по чату."""
        return self.client.execute('''
            SELECT
                toStartOfDay(date) as day,
                count() as messages_count,
                uniqExact(sender_id) as unique_senders,
                sum(file_size) as total_size,
                countIf(media_type != '') as media_count
            FROM messages
            WHERE chat_id = %(chat_id)s
            GROUP BY day
            ORDER BY day DESC
            LIMIT 365
        ''', {'chat_id': chat_id})
```

**Преимущества ClickHouse:**
- ✅ **Экстремальная производительность** для аналитических запросов
- ✅ **Колоночное хранение** — меньше места на диске
- ✅ **Автоматическое партиционирование** по датам
- ✅ **Сжатие данных** (в 5-10 раз меньше места чем PostgreSQL)
- ✅ **Идеально для больших объёмов** (миллиарды записей)

**Недостатки:**
- ❌ **Не для транзакционных операций**
- ❌ Сложная настройка
- ❌ Требует больше RAM

---

##### Рекомендации по выбору

| Объём данных | База данных | Причина |
|--------------|-------------|---------|
| < 1M сообщений | **SQLite** | Простота, не нужен сервер |
| 1M - 100M сообщений | **PostgreSQL** | Баланс производительности и функциональности |
| > 100M сообщений | **ClickHouse** | Максимальная производительность для аналитики |

**Конфиг:**
```yaml
download_settings:
  use_database: true
  database_backend: postgresql  # sqlite, postgresql, clickhouse

  # SQLite
  sqlite:
    database_path: "metadata.db"

  # PostgreSQL
  postgresql:
    host: localhost
    port: 5432
    database: telegram_media
    username: user
    password: password
    pool_size: 10

  # ClickHouse
  clickhouse:
    host: localhost
    port: 9000
    database: default

  keep_jsonl_backup: true  # Дублировать в JSONL для совместимости
```

**Преимущества над текущим решением:**
- 🚀 **10-100x ускорение** для поиска
- 📊 **Мощная аналитика** (статистика, графики)
- 🔍 **Full-text search** с ранжированием
- 📈 **Масштабируемость** до миллиардов записей

```python
# utils/metadata_db.py
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict

class MetadataDB:
    def __init__(self, db_path=\"metadata.db\"):
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER,
                message_id INTEGER PRIMARY KEY,
                date TIMESTAMP,
                text TEXT,
                media_type TEXT,
                file_path TEXT,
                file_size INTEGER,
                downloaded BOOLEAN DEFAULT 0,
                download_date TIMESTAMP,
                sender_id INTEGER
            )
        ''')

        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                last_sync TIMESTAMP,
                message_count INTEGER,
                total_size INTEGER
            )
        ''')

        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_messages ON messages(chat_id)')
        self.conn.commit()

    def add_message(self, chat_id: int, message_data: Dict):
        \"\"\"Добавить сообщение в базу.\"\"\"
        self.conn.execute('''
            INSERT OR REPLACE INTO messages
            (chat_id, message_id, date, text, media_type, sender_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            chat_id,
            message_data['id'],
            message_data['date'],
            message_data.get('text'),
            message_data.get('media_type'),
            message_data.get('sender_id')
        ))
        self.conn.commit()

    def mark_downloaded(self, message_id: int, file_path: str, file_size: int):
        \"\"\"Отметить файл как загруженный.\"\"\"
        self.conn.execute('''
            UPDATE messages
            SET downloaded = 1, file_path = ?, file_size = ?, download_date = ?
            WHERE message_id = ?
        ''', (file_path, file_size, datetime.now(), message_id))
        self.conn.commit()

    def search_messages(self, query: str, chat_id: Optional[int] = None) -> List[Dict]:
        \"\"\"Поиск по тексту сообщений.\"\"\"
        sql = 'SELECT * FROM messages WHERE text LIKE ?'
        params = [f'%{query}%']

        if chat_id:
            sql += ' AND chat_id = ?'
            params.append(chat_id)

        cursor = self.conn.execute(sql, params)
        return [dict(zip([col[0] for col in cursor.description], row))
                for row in cursor.fetchall()]

    def get_chat_statistics(self, chat_id: int) -> Dict:
        \"\"\"Статистика по чату.\"\"\"
        cursor = self.conn.execute('''
            SELECT
                COUNT(*) as total_messages,
                SUM(CASE WHEN downloaded THEN 1 ELSE 0 END) as downloaded_count,
                SUM(file_size) as total_size
            FROM messages
            WHERE chat_id = ?
        ''', (chat_id,))

        row = cursor.fetchone()
        return {
            'total_messages': row[0],
            'downloaded_count': row[1],
            'total_size': row[2] or 0
        }
```

**Конфиг:**
```yaml
download_settings:
  use_database: true
  database_path: \"metadata.db\"
  keep_jsonl_backup: true  # Дублировать в JSONL для совместимости
```

**Преимущества:**
- 🚀 **Быстрый поиск** по истории (SQL индексы)
- 📊 **Статистика** по чатам
- 🔍 **Full-text search** по сообщениям
- 📈 **Масштабируемость** для миллионов сообщений

---

#### 1.3 Прогресс для множественных чатов

**Проблема**: При загрузке нескольких чатов нет общего прогресса.

**Решение**: Rich progress с множественными задачами:

```python
# core/downloader.py
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

class DownloadManager:
    async def begin_import_chats(self, client, chat_queue: List[Tuple[int, str]], pagination_limit: int):
        \"\"\"Загрузка нескольких чатов с общим прогрессом.\"\"\"

        with Progress(
            SpinnerColumn(),
            TextColumn(\"[progress.description]{task.description}\"),
            BarColumn(),
            TextColumn(\"[progress.percentage]{task.percentage:>3.0f}%\"),
            TimeElapsedColumn(),
        ) as progress:

            # Общая задача
            overall_task = progress.add_task(
                f\"[cyan]Обработка {len(chat_queue)} чатов\",
                total=len(chat_queue)
            )

            for chat_id, chat_title in chat_queue:
                # Задача для текущего чата
                chat_task = progress.add_task(
                    f\"[green]{chat_title or chat_id}\",
                    total=None
                )

                try:
                    await self.begin_import_chat(
                        client, chat_id, chat_title, pagination_limit,
                        progress=progress, task_id=chat_task
                    )
                except Exception as e:
                    logger.error(f\"Ошибка при загрузке {chat_title}: {e}\")
                finally:
                    progress.update(chat_task, visible=False)
                    progress.advance(overall_task)
```

**Преимущества:**
- 👁️ Видимость общего прогресса
- ⏱️ Оценка времени завершения
- 📊 Статус каждого чата

---

### Приоритет 2: Важные улучшения

#### 2.1 Web Dashboard (веб-интерфейс)

**Решение**: FastAPI + Vue.js дашборд для мониторинга и управления:

```python
# web_dashboard.py
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import asyncio

app = FastAPI()

class DownloadDashboard:
    def __init__(self):
        self.active_downloads = {}
        self.stats = {}

@app.get(\"/\")
async def dashboard():
    return HTMLResponse(open(\"dashboard/index.html\").read())

@app.get(\"/api/chats\")
async def get_chats():
    \"\"\"Список чатов с статистикой.\"\"\"
    return {
        \"chats\": [
            {
                \"id\": chat_id,
                \"title\": title,
                \"messages\": count,
                \"downloaded\": downloaded,
                \"size\": size
            }
            for chat_id, (title, count, downloaded, size) in chats_data.items()
        ]
    }

@app.websocket(\"/ws/progress\")
async def websocket_progress(websocket: WebSocket):
    \"\"\"WebSocket для реального времени прогресса.\"\"\"
    await websocket.accept()

    while True:
        # Отправлять обновления прогресса
        progress_data = get_current_progress()
        await websocket.send_json(progress_data)
        await asyncio.sleep(1)

@app.post(\"/api/download/start\")
async def start_download(chat_id: int):
    \"\"\"Запустить загрузку чата.\"\"\"
    # Запустить задачу загрузки
    task = asyncio.create_task(download_chat(chat_id))
    return {\"status\": \"started\", \"task_id\": id(task)}

@app.post(\"/api/download/pause\")
async def pause_download(chat_id: int):
    \"\"\"Приостановить загрузку.\"\"\"
    # Реализация паузы
    pass

# Запуск: uvicorn web_dashboard:app --reload
```

**JavaScript (dashboard/index.html):**
```html
<script src=\"https://cdn.jsdelivr.net/npm/vue@3\"></script>
<script src=\"https://cdn.jsdelivr.net/npm/chart.js\"></script>

<script>
const { createApp } = Vue;

createApp({
  data() {
    return {
      chats: [],
      progress: {},
      ws: null
    }
  },
  mounted() {
    this.fetchChats();
    this.connectWebSocket();
  },
  methods: {
    async fetchChats() {
      const response = await fetch('/api/chats');
      this.chats = await response.json();
    },
    connectWebSocket() {
      this.ws = new WebSocket('ws://localhost:8000/ws/progress');
      this.ws.onmessage = (event) => {
        this.progress = JSON.parse(event.data);
      };
    },
    async startDownload(chatId) {
      await fetch('/api/download/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({chat_id: chatId})
      });
    }
  }
}).mount('#app');
</script>
```

**Возможности:**
- 📊 Дашборд с статистикой
- 🔴 Управление загрузками (старт/пауза/стоп)
- 📈 Графики использования места
- 🔍 Поиск по истории
- 📱 Адаптивный интерфейс

---

#### 2.2 Планировщик загрузок

**Решение**: Cron-подобный планировщик для автоматических загрузок:

```python
# utils/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import time

class DownloadScheduler:
    def __init__(self, config_manager, download_manager):
        self.config = config_manager
        self.downloader = download_manager
        self.scheduler = AsyncIOScheduler()

    def setup_schedules(self):
        \"\"\"Настроить расписания из конфига.\"\"\"
        schedules = self.config.config.get('schedules', [])

        for schedule in schedules:
            if schedule['type'] == 'daily':
                self.scheduler.add_job(
                    self.run_download,
                    'cron',
                    hour=schedule['hour'],
                    minute=schedule['minute'],
                    args=[schedule['chats']]
                )
            elif schedule['type'] == 'interval':
                self.scheduler.add_job(
                    self.run_download,
                    'interval',
                    hours=schedule['hours'],
                    args=[schedule['chats']]
                )

    async def run_download(self, chat_ids):
        \"\"\"Запустить загрузку по расписанию.\"\"\"
        logger.info(f\"Запуск запланированной загрузки: {chat_ids}\")
        # Запуск загрузки
        await self.downloader.download_chats(chat_ids)

    def start(self):
        self.scheduler.start()
```

**Конфиг:**
```yaml
schedules:
  - type: daily
    hour: 2
    minute: 0
    chats: [123456789, 987654321]
    description: \"Ночная загрузка новостных каналов\"

  - type: interval
    hours: 6
    chats: [111111111]
    description: \"Каждые 6 часов проверять обновления\"
```

**Преимущества:**
- ⏰ Автоматические загрузки
- 🌙 Планирование на ночное время
- 🔄 Периодические обновления

---

#### 2.3 Экспорт в дополнительные форматы

**Решение**: Экспорт истории в PDF, EPUB, Markdown:

```python
# utils/exporters.py

class PDFExporter:
    def export_to_pdf(self, chat_id: int, output_path: str):
        \"\"\"Экспорт истории чата в PDF с форматированием.\"\"\"
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet

        doc = SimpleDocTemplate(output_path, pagesize=A4)
        styles = getSampleStyleSheet()
        story = []

        messages = self._load_messages(chat_id)

        for msg in messages:
            # Добавить дату и отправителя
            header = Paragraph(
                f\"{msg['date']} - {msg['sender']}\",
                styles['Heading3']
            )
            story.append(header)

            # Текст сообщения
            if msg['text']:
                text = Paragraph(msg['text'], styles['Normal'])
                story.append(text)

            # Информация о медиа
            if msg['media_type']:
                media = Paragraph(
                    f\"[{msg['media_type'].upper()}]: {msg['file_name']}\",
                    styles['Italic']
                )
                story.append(media)

            story.append(Spacer(1, 12))

        doc.build(story)

class MarkdownExporter:
    def export_to_markdown(self, chat_id: int, output_path: str):
        \"\"\"Экспорт в Markdown для GitHub/Obsidian.\"\"\"
        messages = self._load_messages(chat_id)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f\"# Chat {chat_id}\\n\\n\")

            for msg in messages:
                f.write(f\"## {msg['date']}\\n\\n\")
                f.write(f\"**{msg['sender']}**\\n\\n\")

                if msg['text']:
                    f.write(f\"{msg['text']}\\n\\n\")

                if msg['media_type']:
                    f.write(f\"![{msg['media_type']}]({msg['file_path']})\\n\\n\")

                f.write(\"---\\n\\n\")

class EPUBExporter:
    def export_to_epub(self, chat_id: int, output_path: str):
        \"\"\"Экспорт в EPUB для электронных книг.\"\"\"
        from ebooklib import epub

        book = epub.EpubBook()
        book.set_title(f\"Telegram Chat {chat_id}\")

        messages = self._load_messages(chat_id)

        # Создать главы по дням
        chapters_by_date = {}
        for msg in messages:
            date = msg['date'].date()
            if date not in chapters_by_date:
                chapters_by_date[date] = []
            chapters_by_date[date].append(msg)

        for date, msgs in chapters_by_date.items():
            chapter = epub.EpubHtml(
                title=str(date),
                file_name=f\"chap_{date}.xhtml\"
            )

            content = '<h1>' + str(date) + '</h1>'
            for msg in msgs:
                content += f'<p><strong>{msg[\"sender\"]}</strong>: {msg[\"text\"]}</p>'

            chapter.content = content
            book.add_item(chapter)

        epub.write_epub(output_path, book)
```

**CLI:**
```bash
python export_chat.py --chat-id 123456 --format pdf --output chat.pdf
python export_chat.py --chat-id 123456 --format markdown --output chat.md
python export_chat.py --chat-id 123456 --format epub --output chat.epub
```

**Преимущества:**
- 📄 PDF для архивирования
- 📖 EPUB для чтения на eReader
- 📝 Markdown для интеграции с Obsidian/Notion

---

### Приоритет 3: Nice-to-have

#### 3.1 Уведомления

**Решение**: Desktop notifications при завершении:

```python
# utils/notifications.py
from plyer import notification

def notify_download_complete(chat_title, files_count, total_size):
    notification.notify(
        title='Загрузка завершена',
        message=f\"{chat_title}: {files_count} файлов ({total_size} MB)\",
        app_name='Telegram Media Downloader',
        timeout=10
    )
```

**Конфиг:**
```yaml
notifications:
  enabled: true
  on_chat_complete: true
  on_all_complete: true
  sound: true
```

---

#### 3.3 Telegram Bot Interface

**Решение**: Управление через Telegram бота:

```python
# telegram_bot.py
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

class DownloadBot:
    def __init__(self, bot_token, downloader):
        self.app = Application.builder().token(bot_token).build()
        self.downloader = downloader

    async def start_download(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        \"\"\"Старт загрузки через бот: /download 123456\"\"\"
        chat_id = int(context.args[0])
        await update.message.reply_text(f\"Запускаю загрузку {chat_id}...\")

        # Запустить загрузку в фоне
        asyncio.create_task(self.downloader.download_chat(chat_id))

    async def get_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        \"\"\"Статус загрузок: /status\"\"\"
        stats = self.downloader.get_statistics()
        await update.message.reply_text(
            f\"Активных: {stats['active']}\\n\"
            f\"Завершено: {stats['completed']}\\n\"
            f\"Всего файлов: {stats['total_files']}\"
        )
```

**Возможности:**
- 📱 Удалённое управление загрузками
- 📊 Получение статусов
- 🔔 Уведомления в реальном времени

---

#### 3.4 Cloud Storage Integration

**Решение**: Автоматическая выгрузка в облако:

```python
# utils/cloud_upload.py

class CloudUploader:
    def __init__(self, provider='dropbox'):
        self.provider = provider

    async def upload_to_dropbox(self, local_path, remote_path):
        import dropbox
        dbx = dropbox.Dropbox(os.getenv('DROPBOX_TOKEN'))

        with open(local_path, 'rb') as f:
            dbx.files_upload(f.read(), remote_path)

    async def upload_to_gdrive(self, local_path, folder_id):
        from googleapiclient.discovery import build
        # ... реализация
```

**Конфиг:**
```yaml
cloud_storage:
  enabled: true
  provider: dropbox  # dropbox, gdrive, onedrive
  auto_upload: true
  upload_after_download: true
  keep_local: false  # Удалять локально после выгрузки
```

---

#### 3.5 Duplicate Detection (улучшенная)

**Решение**: Perceptual hashing для изображений:

```python
# utils/deduplication.py
import imagehash
from PIL import Image

class AdvancedDeduplicator:
    def __init__(self):
        self.image_hashes = {}

    def get_image_hash(self, image_path):
        \"\"\"Perceptual hash для обнаружения похожих изображений.\"\"\"
        img = Image.open(image_path)
        return imagehash.average_hash(img)

    def is_duplicate(self, image_path, threshold=5):
        \"\"\"Проверить, является ли изображение дубликатом.\"\"\"
        current_hash = self.get_image_hash(image_path)

        for path, stored_hash in self.image_hashes.items():
            if current_hash - stored_hash < threshold:
                return True, path

        self.image_hashes[image_path] = current_hash
        return False, None
```

**Преимущества:**
- 🖼️ Обнаружение слегка изменённых изображений
- 💾 Экономия места
- 🔍 Более умное удаление дубликатов

---

## 📋 Приоритетная дорожная карта

### Этап 1: Критичные улучшения (1-2 недели)
1. ✅ [DONE] Resumable downloads
2. ✅ [DONE] Прогресс для множественных чатов
3. ✅ Database backend

### Этап 2: Важные фичи (2-4 недели)
4. ✅ Web Dashboard
5. ✅ Планировщик загрузок
6. ✅ Экспорт в PDF/EPUB/MD

### Этап 3: Дополнительно (4-6 недель)
7. ✅ Уведомления
8. ✅ Telegram Bot Interface
9. ✅ Cloud Storage Integration

---

## 🎯 Выводы

**Проект в отличном состоянии:**
- ✅ Чистая архитектура
- ✅ Хорошее покрытие тестами
- ✅ Rich feature set

**Топ-3 рекомендации:**
1. **Database backend (ClickHouse)** — выбран в качестве основного решения для обеспечения максимальной производительности и аналитики.
2. **Web Dashboard** — значительно улучшит UX и позволит удобно мониторить состояние ClickHouse.
3. **Resumable Downloads** — повысит надёжность загрузки больших файлов в хранилище.

**Общая оценка:** 8/10
**Потенциал:** 10/10 с предложенными улучшениями
