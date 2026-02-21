---
name: resolve-username-links-and-pending-chat-ui
overview: "Добавить полноценную поддержку username-ссылок без chat_id: при клике открывать плейсхолдер «пустой чат», в фоне (отдельным потоком) резолвить @username → chat_id через Telethon, затем добавлять чат в ClickHouse/конфиг и автоматически переключать UI на реальный чат/сообщение."
todos:
  - id: backend-resolver
    content: Сделать фоновый резолвер username→chat_id на Telethon + endpoints /api/chats/resolve(+status) и запись в ClickHouse/config.
    status: completed
  - id: frontend-pending-open
    content: В UI при username без chat_id создавать pending job, открывать плейсхолдер чата, поллить статус и автопереключать на реальный чат.
    status: completed
  - id: chatlist-pending
    content: Показывать pending-чат в левом списке чатов до момента резолва и добавления в БД.
    status: completed
  - id: verify-scenarios
    content: Проверить сценарии c/ID и username, включая случай отсутствия сообщения и ClickHouse disabled.
    status: completed
isProject: false
---

## Цель

- При клике по `t.me/username/...` и отсутствии `chat_id` в архиве: **открыть плейсхолдер чата**, запустить **фоновый резолв** `chat_id`, затем **добавить чат в ClickHouse + показать в списке чатов** и **перейти** на реальный чат/сообщение.
- Для `t.me/c/<id>/<post>` и уже известных `chat_id`: после добавления чат должен появляться в списке (через refresh) и открываться сразу.

## Текущее состояние (что есть)

- UI кликает по ссылкам в `web/ui/src/App.jsx` (`formatMessageText`) и дергает `/api/chats/by-username/{username}` и `/api/chats/add`.
- `/api/chats/add` не умеет “добавить по username без chat_id” (возвращает `"username не найден"`).
- Telethon уже используется в проекте, есть `core/session.py` (`SessionManager`) для создания `TelegramClient`.

## Изменения в бэкенде

- **Добавить фоновый резолвер username → chat_id** в `web/app.py`:
  - Поток с отдельным asyncio-loop + очередь задач.
  - Внутри потока держать один `TelegramClient` (через `SessionManager(config)`) и переиспользовать.
  - Нормализация chat_id:
    - Для каналов/супергрупп: `chat_id = int(f"-100{entity.id}")`.
    - Для обычных групп/юзеров: использовать `entity.id` (если в проекте встречаются другие форматы — уточнить по реальным данным после внедрения через логирование).
- **Новые API эндпоинты** в `web/app.py`:
  - `POST /api/chats/resolve` тело: `{ "username": "foo", "title": "@foo" }` → ответ: `{ "job_id": "...", "status": "pending" }`.
  - `GET /api/chats/resolve/{job_id}` → `{ status: pending|done|error, chat_id?, title?, error? }`.
  - Когда job завершён:
    - если ClickHouse включен: `ClickHouseMetadataDB.update_chat_info(chat_id, title, 0, 0, description="", username=username)` (уже есть в `utils/clickhouse_db.py`).
    - добавить в конфиг-очередь: `ConfigManager.add_chat_to_download_list(chat_id, title)` (fallback если ClickHouse отключен — только конфиг).
- **Обновить ответ `/api/chats/add`** (не меняя контракт): по-прежнему быстро добавляет, но для `username` без найденного `chat_id` будет рекомендовано пользоваться `/api/chats/resolve` (UI перейдет на него).

## Изменения во фронтенде

- `web/ui/src/App.jsx`:
  - В `formatMessageText(...)` для username-ссылок, когда `/api/chats/by-username/{username}` вернул `chat_id: null`:
    - вызвать `POST /api/chats/resolve` → получить `job_id`.
    - открыть **плейсхолдер**: `onOpenChat({ chat_id: "pending:<job_id>", title:` @${username}`, initialMessageId: postId })`.
  - Доработать `ChatsView`/`ChatViewer` чтобы поддерживать “pending chat”:
    - если `selectedChat.chat_id` строковый `pending:<job_id>`:
      - показывать пустой чат (заглушка) + статус «ищу chat_id…»
      - поллить `GET /api/chats/resolve/{job_id}` раз в 1–2 сек
      - когда `done`: вызвать `onRefresh()` и `onOpenChat({ chat_id: resolved_chat_id, title, initialMessageId })`.
- **Список чатов (левое окно)**:
  - В `ChatsView` добавить локальное состояние `pendingChats` (или прокинуть из Dashboard) и мержить его в `chatList` перед фильтрацией/сортировкой.
  - Pending-элемент отображать как обычный чат с `count=0,size=0` + бейдж `"поиск…"`.
  - При `done` убирать pending-элемент.

## Проверки и регрессии

- Проверить сценарии:
  - `t.me/c/<id>/<post>`: чат отсутствует → добавился → появился в списке → открылся.
  - `t.me/username/<post>`: chat_id отсутствует → открылся плейсхолдер → после резолва чат появился в списке → открылся на сообщение (если существует) либо просто чат.
  - ClickHouse disabled: резолв всё равно возвращает chat_id и добавляет в config (без вставки в CH).
- Прогнать линтеры по затронутым файлам (минимально: `ReadLints` / eslint для `App.jsx`).

## Файлы, которые затронем

- `web/app.py`
- `core/session.py` (только переиспользование, без ломания API)
- `utils/clickhouse_db.py` (использовать `update_chat_info`, без изменений или минимальные)
- `web/ui/src/App.jsx`
- (опционально) `CHANGELOG.md` и новый коммит после реализации.

