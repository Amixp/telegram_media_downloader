# Telegram Media Downloader

<p align="center">
<a href="https://github.com/Dineshkarthik/telegram_media_downloader/actions"><img alt="Unittest" src="https://github.com/Dineshkarthik/telegram_media_downloader/workflows/Unittest/badge.svg"></a>
<a href="https://codecov.io/gh/Dineshkarthik/telegram_media_downloader"><img alt="Coverage Status" src="https://codecov.io/gh/Dineshkarthik/telegram_media_downloader/branch/master/graph/badge.svg"></a>
<a href="https://github.com/Dineshkarthik/telegram_media_downloader/blob/master/LICENSE"><img alt="License: MIT" src="https://black.readthedocs.io/en/stable/_static/license.svg"></a>
<a href="https://github.com/python/black"><img alt="Code style: black" src="https://img.shields.io/badge/code%20style-black-000000.svg"></a>
</p>

## Описание

Telegram Media Downloader — инструмент для загрузки всех медиафайлов и истории сообщений из чатов и каналов Telegram, участником которых вы являетесь. Метаданные последнего прочитанного/загруженного сообщения сохраняются в файле конфигурации, чтобы не загружать одни и те же файлы повторно.

## Возможности

- ✅ Загрузка всех типов медиа (аудио, документы, фото, видео, голосовые сообщения, видеосообщения)
- ✅ Загрузка истории текстовых сообщений (JSON или текстовый формат)
- ✅ Поддержка нескольких чатов/каналов одновременно
- ✅ Интерактивный выбор чатов с мультивыбором
- ✅ Сохранение состояния загрузки для каждого чата отдельно
- ✅ Параллельные загрузки с настраиваемым количеством потоков
- ✅ Фильтрация по отправителю, дате, размеру файла
- ✅ Пропуск дубликатов файлов
- ✅ Кастомная директория для загрузок
- ✅ Логирование в файл с ротацией
- ✅ Прогресс-бар для отслеживания загрузки
- ✅ Полная русификация интерфейса
- ✅ Поддержка прокси (SOCKS4, SOCKS5, HTTP)

## Требования

| Категория  | Требования                                       |
| ---------- | ------------------------------------------------ |
| Python     | `Python 3.8` и выше                              |
| Типы медиа | audio, document, photo, video, video_note, voice |

## Установка

### Для Linux/macOS (с поддержкой `make`):

```sh
git clone https://github.com/Dineshkarthik/telegram_media_downloader.git
cd telegram_media_downloader
make install
```

### Для Windows или систем без `make`:

```sh
git clone https://github.com/Dineshkarthik/telegram_media_downloader.git
cd telegram_media_downloader
pip3 install -r requirements.txt
```

### Для разработчиков:

```sh
git clone https://github.com/Dineshkarthik/telegram_media_downloader.git
cd telegram_media_downloader
make dev_install
```

## Конфигурация

Все настройки передаются через файл `config.yaml`.

### Быстрый старт

1. Скопируйте пример конфигурации:
   ```sh
   cp config.yaml.example config.yaml
   ```

