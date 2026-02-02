"Загрузка медиа из Telegram."
import asyncio
import logging
import argparse
from typing import List, Set

from rich.logging import RichHandler
from rich.prompt import Confirm

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
        # Выбор чатов
        language = config.get("language", "ru")
        chat_selector = ChatSelector(client, language, tui_config=config.get("tui"))
        chat_selection_ui = config.get("chat_selection_ui", "classic")

        # Проверить, есть ли сохраненные чаты в конфиге
        selected_chats = []
        if "chats" in config and isinstance(config["chats"], list):
            enabled_entries = [c for c in config["chats"] if isinstance(c, dict) and c.get("enabled", True) and "chat_id" in c]
            # Если есть order хотя бы у одного — сортируем очередь по нему, иначе сохраняем порядок из YAML
            if any("order" in c for c in enabled_entries):
                enabled_entries.sort(key=lambda c: int(c.get("order", 10**9)) if str(c.get("order", "")).lstrip("-").isdigit() else 10**9)

            enabled_chats = [(c["chat_id"], c.get("title", ""), "saved") for c in enabled_entries]
            preselected_ids: Set[int] = {c["chat_id"] for c in enabled_entries}
            preselected_order: List[int] = [c["chat_id"] for c in enabled_entries]

            if enabled_chats and not config.get("interactive_chat_selection", True):
                selected_chats = enabled_chats
            elif enabled_chats and config.get("interactive_chat_selection", True):
                edit = Confirm.ask("Редактировать список чатов?", default=False)
                if edit:
                    selected_chats = await chat_selector.select_chats(
                        allow_multiple=True,
                        ui=chat_selection_ui,
                        preselected_chat_ids=preselected_ids,
                        preselected_chat_id_order=preselected_order,
                    )
                else:
                    selected_chats = enabled_chats
            else:
                selected_chats = await chat_selector.select_chats(
                    allow_multiple=True,
                    ui=chat_selection_ui,
                    preselected_chat_ids=preselected_ids,
                    preselected_chat_id_order=preselected_order,
                )
        elif config.get("chat_id"):
            # Старая структура - один чат
            selected_chats = [(config["chat_id"], "", "single")]
        else:
            # Интерактивный выбор
            selected_chats = await chat_selector.select_chats(
                allow_multiple=True,
                ui=chat_selection_ui,
                preselected_chat_ids=None,
            )

        if not selected_chats:
            logger.warning("Не выбрано ни одного чата для загрузки")
            await session_manager.stop()
            return

        # Сохранить выбранные чаты в конфиг
        config_manager.set_selected_chats([(cid, title) for (cid, title, _) in selected_chats])
        config_manager.save()

        # Загрузить для каждого выбранного чата
        download_manager = DownloadManager(config_manager)
        pagination_limit = config.get("download_settings", {}).get("pagination_limit", 100)

        # Очередь загрузки: берём из конфига (с учётом order), чтобы порядок был стабильным и редактируемым
        cfg_after = config_manager.config
        queue_entries = [
            c for c in cfg_after.get("chats", [])
            if isinstance(c, dict) and c.get("enabled", True) and "chat_id" in c
        ]
        if any("order" in c for c in queue_entries):
            queue_entries.sort(key=lambda c: int(c.get("order", 10**9)) if str(c.get("order", "")).lstrip("-").isdigit() else 10**9)

        # Запустить загрузку всех чатов с общим прогрессом
        downloader_task = asyncio.create_task(
            download_manager.begin_import_all_chats(
                client, queue_entries, pagination_limit
            )
        )

        # Если включен веб-интерфейс, запустить сервер
        if args.web:
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
        await session_manager.stop()


def main():
    """Главная функция загрузчика."""
    parser = argparse.ArgumentParser(description="Telegram Media Downloader")
    parser.add_argument("--web", action="store_true", help="Запустить веб-интерфейс дашборда")
    args = parser.parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Обработка сигналов для корректного завершения
    import signal
    def stop_loop():
        # Пытаемся корректно остановить задачи
        for task in asyncio.all_tasks(loop):
            task.cancel()
        logger.info("Получен сигнал прерывания, завершение работы...")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_loop)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(main_async(args))
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    print_meta(logger)
    main()