# Сессия резолвера веб-дашборда

Резолвер (username → chat_id, chat_id → title, фото профиля, полный профиль чата)
использует текущую рабочую сессию Telegram **`media_downloader`**.

Отдельная авторизация для `media_downloader_resolver` больше не требуется.

## Как выполняются задачи резолвера

1. Все задачи резолвера идут через priority-очередь (urgent: `username/chat_id`, затем `profile_photo/full_profile`).
2. Для задач действует дедлайн `5 сек` (`deadline_exceeded`), чтобы не зависать бесконечно.
3. При `database is locked` резолвер делает bounded retry и возвращает задачу в очередь.
4. API работает в job-режиме: `request -> job_id -> status polling`.

## Метрики SLA

- `GET /api/chats/resolve-metrics`
  - `queue_depth`
  - `counts` по статусам (`pending`, `done`, `error`, `deadline_exceeded`)
  - `avg_schedule_latency_ms`
  - `worker_stats` (retry/deadline counters)