2. Получите API ключи Telegram:
   - Перейдите на [https://my.telegram.org/apps](https://my.telegram.org/apps)
   - Войдите в свой аккаунт Telegram
   - Заполните форму для регистрации нового приложения
   - Скопируйте `api_id` и `api_hash`

3. Получите ID чата:
   - **Через веб-версию Telegram**: Откройте чат и посмотрите URL
   - **Через бота**: Используйте [@username_to_id_bot](https://t.me/username_to_id_bot)

4. Отредактируйте `config.yaml` с вашими данными

### Пример конфигурации

См. файл `config.yaml.example` для полного примера со всеми опциями.

#### Основные параметры:

```yaml
api_id: your_api_id
api_hash: your_api_hash
language: ru  # ru или en

# Типы медиа для загрузки
media_types:
  - all  # или конкретные типы: audio, document, photo, video, voice, video_note

# Настройки загрузки
download_settings:
  max_parallel_downloads: 5  # Количество одновременных загрузок
  pagination_limit: 100       # Размер пакета сообщений
  base_directory: ""          # Базовая директория (пусто = директория скрипта)
  skip_duplicates: true       # Пропускать дубликаты
  download_message_history: false  # Загружать историю сообщений
  history_format: json        # Формат истории (json или txt)
  history_directory: history  # Директория для истории
```

#### Фильтры:

```yaml
# Фильтр по отправителю
sender_filter:
  enabled: false
  user_ids: [123456789]
  usernames: ["@username"]

# Фильтры по дате
start_date: "2024-01-01"  # Начальная дата
end_date: "2024-12-31"    # Конечная дата
max_messages: 1000        # Максимум сообщений
```

#### Логирование:

```yaml
logging:
  file_logging:
    enabled: false
    file_path: downloads.log
    level: INFO
    max_bytes: 10485760  # 10 MB
    backup_count: 5
```

#### Прокси:

```yaml
proxy:
  scheme: socks5  # socks4, socks5, http
  hostname: 11.22.33.44
  port: 1234
  username: your_username  # опционально
  password: your_password  # опционально
```

#### Несколько чатов:

```yaml
chats:
  - chat_id: 123456789
    title: "Название чата"
    last_read_message_id: 0
    ids_to_retry: []
    enabled: true
  - chat_id: 987654321
    title: "Другой чат"
    last_read_message_id: 0
    ids_to_retry: []
    enabled: true
```

## Использование

### Базовое использование:

```sh
python3 media_downloader.py
```

При первом запуске будет предложен интерактивный выбор чатов. Выбранные чаты сохраняются в конфигурации для последующих запусков.

### Структура загруженных файлов:

| Тип медиа  | Директория загрузки         |
| ---------- | --------------------------- |
| audio      | `base_directory/audio`      |
| document   | `base_directory/document`   |
| photo      | `base_directory/photo`      |
| video      | `base_directory/video`      |
| voice      | `base_directory/voice`      |
| video_note | `base_directory/video_note` |
| history    | `base_directory/history`    |

### Интерактивный выбор чатов

При запуске программы:

1. Если в конфиге есть сохраненные чаты, будет предложено использовать их или выбрать новые
2. Выбор фильтра для удобной работы с большим списком чатов:
   - **Все чаты** — показать весь список
   - **Только группы и каналы** — исключить личные чаты (рекомендуется по умолчанию)
   - **Только каналы** — только публичные каналы
   - **Только группы** — только групповые чаты
   - **Только пользователи** — только личные чаты
   - **Поиск по названию** — найти чаты по части названия
3. Отобразится таблица с отфильтрованными чатами (по 50 на страницу)
4. Доступные команды:
   - Номера через запятую (например: `1,3,5`) — выбрать конкретные чаты
   - `all` — выбрать все отфильтрованные чаты
   - `next` или `n` — следующая страница
   - `prev` или `p` — предыдущая страница
   - `page N` — перейти на страницу N
   - `search` — новый поиск по названию
   - `filter` — изменить фильтр
   - `done` — завершить выбор
5. Выбранные чаты сохраняются в конфигурации

**Пример работы с большим списком:**
```
Выберите тип чатов: 2 (только группы и каналы)
Отфильтровано: 156 чатов (группы + каналы)

Показано 1-50 из 156
[таблица с чатами]

Ваш выбор: 1,5,10
✓ Выбран: Канал 1
✓ Выбран: Канал 2
✓ Выбран: Канал 3

Ваш выбор: next
[следующая страница]

Ваш выбор: search
Поисковый запрос: программ
Найдено: 12 чатов

Ваш выбор: all
✓ Выбрано всех: 12 чатов
```

### Загрузка истории сообщений

Для включения загрузки истории сообщений:

```yaml
download_settings:
  download_message_history: true
  history_format: json  # или txt
  history_directory: history
```

История сохраняется в формате:
- **JSON**: `chat_{chat_id}.jsonl` (каждая строка — JSON объект)
- **TXT**: `chat_{chat_id}.txt` (текстовый формат для чтения)

## Новые возможности

### Параллельные загрузки

Настройте количество одновременных загрузок:

```yaml
download_settings:
  max_parallel_downloads: 5  # None = без ограничений
```

### Фильтр по отправителю

Загружайте медиа только от определенных пользователей:

```yaml
sender_filter:
  enabled: true
  user_ids: [123456789, 987654321]
  usernames: ["@username1", "@username2"]
```

### Пропуск дубликатов

Автоматическое обнаружение и удаление дубликатов:

```yaml
download_settings:
  skip_duplicates: true
```

### Кастомная директория

Укажите свою директорию для всех загрузок:

```yaml
download_settings:
  base_directory: "/path/to/downloads"
```

### Логирование в файл

Сохраняйте логи в файл с автоматической ротацией:

```yaml
logging:
  file_logging:
    enabled: true
    file_path: downloads.log
    level: INFO
    max_bytes: 10485760  # 10 MB
    backup_count: 5
```

## Состояние загрузки

Программа автоматически сохраняет состояние загрузки для каждого чата:

- `last_read_message_id` — ID последнего обработанного сообщения
- `ids_to_retry` — список ID сообщений, которые не удалось загрузить

При следующем запуске загрузка продолжится с места остановки.

## Вклад в проект

### Руководство по вкладу

Прочитайте [руководство по вкладу](CONTRIBUTING.md) для изучения процесса подачи изменений, правил кодирования и многого другого.

### Хотите помочь?

Хотите сообщить об ошибке, внести код или улучшить документацию? Отлично! Прочитайте наши [руководящие принципы](CONTRIBUTING.md).

### Кодекс поведения

Помогите нам сохранить Telegram Media Downloader открытым и инклюзивным. Пожалуйста, прочитайте и следуйте нашему [Кодексу поведения](CODE_OF_CONDUCT.md).

## Лицензия

MIT License - см. файл [LICENSE](LICENSE) для деталей.

## Поддержка

- [Обсуждения](https://github.com/Dineshkarthik/telegram_media_downloader/discussions)
- [Сообщить об ошибке](https://github.com/Dineshkarthik/telegram_media_downloader/issues)
- [Telegram Community](https://t.me/tgmdnews)
