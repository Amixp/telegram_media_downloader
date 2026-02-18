"Загрузка медиа из Telegram."
import asyncio
import logging
import argparse
import sys
from typing import List, Set

from rich.console import Console
from rich.logging import RichHandler

console = Console()

from core.downloader import DownloadManager
from core.session import SessionManager
from utils.chat_selector import ChatSelector
from utils.config import ConfigManager
from utils.i18n import get_i18n
from utils.log import LogFilter
from utils.meta import print_meta
from utils.updates import check_for_updates

# Настройка базового логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()],
)
# Применение фильтров к логгерам Telethon
logging.getLogger("telethon.client.downloads").addFilter(LogFilter())
logging.getLogger("telethon.network").addFilter(LogFilter())
logger = logging.getLogger("media_downloader")


async def main_async(args: argparse.Namespace):
    """Асинхронная главная функция загрузчика."""
    config_manager = ConfigManager()
    try:
        config = config_manager.load()
    except FileNotFoundError as e:
        logger.error(str(e))
        return
    except ValueError as e:
        logger.error(f"Ошибка валидации конфигурации: {e}")
        return

    # Инициализация сессии и клиента
    session_manager = SessionManager(config)
    client = await session_manager.create_client()

    try:
        # Инициализация ClickHouse до выбора чатов (для загрузки чатов из БД)
        clickhouse_db = None
        if config.get("clickhouse", {}).get("enabled"):
            from utils.clickhouse_db import ClickHouseMetadataDB
            from utils.log import ClickHouseLogHandler
            clickhouse_db = ClickHouseMetadataDB(config["clickhouse"])
            if clickhouse_db.enabled:
                try:
                    clickhouse_db.check_connection()
                except Exception as e:
                    logger.warning("Нет доступа к ClickHouse: %s. Операции с БД отключены.", e)
                    clickhouse_db.enabled = False
                    if getattr(clickhouse_db, "_client", None) is not None:
                        try:
                            clickhouse_db._client.disconnect()
                        except Exception:
                            pass
                        clickhouse_db._client = None

        # Выбор чатов
        language = config.get("language", "ru")
        chat_selector = ChatSelector(client, language, tui_config=config.get("tui"))
        chat_selection_ui = config.get("chat_selection_ui", "classic")

        # Проверить, есть ли сохраненные чаты в конфиге или БД
        selected_chats = []
        config_has_chats = "chats" in config and isinstance(config["chats"], list) and len(config["chats"]) > 0

        logger.debug("config_has_chats = %s", config_has_chats)
        logger.debug("clickhouse_db = %s", clickhouse_db)
        if clickhouse_db:
            logger.debug("clickhouse_db.enabled = %s", clickhouse_db.enabled)

        if config_has_chats:
            enabled_entries = [c for c in config["chats"] if isinstance(c, dict) and c.get("enabled", True) and "chat_id" in c]
            # Если есть order хотя бы у одного — сортируем очередь по нему, иначе сохраняем порядок из YAML
            if any("order" in c for c in enabled_entries):
                enabled_entries.sort(key=lambda c: int(c.get("order", 10**9)) if str(c.get("order", "")).lstrip("-").isdigit() else 10**9)

            enabled_chats = [(c["chat_id"], c.get("title", ""), "saved") for c in enabled_entries]
            preselected_ids: Set[int] = {c["chat_id"] for c in enabled_entries}
            preselected_order: List[int] = [c["chat_id"] for c in enabled_entries]

            # Если указан --select-chats, всегда открыть интерфейс выбора
            if args.select_chats:
                selected_chats = await chat_selector.select_chats(
                    allow_multiple=True,
                    ui=chat_selection_ui,
                    preselected_chat_ids=preselected_ids,
                    preselected_chat_id_order=preselected_order,
                )
            elif enabled_chats:
                # Использовать чаты из конфига без вопросов
                selected_chats = enabled_chats
                logger.info("Использовано %s чатов из config.yaml", len(enabled_chats))
            else:
                # Нет enabled чатов - открыть интерфейс выбора
                selected_chats = await chat_selector.select_chats(
                    allow_multiple=True,
                    ui=chat_selection_ui,
                    preselected_chat_ids=preselected_ids,
                    preselected_chat_id_order=preselected_order,
                )
        elif config.get("chat_id"):
            # Старая структура - один чат
            logger.info("Использован устаревший параметр chat_id: %s", config["chat_id"])
            selected_chats = [(config["chat_id"], "", "single")]
        elif clickhouse_db and clickhouse_db.enabled:
            # Нет чатов в конфиге - попробовать загрузить из БД
            logger.info("Нет чатов в config.yaml, загрузка из ClickHouse...")
            db_chats = clickhouse_db.get_all_chats()
            if db_chats:
                console.print(f"[cyan]Найдено {len(db_chats)} чатов в БД[/cyan]")
                preselected_ids_db: Set[int] = {cid for cid, _ in db_chats}
                preselected_order_db: List[int] = [cid for cid, _ in db_chats]

                # Если указан --select-chats, открыть интерфейс выбора
                if args.select_chats:
                    selected_chats = await chat_selector.select_chats(
                        allow_multiple=True,
                        ui=chat_selection_ui,
                        preselected_chat_ids=preselected_ids_db,
                        preselected_chat_id_order=preselected_order_db,
                    )
                else:
                    # Использовать все чаты из БД без вопросов
                    selected_chats = [(cid, title, "db") for cid, title in db_chats]
                    logger.info("Использовано %s чатов из ClickHouse", len(db_chats))
            else:
                # Нет чатов ни в конфиге, ни в БД
                if args.select_chats:
                    # Открыть интерфейс выбора
                    selected_chats = await chat_selector.select_chats(
                        allow_multiple=True,
                        ui=chat_selection_ui,
                        preselected_chat_ids=None,
                    )
                else:
                    # Нет чатов и не указан --select-chats
                    logger.warning("Нет чатов для загрузки. Используйте --select-chats для выбора чатов.")
                    await session_manager.stop()
                    return
        else:
            # ClickHouse отключен и нет чатов в конфиге
            if args.select_chats:
                # Открыть интерфейс выбора
                selected_chats = await chat_selector.select_chats(
                    allow_multiple=True,
                    ui=chat_selection_ui,
                    preselected_chat_ids=None,
                )
            else:
                # Нет чатов и не указан --select-chats
                logger.warning("Нет чатов для загрузки. Используйте --select-chats для выбора чатов.")
                await session_manager.stop()
                return

        if not selected_chats:
            logger.warning("Не выбрано ни одного чата для загрузки")
            await session_manager.stop()
            return

        # Сохранить выбранные чаты: в БД (если включена) ИЛИ в config (fallback)
        if clickhouse_db and clickhouse_db.enabled:
            # Сохранение только в БД
            clickhouse_db.save_selected_chats([(cid, title) for (cid, title, _) in selected_chats])
            logger.info("Чаты сохранены в ClickHouse")

            # Миграция: если есть чаты в config.yaml - переместить в БД и очистить config
            if config_has_chats and "chats" in config and isinstance(config["chats"], list):
                migrated_count = 0
                for chat_entry in config["chats"]:
                    if isinstance(chat_entry, dict) and "chat_id" in chat_entry:
                        cid = chat_entry["chat_id"]
                        title = chat_entry.get("title", "")
                        # Добавить в БД, если его там нет
                        if not clickhouse_db.chat_exists(cid):
                            clickhouse_db.ensure_chat_in_db(cid, title)
                            migrated_count += 1
                # Очистить config после миграции
                if migrated_count > 0:
                    config["chats"] = []
                    config_manager.save()
                    logger.info("Мигрировано %s чатов из config.yaml в ClickHouse", migrated_count)
        else:
            # Fallback: сохранение только в config.yaml (ClickHouse отключен)
            config_manager.set_selected_chats([(cid, title) for (cid, title, _) in selected_chats])
            config_manager.save()
            logger.info("Чаты сохранены в config.yaml")

        # Настройка логирования в ClickHouse (если включено)
        if clickhouse_db and clickhouse_db.enabled:
            from utils.log import ClickHouseLogHandler
            if config.get("clickhouse", {}).get("primary_source"):
                # Дополнительная проверка для primary_source режима
                pass
                if clickhouse_db.enabled:
                    root_logger = logging.getLogger()
                    handler = ClickHouseLogHandler(clickhouse_db)
                    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
                    root_logger.addHandler(handler)

        # Загрузить для каждого выбранного чата
        download_manager = DownloadManager(config_manager, clickhouse_db=clickhouse_db)
        pagination_limit = config.get("download_settings", {}).get("pagination_limit", 100)

        # Очередь загрузки: преобразовать selected_chats в формат для begin_import_all_chats
        # Формат: [{"chat_id": int, "title": str, "enabled": True, ...}, ...]
        queue_entries = [
            {"chat_id": cid, "title": title, "enabled": True}
            for cid, title, _ in selected_chats
        ]

        logger.info("Подготовлена очередь загрузки: %s чатов", len(queue_entries))

        # Запустить загрузку всех чатов с общим прогрессом
        downloader_task = asyncio.create_task(
            download_manager.begin_import_all_chats(
                client, queue_entries, pagination_limit
            )
        )

        # Если включен веб-интерфейс, запустить сервер
        web_enabled = args.web or config.get("web", {}).get("enabled", False)
        if web_enabled:
            import uvicorn
            from web.app import app as web_app

            # Настройка DownloadManager для веба
            import web.app as web_module
            download_manager.web_app = web_module

            # Запуск uvicorn в том же цикле
            config = uvicorn.Config(web_app, host="0.0.0.0", port=8000, log_level="error")
            server = uvicorn.Server(config)

            # Ждем завершения загрузчика или сервера
            # server.serve() асинхронный и должен быть запущен в текущем цикле
            await asyncio.gather(downloader_task, server.serve())
        else:
            await downloader_task

    finally:
        # Graceful shutdown: flush буферов и сохранение конфига при любом выходе (в т.ч. Ctrl+C)
        try:
            if "download_manager" in locals() and download_manager is not None:
                await download_manager.flush()
            if "config_manager" in locals() and config_manager is not None:
                config_manager.save()
        except Exception as e:
            if getattr(sys, "meta_path", None) is not None:
                logger.warning("Ошибка при завершении (flush/save): %s", e)
        if "clickhouse_db" in locals() and clickhouse_db is not None and getattr(clickhouse_db, "enabled", False):
            await clickhouse_db.flush_logs()
        if "download_manager" in locals() and download_manager is not None:
            download_manager.close()
        await session_manager.stop()


def main():
    """Главная функция загрузчика."""
    parser = argparse.ArgumentParser(description="Telegram Media Downloader")
    parser.add_argument("--web", action="store_true", help="Запустить веб-интерфейс дашборда")
    parser.add_argument("--select-chats", action="store_true", help="Открыть интерфейс выбора чатов")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Обработка сигналов: отменяем только дочерние задачи, главная — выполняет finally (flush/save).
    # loop.stop() не вызываем, чтобы main_async успел выполнить finally.
    import signal
    main_task_ref = []

    def on_signal():
        if main_task_ref:
            main_task = main_task_ref[0]
            for task in asyncio.all_tasks(loop):
                if task is not main_task:
                    task.cancel()
        else:
            for task in asyncio.all_tasks(loop):
                task.cancel()
        try:
            logger.info("Получен сигнал прерывания, завершение работы...")
        except Exception:
            pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, on_signal)
        except NotImplementedError:
            pass

    try:
        main_task_ref.append(loop.create_task(main_async(args)))
        loop.run_until_complete(main_task_ref[0])
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    print_meta(logger)
    main()