# Архитектура Telegram Media Downloader

## 📋 Обзор системы

Telegram Media Downloader — система загрузки медиафайлов и истории сообщений из Telegram с веб-интерфейсом для просмотра.

### Основные компоненты

```
telegram_media_downloader/
├── media_downloader.py      # Главный модуль загрузки
├── utils/
│   ├── config.py            # Управление конфигурацией
│   ├── history.py           # Сохранение и генерация истории
│   ├── file_management.py   # Управление файлами и дубликатами
│   ├── chat_selector.py     # Интерактивный выбор чатов
│   ├── filter.py            # Фильтрация сообщений
│   ├── i18n.py              # Интернационализация
│   ├── log.py               # Логирование
│   └── meta.py              # Метаданные приложения
└── config.yaml              # Конфигурация пользователя
```

## 🏗️ Архитектура истории сообщений

### Двухуровневая система сохранения

```
┌─────────────────────────────────────────────────────────────┐
│                  АРХИТЕКТУРА ИСТОРИИ                         │
└─────────────────────────────────────────────────────────────┘

Уровень 1: JSONL (Источник истины)
├─ Формат: Одна строка = одно сообщение в JSON
├─ Режим записи: append (дополнение)
├─ Файлы: chat_{chat_id}.jsonl
└─ Назначение: Быстрая запись, накопление данных

Уровень 2: HTML (Презентация)
├─ Формат: Полный HTML с CSS и JavaScript
├─ Режим записи: overwrite (перезапись)
├─ Файлы: chat_{chat_id}.html, index.html
└─ Назначение: Красивое отображение, поиск, навигация
```

### Поток данных

```
Загрузка медиа
      ↓
Пакет сообщений (100 шт)
      ↓
┌─────────────────────────────────────┐
│  process_messages()                 │
│  ├─ Скачивание файлов              │
│  ├─ Сохранение путей:              │
│  │  downloaded_files[               │
│  │    (chat_id, msg_id)            │
│  │  ] = file_path                  │
│  └─ Возврат message_ids            │
└─────────────────────────────────────┘
      ↓
┌─────────────────────────────────────┐
│  save_batch()                       │
│  ├─ Для каждого сообщения:         │
│  │  └─ save_message()              │
│  │     └─ _save_html_message()     │
│  │        └─ append → JSONL ✍️     │
│  │                                  │
│  └─ _generate_index_html()         │
│     ├─ Для каждого чата:           │
│     │  └─ _generate_chat_html()    │
│     │     ├─ Читает JSONL 📖       │
│     │     ├─ Генерирует HTML 🎨    │
│     │     └─ Перезаписывает ✅     │
│     └─ Генерирует index.html ✅    │
└─────────────────────────────────────┘
      ↓
Очистка памяти для чата 🧹
```

### Изоляция данных между чатами

**Проблема коллизий message_id:**
- Message ID уникальны только внутри чата, не глобально
- При загрузке нескольких чатов возможны совпадения ID

**Решение:**
```python
# ❌ НЕПРАВИЛЬНО: только message_id
downloaded_files[message_id] = path

# ✅ ПРАВИЛЬНО: кортеж (chat_id, message_id)
downloaded_files[(chat_id, message_id)] = path
```

**Фильтрация для истории:**
```python
# Создать словарь только для текущего чата
chat_files = {
    msg_id: path 
    for (cid, msg_id), path in self.downloaded_files.items() 
    if cid == chat_id
}
```

### Очистка памяти

**Почему нужна:**
- Словарь `downloaded_files` накапливает записи для ВСЕХ чатов
- При загрузке 25 чатов по 10,000 сообщений = 250,000 записей
- Потребление памяти: до 30 MB

**Реализация:**
```python
# В конце begin_import_chat()
keys_to_remove = [
    key for key in self.downloaded_files.keys() 
    if key[0] == chat_id
]
for key in keys_to_remove:
    del self.downloaded_files[key]
```

**Результат:**
- Без очистки: ~30 MB (все чаты)
- С очисткой: ~2 MB (только текущий чат)
- Экономия: 93%

## 🔄 Цикл жизни загрузки

```
1. main_async()
   ├─ Загрузка конфига
   ├─ Создание Telegram клиента
   ├─ Интерактивный выбор чатов
   └─ Сохранение выбора в config

2. Для каждого чата:
   ├─ begin_import_chat()
   │  ├─ Получение настроек чата
   │  ├─ Создание семафора (параллельность)
   │  ├─ Итерация по сообщениям (пакеты по 100)
   │  │  ├─ download_media() для каждого
   │  │  │  └─ Сохранение в downloaded_files[(chat_id, msg_id)]
   │  │  ├─ process_messages()
   │  │  │  └─ save_batch()
   │  │  │     ├─ append в JSONL
   │  │  │     └─ Регенерация HTML
   │  │  └─ update_config()
   │  └─ Очистка downloaded_files[чат]
   └─ Переход к следующему чату

3. disconnect()
```

## 📊 Структура данных

### Config (config.yaml)

```yaml
chats:
  - chat_id: -1003334819414
    title: "Название чата"
    last_read_message_id: 5709  # Последнее обработанное
    ids_to_retry: []             # Неудачные загрузки
    enabled: true                # Активен ли чат

download_settings:
  base_directory: /media/disk/Telegram
  pagination_limit: 100          # Размер пакета
  max_parallel_downloads: 5      # Параллельность
  download_message_history: true
  history_format: html           # html, json, txt
```

### JSONL (chat_{chat_id}.jsonl)

```jsonl
{"id": 1, "text": "Привет", "date": "2025-01-02T10:00:00+00:00", "downloaded_file": "/path/file.jpg", ...}
{"id": 2, "text": "Как дела?", "date": "2025-01-02T10:01:00+00:00", "downloaded_file": null, ...}
{"id": 3, "text": "Отлично!", "date": "2025-01-02T10:02:00+00:00", "downloaded_file": "/path/video.mp4", ...}
```

### downloaded_files

```python
{
    (-1003334819414, 1): "/media/disk/Telegram/video/file1.mp4",
    (-1003334819414, 2): "/media/disk/Telegram/photo/file2.jpg",
    (-1009876543210, 1): "/media/disk/Telegram/document/file3.pdf",
}
```

## 🎨 HTML интерфейс

### Архитектура веб-интерфейса

```
/media/disk/Telegram/history/
├── index.html                    # Список всех чатов
├── chat_-1003334819414.html      # Чат с сообщениями
├── chat_-1003334819414.jsonl     # Источник данных
└── ... (другие чаты)
```

### Особенности дизайна

**Стиль Telegram Web:**
- Тёмная/светлая темы
- Пузыри сообщений
- Плавные анимации
- Адаптивный дизайн

**Превью медиа:**
- Фото: встроенные `<img>` с lazy loading
- Видео: встроенный `<video>` плеер
- Документы: карточки с иконками

**Пути к файлам:**
```html
<!-- Абсолютный путь с file:// -->
<img src="file:///media/disk/Telegram/photo/image.jpg">
<video src="file:///media/disk/Telegram/video/video.mp4"></video>
```

**Преимущества:**
- Работает на USB дисках в Linux
- Нет проблем с точками монтирования
- Прямое открытие файлов

## 🔧 Оптимизация производительности

### Параллельные загрузки

```python
# Семафор ограничивает количество одновременных загрузок
semaphore = asyncio.Semaphore(max_parallel_downloads)

# Все загрузки в пакете выполняются параллельно
message_ids = await asyncio.gather(
    *[download_with_semaphore(message) for message in messages]
)
```

### Управление дубликатами

**MD5 хеширование с кешем:**
```python
_hash_cache: Dict[str, str] = {}  # Глобальный кеш

def _get_file_hash(file_path: str) -> str:
    if file_path in _hash_cache:
        return _hash_cache[file_path]
    
    with open(file_path, "rb") as f:
        file_hash = md5(f.read()).hexdigest()
    _hash_cache[file_path] = file_hash
    return file_hash
```

**Преимущества:**
- Повторный хеш не пересчитывается
- Ускорение проверки дубликатов
- Меньше I/O операций

### Pagination

```yaml
pagination_limit: 100  # Обрабатывать по 100 сообщений
```

**Баланс:**
- Меньше значение: Чаще обновление HTML, медленнее
- Больше значение: Реже обновление HTML, риск потери прогресса

## 🧪 Тестирование

### Юнит-тесты

```
tests/
├── test_media_downloader.py
└── utils/
    ├── test_file_management.py
    ├── test_log.py
    ├── test_meta.py
    └── test_updates.py
```

### Покрытие кода

- Codecov интеграция
- CI/CD с GitHub Actions
- Автоматическое тестирование при коммитах

## 📝 Соглашения о коде

### Стиль кода

- **Black** для форматирования
- **Pylint** для линтинга
- **Mypy** для проверки типов
- **Docstrings** в стиле NumPy

### Типизация

```python
from typing import Dict, List, Optional, Tuple, Union

def process_messages(
    self,
    client: TelegramClient,
    messages: List[Message],
    media_types: List[str],
    file_formats: dict,
    download_directory: Optional[str] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> int:
    """
    Загрузить медиа из Telegram.

    Parameters
    ----------
    client: TelegramClient
        Клиент для взаимодействия с API Telegram.
    ...

    Returns
    -------
    int
        Максимальное значение из списка ID сообщений.
    """
```

## 🔐 Безопасность

### Конфигурация

- `config.yaml` в `.gitignore`
- API ключи НЕ коммитятся
- Пример конфигурации: `config.yaml.example`

### Данные

- История содержит все сообщения
- Хранить файлы в безопасном месте
- Не делиться HTML с историей

## 🚀 Масштабируемость

### Поддержка больших объёмов

- **Неограниченное количество чатов**
- **Неограниченное количество сообщений**
- **Стабильное потребление памяти** (благодаря очистке)

### Ограничения

- **HTML файлы:** Для чатов с 100k+ сообщений могут быть большими (>100 MB)
- **Регенерация HTML:** Для очень больших чатов может занимать время

### Решения

```yaml
# Увеличить размер пакета для меньшей частоты регенерации
pagination_limit: 500

# Использовать JSON вместо HTML для больших чатов
history_format: json
```

## 📚 Зависимости

### Основные

- `telethon` - Telegram API клиент
- `tqdm` - Прогресс-бары
- `rich` - Красивый вывод в терминал
- `pyyaml` - Парсинг конфигурации

### Опциональные

- `PySocks` - Поддержка SOCKS прокси

## 🔄 Будущие улучшения

### В разработке

- Инкрементальное обновление HTML (без полной регенерации)
- База данных вместо JSONL для больших объёмов
- Экспорт в другие форматы (PDF, EPUB)
- Поиск по всем чатам одновременно
- Статистика и аналитика чатов

### Идеи

- Веб-сервер для удалённого доступа
- Мобильное приложение
- Интеграция с облачными хранилищами
- Шифрование истории

---

**Версия:** 1.0  
**Дата:** 2025-01-02  
**Автор:** Telegram Media Downloader Team
