"""Модуль для интерактивного выбора чатов."""

from __future__ import annotations

import asyncio
import logging
import sys
import textwrap
import time
import unicodedata
import locale
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from telethon import TelegramClient
from telethon.tl.types import Chat, User

from utils.i18n import get_i18n


@dataclass(frozen=True)
class ChatListItem:
    """Элемент списка чатов для выбора."""

    chat_id: int
    title: str
    chat_type: str
    last_message_preview: str = ""


class ChatSelector:
    """Класс для интерактивного выбора чатов."""

    _DEFAULT_TUI_CONFIG: Dict[str, Any] = {
        "display": {
            "show_chat_id": True,
        },
        "preview": {
            # Сколько последних сообщений показывать в превью справа.
            # 1 = текущее поведение (берём dialog.message, без сетевых запросов).
            "messages_count": 1,
            # Режим получения сообщений, если messages_count > 1:
            # - "on_demand": подкачка по курсору
            # - "off": никогда не делать сетевых запросов (даже если messages_count > 1)
            # - "auto": on_demand если messages_count > 1, иначе off
            "fetch_mode": "auto",
            # Дебаунс (мс) перед запросом после остановки курсора
            "debounce_ms": 200,
            # Кэш превью по chat_id (LRU)
            "cache_size": 128,
            "cache_ttl_s": 300,
            # Рендер (вариант D): переносить строки по ширине и ограничивать высоту
            "wrap": True,
            "max_lines": 12,
            "show_loading": True,
            "loading_text": "Загружаю…",
            "error_text": "Ошибка загрузки превью",
            "label_single": "Последнее сообщение:",
            "label_multi": "Последние сообщения:",
            "include_media_placeholder": True,
            # Частота опроса клавиатуры/обновления (мс)
            "poll_interval_ms": 33,
        },
        "colors": {
            # Цвета: black, red, green, yellow, blue, magenta, cyan, white, default
            # Для фона используйте *_bg. "default" = фон/цвет терминала.
            "screen_fg": "default",
            "screen_bg": "default",
            "header_fg": "cyan",
            "header_bg": "default",
            "footer_fg": "white",
            "footer_bg": "default",
            "separator_fg": "blue",
            "separator_bg": "default",
            "list_fg": "default",
            "list_bg": "default",
            # Если selected_fg/selected_bg == default, выделение будет через reverse (как раньше)
            "selected_fg": "default",
            "selected_bg": "default",
        },
        "layout": {
            "list_min_width": 30,
            "list_width_ratio": 0.5,
            "preview_min_width": 10,
        },
        "text": {
            "header": (
                "Выбор чатов: ↑/↓ PgUp/PgDn Home/End | Space=выбрать | "
                "Enter=OK | f=фильтр | /=поиск по имени | c=очистить | "
                "v=только выбранные | J/K=порядок очереди | q/Esc=выход"
            ),
            "no_chats": "Нет доступных чатов",
            "no_selected": "Нет выбранных чатов",
            "search_prompt": "Поиск по имени: ",
        },
        "keys": {
            "quit": ["q", "esc"],
            "confirm": ["enter"],
            "toggle": ["space"],
            "filter": ["f"],
            "search": ["/"],
            "clear": ["c"],
            "show_selected": ["v"],
            "up": ["up", "k"],
            "down": ["down", "j"],
            "page_up": ["pageup"],
            "page_down": ["pagedown"],
            "home": ["home"],
            "end": ["end"],
            # Редактирование очереди (только для выбранных чатов)
            "move_up": ["K"],
            "move_down": ["J"],
        },
    }

    def __init__(
        self,
        client: TelegramClient,
        language: str = "ru",
        tui_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Инициализация ChatSelector.

        Parameters
        ----------
        client: TelegramClient
            Клиент Telethon.
        language: str
            Язык интерфейса.
        tui_config: Optional[Dict[str, Any]]
            Настройки TUI из конфигурации (секция `tui`).
        """
        self.client = client
        self.i18n = get_i18n(language)
        self.console = Console()
        self.page_size = 50  # Количество чатов на странице
        self._tui_config_raw: Dict[str, Any] = tui_config or {}

    def _get_tui_config(self) -> Dict[str, Any]:
        """
        Получить TUI конфигурацию с применением дефолтов.

        Returns
        -------
        Dict[str, Any]
            Слитая конфигурация TUI.
        """
        cfg: Dict[str, Any] = {
            "display": dict(self._DEFAULT_TUI_CONFIG["display"]),
            "preview": dict(self._DEFAULT_TUI_CONFIG["preview"]),
            "colors": dict(self._DEFAULT_TUI_CONFIG["colors"]),
            "layout": dict(self._DEFAULT_TUI_CONFIG["layout"]),
            "text": dict(self._DEFAULT_TUI_CONFIG["text"]),
            "keys": dict(self._DEFAULT_TUI_CONFIG["keys"]),
        }
        raw = self._tui_config_raw
        if isinstance(raw, dict):
            for section in ("display", "preview", "colors", "layout", "text", "keys"):
                val = raw.get(section)
                if isinstance(val, dict):
                    cfg[section].update(val)
        return cfg

    async def get_available_chats(self) -> List[Tuple[int, str, str]]:
        """
        Получить список доступных чатов.

        Returns
        -------
        List[Tuple[int, str, str]]
            Список кортежей (chat_id, title, type).
        """
        chats = []
        async for dialog in self.client.iter_dialogs():
            chat_id = dialog.id
            title = dialog.name
            entity = dialog.entity

            # Определить тип чата
            if isinstance(entity, User):
                chat_type = "user"
            elif isinstance(entity, Chat):
                chat_type = "group"
            else:
                chat_type = "channel"

            chats.append((chat_id, title, chat_type))
        return chats

    async def get_available_chat_items(self) -> List[ChatListItem]:
        """
        Получить список доступных чатов с превью последнего сообщения.

        Notes
        -----
        Превью берётся из `dialog.message` (последнее сообщение диалога), без
        дополнительных сетевых запросов.

        Returns
        -------
        List[ChatListItem]
            Список элементов чатов для TUI/интерактивного выбора.
        """
        items: List[ChatListItem] = []
        async for dialog in self.client.iter_dialogs():
            chat_id = dialog.id
            title = dialog.name
            entity = dialog.entity

            if isinstance(entity, User):
                chat_type = "user"
            elif isinstance(entity, Chat):
                chat_type = "group"
            else:
                chat_type = "channel"

            preview = ""
            last_msg = getattr(dialog, "message", None)
            if last_msg is not None:
                text = getattr(last_msg, "message", None)
                if isinstance(text, str):
                    preview = text.replace("\n", " ").strip()
                if not preview and getattr(last_msg, "media", None) is not None:
                    preview = "<media>"

            items.append(
                ChatListItem(
                    chat_id=chat_id,
                    title=title or "",
                    chat_type=chat_type,
                    last_message_preview=preview,
                )
            )
        return items

    @staticmethod
    def filter_chat_items(
        items: Sequence[ChatListItem],
        filter_mode: str = "all",
        search_query: str = "",
    ) -> List[ChatListItem]:
        """
        Отфильтровать список чатов для TUI по типу и поисковому запросу.

        Parameters
        ----------
        items: Sequence[ChatListItem]
            Входной список.
        filter_mode: str
            Режим фильтрации по типу:
            - "all": все
            - "groups_channels": группы + каналы
            - "channels": только каналы
            - "groups": только группы
            - "users": только пользователи
        search_query: str
            Поисковый запрос по названию чата (подстрока, без регистра).

        Returns
        -------
        List[ChatListItem]
            Отфильтрованный список.
        """
        filtered: List[ChatListItem] = list(items)

        if filter_mode == "groups_channels":
            filtered = [i for i in filtered if i.chat_type in ("group", "channel")]
        elif filter_mode == "channels":
            filtered = [i for i in filtered if i.chat_type == "channel"]
        elif filter_mode == "groups":
            filtered = [i for i in filtered if i.chat_type == "group"]
        elif filter_mode == "users":
            filtered = [i for i in filtered if i.chat_type == "user"]

        q = (search_query or "").strip().lower()
        if q:
            filtered = [
                i
                for i in filtered
                if q in (i.title or "").lower()
                or q in str(i.chat_id)
            ]

        return filtered

    def filter_chats(
        self,
        chats: List[Tuple[int, str, str]],
        chat_type: Optional[str] = None,
        search_query: Optional[str] = None
    ) -> List[Tuple[int, str, str]]:
        """
        Фильтровать чаты по типу и поисковому запросу.

        Parameters
        ----------
        chats: List[Tuple[int, str, str]]
            Список чатов.
        chat_type: Optional[str]
            Тип чата для фильтрации ('user', 'group', 'channel', None - все).
        search_query: Optional[str]
            Поисковый запрос по названию.

        Returns
        -------
        List[Tuple[int, str, str]]
            Отфильтрованный список чатов.
        """
        filtered = chats

        # Фильтр по типу
        if chat_type:
            filtered = [c for c in filtered if c[2] == chat_type]

        # Поиск по названию
        if search_query:
            query_lower = search_query.lower()
            filtered = [c for c in filtered if query_lower in c[1].lower()]

        return filtered

    def display_chats(
        self,
        chats: List[Tuple[int, str, str]],
        page: int = 1,
        show_stats: bool = True
    ) -> int:
        """
        Отобразить список чатов в виде таблицы с пагинацией.

        Parameters
        ----------
        chats: List[Tuple[int, str, str]]
            Список чатов (chat_id, title, type).
        page: int
            Номер страницы для отображения.
        show_stats: bool
            Показывать ли статистику по типам чатов.

        Returns
        -------
        int
            Общее количество страниц.
        """
        total_pages = (len(chats) - 1) // self.page_size + 1 if chats else 0
        start_idx = (page - 1) * self.page_size
        end_idx = min(start_idx + self.page_size, len(chats))
        page_chats = chats[start_idx:end_idx]

        if show_stats:
            # Статистика по типам
            users = sum(1 for c in chats if c[2] == "user")
            groups = sum(1 for c in chats if c[2] == "group")
            channels = sum(1 for c in chats if c[2] == "channel")

            self.console.print(f"\n[bold cyan]Статистика:[/bold cyan]")
            self.console.print(f"  Всего: {len(chats)} | Пользователи: {users} | Группы: {groups} | Каналы: {channels}")

        title = f"Доступные чаты (стр. {page}/{total_pages}, показано {start_idx + 1}-{end_idx} из {len(chats)})"
        table = Table(title=title)
        table.add_column("№", style="cyan", no_wrap=True)
        table.add_column("Название", style="magenta", max_width=50)
        table.add_column("Тип", style="green")
        table.add_column("ID", style="yellow")

        for idx, (chat_id, title_text, chat_type) in enumerate(page_chats, start_idx + 1):
            type_ru = {
                "user": "👤 Пользователь",
                "group": "👥 Группа",
                "channel": "📢 Канал"
            }.get(chat_type, chat_type)
            table.add_row(str(idx), title_text[:50], type_ru, str(chat_id))

        self.console.print(table)
        return total_pages

    def select_chats_interactive(
        self, chats: List[Tuple[int, str, str]]
    ) -> List[Tuple[int, str, str]]:
        """
        Интерактивный выбор чатов с фильтрацией и пагинацией.

        Parameters
        ----------
        chats: List[Tuple[int, str, str]]
            Список доступных чатов.

        Returns
        -------
        List[Tuple[int, str, str]]
            Список выбранных чатов.
        """
        if not chats:
            self.console.print("[red]Нет доступных чатов[/red]")
            return []

        # Спросить о фильтрации
        self.console.print("\n[bold cyan]Фильтрация чатов[/bold cyan]")
        self.console.print("Выберите тип чатов для отображения:")
        self.console.print("  1. Все чаты")
        self.console.print("  2. Только группы и каналы (без пользователей)")
        self.console.print("  3. Только каналы")
        self.console.print("  4. Только группы")
        self.console.print("  5. Только пользователи")
        self.console.print("  6. Поиск по названию")

        filter_choice = Prompt.ask("Ваш выбор", default="2").strip()

        filtered_chats = chats
        if filter_choice == "2":
            filtered_chats = self.filter_chats(chats, chat_type="group") + \
                           self.filter_chats(chats, chat_type="channel")
            self.console.print(f"[green]Отфильтровано: {len(filtered_chats)} чатов (группы + каналы)[/green]")
        elif filter_choice == "3":
            filtered_chats = self.filter_chats(chats, chat_type="channel")
            self.console.print(f"[green]Отфильтровано: {len(filtered_chats)} каналов[/green]")
        elif filter_choice == "4":
            filtered_chats = self.filter_chats(chats, chat_type="group")
            self.console.print(f"[green]Отфильтровано: {len(filtered_chats)} групп[/green]")
        elif filter_choice == "5":
            filtered_chats = self.filter_chats(chats, chat_type="user")
            self.console.print(f"[green]Отфильтровано: {len(filtered_chats)} пользователей[/green]")
        elif filter_choice == "6":
            search_query = Prompt.ask("Введите поисковый запрос")
            filtered_chats = self.filter_chats(chats, search_query=search_query)
            self.console.print(f"[green]Найдено: {len(filtered_chats)} чатов[/green]")

        if not filtered_chats:
            self.console.print("[yellow]После фильтрации чатов не осталось[/yellow]")
            return []

        # Пагинация
        current_page = 1
        selected_chats = []

        while True:
            total_pages = self.display_chats(filtered_chats, current_page)

            self.console.print("\n[bold]Команды:[/bold]")
            self.console.print("  - Номера через запятую (например: 1,3,5) - выбрать чаты")
            self.console.print("  - 'all' - выбрать все отфильтрованные чаты")
            self.console.print("  - 'next' или 'n' - следующая страница")
            self.console.print("  - 'prev' или 'p' - предыдущая страница")
            self.console.print("  - 'page N' - перейти на страницу N")
            self.console.print("  - 'search' - новый поиск")
            self.console.print("  - 'filter' - изменить фильтр")
            self.console.print("  - 'done' - завершить выбор")

            if selected_chats:
                self.console.print(f"\n[green]Уже выбрано: {len(selected_chats)} чатов[/green]")

            choice = Prompt.ask("Ваш выбор", default="done").strip().lower()

            if choice == "done":
                break
            elif choice == "all":
                selected_chats = filtered_chats.copy()
                self.console.print(f"[green]✓ Выбрано всех: {len(selected_chats)} чатов[/green]")
                break
            elif choice in ["next", "n"]:
                if current_page < total_pages:
                    current_page += 1
                else:
                    self.console.print("[yellow]Вы на последней странице[/yellow]")
            elif choice in ["prev", "p"]:
                if current_page > 1:
                    current_page -= 1
                else:
                    self.console.print("[yellow]Вы на первой странице[/yellow]")
            elif choice.startswith("page "):
                try:
                    page_num = int(choice.split()[1])
                    if 1 <= page_num <= total_pages:
                        current_page = page_num
                    else:
                        self.console.print(f"[red]Неверный номер страницы. Доступны: 1-{total_pages}[/red]")
                except (ValueError, IndexError):
                    self.console.print("[red]Неверный формат. Используйте: page N[/red]")
            elif choice == "search":
                search_query = Prompt.ask("Введите поисковый запрос")
                filtered_chats = self.filter_chats(chats, search_query=search_query)
                self.console.print(f"[green]Найдено: {len(filtered_chats)} чатов[/green]")
                current_page = 1
            elif choice == "filter":
                # Рекурсивный вызов для нового выбора фильтра
                return self.select_chats_interactive(chats)
            else:
                try:
                    indices = [int(x.strip()) - 1 for x in choice.split(",")]
                    for idx in indices:
                        if 0 <= idx < len(filtered_chats):
                            if filtered_chats[idx] not in selected_chats:
                                selected_chats.append(filtered_chats[idx])
                                self.console.print(
                                    f"[green]✓ Выбран: {filtered_chats[idx][1]}[/green]"
                                )
                            else:
                                self.console.print(
                                    f"[yellow]Уже выбран: {filtered_chats[idx][1]}[/yellow]"
                                )
                        else:
                            self.console.print(
                                f"[red]Неверный номер: {idx + 1} (доступно: 1-{len(filtered_chats)})[/red]"
                            )
                except ValueError:
                    self.console.print("[red]Неверный формат. Используйте номера через запятую.[/red]")

        return selected_chats

    async def _select_chats_tui(
        self,
        items: Sequence[ChatListItem],
        preselected_chat_ids: Optional[Set[int]] = None,
        preselected_chat_id_order: Optional[Sequence[int]] = None,
    ) -> List[Tuple[int, str, str]]:
        """
        TUI выбор чатов: навигация клавишами + пробел для выбора.

        Управление:
        - ↑/↓, PgUp/PgDn, Home/End: навигация
        - Space: toggle выбора
        - Enter: подтвердить
        - q / ESC: отменить (вернёт пустой список)

        Parameters
        ----------
        items: Sequence[ChatListItem]
            Список чатов.
        preselected_chat_ids: Optional[Set[int]]
            Предварительно выбранные chat_id (например из config.yaml).
        preselected_chat_id_order: Optional[Sequence[int]]
            Предварительно выбранные chat_id в порядке очереди (например из config.yaml).

        Returns
        -------
        List[Tuple[int, str, str]]
            Список выбранных чатов в формате (chat_id, title, type).
        """
        try:
            import curses
        except Exception:  # pragma: no cover - optional on Windows without windows-curses
            self.console.print(
                "[yellow]TUI режим недоступен (нет curses). Использую старый интерактивный режим.[/yellow]"
            )
            return self.select_chats_interactive([(i.chat_id, i.title, i.chat_type) for i in items])

        tui_cfg = self._get_tui_config()
        display_cfg = tui_cfg.get("display", {})
        preview_cfg = tui_cfg.get("preview", {})
        layout_cfg = tui_cfg.get("layout", {})
        text_cfg = tui_cfg.get("text", {})
        colors_cfg = tui_cfg.get("colors", {})
        keys_cfg = tui_cfg.get("keys", {})
        show_chat_id = display_cfg.get("show_chat_id", True) is True

        # Важно: любые выводы в stdout/stderr или StreamHandler'ы логгера во время curses
        # могут "сломать" экран (пропадает заголовок/подвал, всё плывёт).
        # Поэтому на время TUI перехватываем вывод в файл и отключаем вывод в терминал,
        # не теряя при этом отладочную информацию.
        class _TuiStreamCapture:  # pylint: disable=too-few-public-methods
            def __init__(self, file_obj):  # noqa: ANN001
                self._f = file_obj

            def write(self, s: Any) -> int:
                try:
                    text = s if isinstance(s, str) else str(s)
                except Exception:
                    text = "<unprintable>"
                try:
                    self._f.write(text)
                    self._f.flush()
                except Exception:
                    # Ничего не делаем: нельзя падать из-за логов
                    return 0
                return len(text)

            def flush(self) -> None:
                try:
                    self._f.flush()
                except Exception:
                    pass

            def isatty(self) -> bool:
                return False

        log_path = "tui-debug.log"
        root_logger = logging.getLogger()
        saved_handlers = list(root_logger.handlers)
        saved_root_level = root_logger.level
        saved_stdout = sys.stdout
        saved_stderr = sys.stderr
        tui_log_file = None
        tui_file_handler: Optional[logging.Handler] = None
        try:
            tui_log_file = open(log_path, "a", encoding="utf-8")  # noqa: PTH123
            sys.stdout = _TuiStreamCapture(tui_log_file)  # type: ignore[assignment]
            sys.stderr = _TuiStreamCapture(tui_log_file)  # type: ignore[assignment]

            # Убрать вывод в терминал (RichHandler/StreamHandler), оставив файл.
            for h in list(root_logger.handlers):
                if isinstance(h, logging.StreamHandler):
                    root_logger.removeHandler(h)

            tui_file_handler = logging.FileHandler(log_path, encoding="utf-8")
            tui_file_handler.setLevel(logging.DEBUG)
            tui_file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            root_logger.addHandler(tui_file_handler)
            if root_logger.level > logging.DEBUG:
                root_logger.setLevel(logging.DEBUG)
        except Exception:
            # Если что-то пошло не так — лучше продолжить TUI без перехвата,
            # чем падать на этапе выбора чатов.
            tui_log_file = None
            tui_file_handler = None

        selected: Set[int] = set(preselected_chat_ids or set())
        # Порядок очереди: сохраняем как список chat_id
        selected_order: List[int] = []
        if preselected_chat_id_order:
            for cid in preselected_chat_id_order:
                if cid in selected and cid not in selected_order:
                    selected_order.append(cid)
        # Дособрать порядок из items (стабильно), если не все preselected были в order
        if selected:
            for it0 in items:
                if it0.chat_id in selected and it0.chat_id not in selected_order:
                    selected_order.append(it0.chat_id)

        show_selected_only = False
        by_id: Dict[int, ChatListItem] = {i.chat_id: i for i in items}

        def _visible_items() -> List[ChatListItem]:
            if show_selected_only:
                out: List[ChatListItem] = []
                for cid in selected_order:
                    if cid not in selected:
                        continue
                    it = by_id.get(cid)
                    if it is not None:
                        out.append(it)
                return out
            return self.filter_chat_items(items, filter_mode=filter_mode, search_query=search_query)
        filter_mode = "all"
        search_query = ""
        index = 0
        offset = 0

        def _type_label(t: str) -> str:
            return {"user": "👤", "group": "👥", "channel": "📢"}.get(t, "?")

        def _filter_label(mode: str) -> str:
            return {
                "all": "все",
                "groups_channels": "группы+каналы",
                "channels": "каналы",
                "groups": "группы",
                "users": "пользователи",
            }.get(mode, mode)

        def _ch_width(ch: str) -> int:
            if not ch:
                return 0
            # Combining marks не занимают места
            if unicodedata.combining(ch):
                return 0
            # Управляющие/непечатные — считаем нулевой ширины
            cat = unicodedata.category(ch)
            if cat in ("Cc", "Cf"):
                return 0
            # East Asian wide/fullwidth обычно занимают 2 колонки
            if unicodedata.east_asian_width(ch) in ("W", "F"):
                return 2
            return 1

        def _wcswidth(text: str) -> int:
            total = 0
            for ch in text:
                total += _ch_width(ch)
            return total

        def _wrap_lines_display(s: str, width: int) -> List[str]:
            """
            Перенос строки по ширине экрана (в колонках терминала), учитывая wide Unicode.

            В отличие от textwrap.wrap(), здесь ширина считается в "колонках", чтобы
            curses не переносил хвост строки на следующую строку в колонку 0.
            """
            if width <= 1:
                return [""]
            text = (s or "").strip()
            if not text:
                return [""]

            def _flush_line(buf: List[str]) -> str:
                # убрать пробелы по краям, чтобы не генерить пустые "хвосты"
                return "".join(buf).strip()

            out: List[str] = []
            line: List[str] = []
            line_w = 0

            # word-wrap: переносим по пробелам, длинные слова дробим по символам
            words = text.split(" ")
            for wi, word in enumerate(words):
                if wi > 0:
                    sep = " "
                    sep_w = 1
                else:
                    sep = ""
                    sep_w = 0

                word_w = _wcswidth(word)

                # Если слово целиком не помещается даже в пустую строку — дробим
                if word_w > width:
                    if line:
                        out.append(_flush_line(line))
                        line = []
                        line_w = 0
                    chunk: List[str] = []
                    chunk_w = 0
                    for ch in word:
                        cw = _ch_width(ch)
                        if chunk_w + cw > width and chunk:
                            out.append(_flush_line(chunk))
                            chunk = []
                            chunk_w = 0
                        chunk.append(ch)
                        chunk_w += cw
                    if chunk:
                        out.append(_flush_line(chunk))
                    continue

                # Пробуем добавить слово (и пробел перед ним, если нужно)
                needed = sep_w + word_w
                if line and (line_w + needed) > width:
                    out.append(_flush_line(line))
                    line = []
                    line_w = 0
                    sep = ""
                    sep_w = 0
                    needed = word_w

                if sep:
                    line.append(sep)
                    line_w += sep_w
                line.append(word)
                line_w += word_w

            if line:
                out.append(_flush_line(line))
            return out or [""]

        def _truncate(s: str, width: int) -> str:
            """
            Обрезать строку по ширине экрана (в колонках терминала), а не по len().

            Это важно из‑за wide Unicode (эмодзи, CJK): иначе curses может перенести
            хвост строки на следующую строку в колонку 0, «залезая» в левую панель.
            """
            if width <= 1:
                return ""

            if _wcswidth(s) <= width:
                return s

            ell = "…"
            target = max(0, width - 1)
            out: List[str] = []
            cur = 0
            for ch in s:
                w = _ch_width(ch)
                if cur + w > target:
                    break
                out.append(ch)
                cur += w
            return "".join(out) + ell

        def _addstr_safe(stdscr, y: int, x: int, s: str, max_cols: int, attr: int = 0) -> None:  # noqa: ANN001
            """
            Безопасно вывести строку, гарантируя что она не выйдет за границы экрана.
            """
            if max_cols <= 0:
                return
            try:
                stdscr.addstr(y, x, _truncate(s, max_cols), attr)
            except Exception:
                # curses.error и любые проблемы рендера не должны падать
                return

        def _as_int(v: Any, default: int, *, min_value: int = 0, max_value: Optional[int] = None) -> int:
            try:
                iv = int(v)
            except Exception:
                return default
            if iv < min_value:
                return min_value
            if max_value is not None and iv > max_value:
                return max_value
            return iv

        def _as_bool(v: Any, default: bool) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                s = v.strip().lower()
                if s in ("1", "true", "yes", "y", "on"):
                    return True
                if s in ("0", "false", "no", "n", "off"):
                    return False
            return default

        def _normalize_message_text(text: Any, has_media: bool, include_media_placeholder: bool) -> str:
            if isinstance(text, str):
                t = text.replace("\n", " ").strip()
                if t:
                    return t
            if has_media and include_media_placeholder:
                return "<media>"
            return ""

        def _wrap_lines(s: str, width: int) -> List[str]:
            if width <= 1:
                return [""]
            if not s:
                return [""]
            return _wrap_lines_display(s, width)

        preview_messages_count = _as_int(preview_cfg.get("messages_count", 1), 1, min_value=1, max_value=100)
        fetch_mode_raw = str(preview_cfg.get("fetch_mode", "auto")).strip().lower()
        if fetch_mode_raw not in ("auto", "on_demand", "off"):
            fetch_mode_raw = "auto"
        fetch_mode = "off" if preview_messages_count <= 1 else ("on_demand" if fetch_mode_raw in ("auto", "on_demand") else "off")
        debounce_s = _as_int(preview_cfg.get("debounce_ms", 200), 200, min_value=0, max_value=10_000) / 1000.0
        cache_size = _as_int(preview_cfg.get("cache_size", 128), 128, min_value=0, max_value=10_000)
        cache_ttl_s = float(_as_int(preview_cfg.get("cache_ttl_s", 300), 300, min_value=0, max_value=86_400))
        wrap_enabled = _as_bool(preview_cfg.get("wrap", True), True)
        max_preview_lines = _as_int(preview_cfg.get("max_lines", 12), 12, min_value=1, max_value=10_000)
        show_loading = _as_bool(preview_cfg.get("show_loading", True), True)
        include_media_placeholder = _as_bool(preview_cfg.get("include_media_placeholder", True), True)
        loading_text = str(preview_cfg.get("loading_text", "Загружаю…"))
        error_text = str(preview_cfg.get("error_text", "Ошибка загрузки превью"))
        label_single = str(preview_cfg.get("label_single", "Последнее сообщение:"))
        label_multi = str(preview_cfg.get("label_multi", "Последние сообщения:"))
        poll_interval_s = _as_int(preview_cfg.get("poll_interval_ms", 33), 33, min_value=1, max_value=1000) / 1000.0

        def _prompt_input(stdscr, prompt: str, initial: str = "") -> str:  # noqa: ANN001
            """
            Простой ввод строки внизу экрана с поддержкой UTF-8 (русские буквы).

            Использует get_wch() для корректной работы с многобайтовыми символами.
            """
            height, width = stdscr.getmaxyx()
            y = height - 1
            prompt_s = str(prompt or "")
            x0 = min(len(prompt_s), max(0, width - 1))
            edit_w = max(0, width - x0 - 1)
            if edit_w <= 0:
                return initial

            # Переключаем в blocking режим для ввода
            try:
                stdscr.nodelay(False)
                stdscr.timeout(-1)
            except Exception:
                pass

            try:
                # Показать курсор
                try:
                    curses.curs_set(1)
                except Exception:
                    pass

                # Буфер редактирования
                buf = list(initial or "")
                cursor_pos = len(buf)

                while True:
                    # Отрисовка
                    stdscr.move(y, 0)
                    stdscr.clrtoeol()
                    stdscr.addstr(y, 0, _truncate(prompt_s, max(0, width - 1)))
                    
                    # Показываем текст с учётом ширины
                    visible_text = "".join(buf)[: max(0, edit_w - 1)]
                    try:
                        stdscr.addstr(y, x0, visible_text)
                    except Exception:
                        pass
                    
                    # Позиционируем курсор
                    cursor_x = x0 + min(cursor_pos, len(visible_text))
                    try:
                        stdscr.move(y, cursor_x)
                    except Exception:
                        pass
                    stdscr.refresh()

                    # Читаем символ (get_wch поддерживает UTF-8)
                    try:
                        ch = stdscr.get_wch()
                    except Exception:
                        # Фолбэк на getch при ошибках
                        try:
                            ch_code = stdscr.getch()
                            if ch_code == -1:
                                continue
                            ch = ch_code
                        except Exception:
                            continue

                    # Обработка специальных клавиш
                    if isinstance(ch, int):
                        # KEY_ константы или коды управляющих символов
                        if ch in (10, 13):  # Enter
                            break
                        elif ch == 27:  # ESC
                            return initial
                        elif ch in (curses.KEY_BACKSPACE, 127, 8):  # Backspace
                            if cursor_pos > 0:
                                buf.pop(cursor_pos - 1)
                                cursor_pos -= 1
                        elif ch == curses.KEY_DC:  # Delete
                            if cursor_pos < len(buf):
                                buf.pop(cursor_pos)
                        elif ch == curses.KEY_LEFT:
                            cursor_pos = max(0, cursor_pos - 1)
                        elif ch == curses.KEY_RIGHT:
                            cursor_pos = min(len(buf), cursor_pos + 1)
                        elif ch == curses.KEY_HOME:
                            cursor_pos = 0
                        elif ch == curses.KEY_END:
                            cursor_pos = len(buf)
                    elif isinstance(ch, str):
                        # Обычный символ (включая UTF-8 многобайтовые)
                        if ch.isprintable():
                            buf.insert(cursor_pos, ch)
                            cursor_pos += 1

                return "".join(buf).strip()

            finally:
                try:
                    curses.curs_set(0)
                except Exception:
                    pass
                try:
                    stdscr.nodelay(True)
                    stdscr.timeout(0)
                except Exception:
                    pass
                # Очистить строку ввода после завершения
                try:
                    stdscr.move(y, 0)
                    stdscr.clrtoeol()
                    stdscr.refresh()
                except Exception:
                    pass

        def _color_to_curses(color_name: Any) -> int:
            name = str(color_name).strip().lower()
            if name in ("default", "-1", "none"):
                return -1
            mapping = {
                "black": curses.COLOR_BLACK,
                "red": curses.COLOR_RED,
                "green": curses.COLOR_GREEN,
                "yellow": curses.COLOR_YELLOW,
                "blue": curses.COLOR_BLUE,
                "magenta": curses.COLOR_MAGENTA,
                "cyan": curses.COLOR_CYAN,
                "white": curses.COLOR_WHITE,
            }
            return mapping.get(name, curses.COLOR_WHITE)

        def _parse_key_spec(spec: Any) -> Set[int]:
            """
            Преобразовать спецификацию клавиш из конфига в набор keycodes.
            Поддержка:
            - строки: "q", "esc", "enter", "space", "up", "down", "pageup", ...
            - числа: 27, 10, ...
            - списки: ["q", "esc"]
            """
            out: Set[int] = set()
            if spec is None:
                return out
            if isinstance(spec, (list, tuple, set)):
                for s in spec:
                    out |= _parse_key_spec(s)
                return out
            if isinstance(spec, int):
                out.add(spec)
                return out

            raw = str(spec).strip()
            # Важно: одиночные символы должны быть регистрозависимыми.
            # Например, "K" (Shift+K) -> ord("K") != ord("k").
            if len(raw) == 1:
                out.add(ord(raw))
                return out

            key = raw.lower()
            if key == "enter":
                out |= {10, 13}
            elif key == "esc":
                out.add(27)
            elif key == "space":
                out.add(ord(" "))
            elif key == "up":
                out.add(curses.KEY_UP)
            elif key == "down":
                out.add(curses.KEY_DOWN)
            elif key in ("pageup", "pgup"):
                out.add(curses.KEY_PPAGE)
            elif key in ("pagedown", "pgdn"):
                out.add(curses.KEY_NPAGE)
            elif key == "home":
                out.add(curses.KEY_HOME)
            elif key == "end":
                out.add(curses.KEY_END)
            return out

        keymap = {
            "quit": _parse_key_spec(keys_cfg.get("quit", self._DEFAULT_TUI_CONFIG["keys"]["quit"])),
            "confirm": _parse_key_spec(keys_cfg.get("confirm", self._DEFAULT_TUI_CONFIG["keys"]["confirm"])),
            "toggle": _parse_key_spec(keys_cfg.get("toggle", self._DEFAULT_TUI_CONFIG["keys"]["toggle"])),
            "filter": _parse_key_spec(keys_cfg.get("filter", self._DEFAULT_TUI_CONFIG["keys"]["filter"])),
            "search": _parse_key_spec(keys_cfg.get("search", self._DEFAULT_TUI_CONFIG["keys"]["search"])),
            "clear": _parse_key_spec(keys_cfg.get("clear", self._DEFAULT_TUI_CONFIG["keys"]["clear"])),
            "show_selected": _parse_key_spec(keys_cfg.get("show_selected", self._DEFAULT_TUI_CONFIG["keys"]["show_selected"])),
            "up": _parse_key_spec(keys_cfg.get("up", self._DEFAULT_TUI_CONFIG["keys"]["up"])),
            "down": _parse_key_spec(keys_cfg.get("down", self._DEFAULT_TUI_CONFIG["keys"]["down"])),
            "page_up": _parse_key_spec(keys_cfg.get("page_up", self._DEFAULT_TUI_CONFIG["keys"]["page_up"])),
            "page_down": _parse_key_spec(keys_cfg.get("page_down", self._DEFAULT_TUI_CONFIG["keys"]["page_down"])),
            "home": _parse_key_spec(keys_cfg.get("home", self._DEFAULT_TUI_CONFIG["keys"]["home"])),
            "end": _parse_key_spec(keys_cfg.get("end", self._DEFAULT_TUI_CONFIG["keys"]["end"])),
            "move_up": _parse_key_spec(keys_cfg.get("move_up", self._DEFAULT_TUI_CONFIG["keys"]["move_up"])),
            "move_down": _parse_key_spec(keys_cfg.get("move_down", self._DEFAULT_TUI_CONFIG["keys"]["move_down"])),
        }

        async def _fetch_preview(chat_id: int, limit: int) -> List[str]:
            msgs = await self.client.get_messages(chat_id, limit=limit)
            out: List[str] = []
            for m in msgs:
                text = getattr(m, "message", None)
                has_media = getattr(m, "media", None) is not None
                norm = _normalize_message_text(text, has_media, include_media_placeholder)
                if norm:
                    out.append(norm)
            return out

        # LRU cache: chat_id -> (ts, [messages...])
        preview_cache: "OrderedDict[int, Tuple[float, List[str]]]" = OrderedDict()
        inflight: Optional[asyncio.Task[List[str]]] = None
        inflight_chat_id: Optional[int] = None
        last_fetch_error: Optional[str] = None
        last_fetch_error_chat_id: Optional[int] = None
        cursor_changed_at = time.monotonic()
        last_cursor_chat_id: Optional[int] = None

        stdscr = None
        try:
            stdscr = curses.initscr()
            curses.noecho()
            curses.cbreak()
            stdscr.keypad(True)
            curses.curs_set(0)
            stdscr.nodelay(True)
            stdscr.timeout(0)

            # Цвета (зависят от терминала). Если не поддерживаются — остаёмся на дефолтных атрибутах.
            header_attr = curses.A_BOLD
            footer_attr = curses.A_DIM
            sep_attr = curses.A_DIM
            list_attr = curses.A_NORMAL
            selected_attr: Optional[int] = None
            if curses.has_colors():
                curses.start_color()
                try:
                    curses.use_default_colors()
                except Exception:
                    pass
                screen_fg = _color_to_curses(colors_cfg.get("screen_fg", "default"))
                screen_bg = _color_to_curses(colors_cfg.get("screen_bg", "default"))
                header_fg = _color_to_curses(colors_cfg.get("header_fg", "cyan"))
                header_bg = _color_to_curses(colors_cfg.get("header_bg", "default"))
                footer_fg = _color_to_curses(colors_cfg.get("footer_fg", "white"))
                footer_bg = _color_to_curses(colors_cfg.get("footer_bg", "default"))
                sep_fg = _color_to_curses(colors_cfg.get("separator_fg", "blue"))
                sep_bg = _color_to_curses(colors_cfg.get("separator_bg", "default"))
                list_fg = _color_to_curses(colors_cfg.get("list_fg", "default"))
                list_bg = _color_to_curses(colors_cfg.get("list_bg", "default"))
                sel_fg = _color_to_curses(colors_cfg.get("selected_fg", "default"))
                sel_bg = _color_to_curses(colors_cfg.get("selected_bg", "default"))

                curses.init_pair(1, header_fg, header_bg)  # header
                curses.init_pair(2, footer_fg, footer_bg)  # footer
                curses.init_pair(3, sep_fg, sep_bg)        # separator
                curses.init_pair(4, list_fg, list_bg)      # list
                curses.init_pair(6, screen_fg, screen_bg)  # screen
                header_attr |= curses.color_pair(1)
                footer_attr |= curses.color_pair(2)
                sep_attr |= curses.color_pair(3)
                list_attr |= curses.color_pair(4)

                # Глобальный фон экрана (опционально)
                stdscr.bkgd(" ", curses.color_pair(6))

                # Явный цвет выделения строки списка (если задан не-default)
                if sel_fg != -1 or sel_bg != -1:
                    curses.init_pair(5, sel_fg, sel_bg)  # selected
                    selected_attr = curses.color_pair(5)

            while True:
                # Обработать завершение фонового запроса превью
                if inflight is not None and inflight.done():
                    try:
                        msgs = inflight.result()
                        if inflight_chat_id is not None:
                            preview_cache[inflight_chat_id] = (time.monotonic(), msgs)
                            preview_cache.move_to_end(inflight_chat_id)
                            if cache_size > 0:
                                while len(preview_cache) > cache_size:
                                    preview_cache.popitem(last=False)
                        last_fetch_error = None
                        last_fetch_error_chat_id = None
                    except Exception as e:  # noqa: BLE001
                        last_fetch_error = f"{error_text}: {e}"
                        last_fetch_error_chat_id = inflight_chat_id
                    finally:
                        inflight = None
                        inflight_chat_id = None

                filtered_items = _visible_items()
                stdscr.erase()
                height, width = stdscr.getmaxyx()

                header = str(text_cfg.get("header", self._DEFAULT_TUI_CONFIG["text"]["header"]))
                _addstr_safe(stdscr, 0, 0, header, max(0, width - 1), header_attr)

                if not filtered_items:
                    empty_msg = (
                        str(text_cfg.get("no_selected", self._DEFAULT_TUI_CONFIG["text"]["no_selected"]))
                        if show_selected_only
                        else str(text_cfg.get("no_chats", self._DEFAULT_TUI_CONFIG["text"]["no_chats"]))
                    )
                    _addstr_safe(
                        stdscr,
                        2,
                        0,
                        empty_msg,
                        max(0, width - 1),
                    )
                    meta0 = (
                        f"Режим: {'выбранные' if show_selected_only else 'все'} | "
                        f"Фильтр: {_filter_label(filter_mode)} | Поиск: {search_query or '—'}"
                    )
                    _addstr_safe(stdscr, 3, 0, meta0, max(0, width - 1))
                    stdscr.refresh()
                    k0 = stdscr.getch()
                    if k0 in keymap["quit"]:
                        return []
                    if k0 in keymap["confirm"]:
                        break
                    if k0 in keymap["show_selected"]:
                        show_selected_only = not show_selected_only
                        index = 0
                        offset = 0
                    if k0 in keymap["filter"]:
                        filter_mode = {
                            "all": "groups_channels",
                            "groups_channels": "channels",
                            "channels": "groups",
                            "groups": "users",
                            "users": "all",
                        }.get(filter_mode, "all")
                    if k0 in keymap["clear"]:
                        search_query = ""
                    if k0 in keymap["search"]:
                        search_query = _prompt_input(
                            stdscr,
                            str(
                                text_cfg.get(
                                    "search_prompt",
                                    self._DEFAULT_TUI_CONFIG["text"]["search_prompt"],
                                )
                            ),
                            initial=search_query,
                        ).strip()
                    await asyncio.sleep(poll_interval_s)
                    continue

                # Разделение на список/превью
                list_min_width = int(layout_cfg.get("list_min_width", 30))
                list_width_ratio = float(layout_cfg.get("list_width_ratio", 0.5))
                preview_min_width = int(layout_cfg.get("preview_min_width", 10))
                list_w = max(list_min_width, int(width * list_width_ratio))
                preview_w = max(0, width - list_w - 1)
                list_h = max(0, height - 2)

                # Нормализовать индекс при смене фильтра/поиска
                if index >= len(filtered_items):
                    index = max(0, len(filtered_items) - 1)

                # Поддержать offset так, чтобы курсор был виден
                if index < offset:
                    offset = index
                if index >= offset + list_h:
                    offset = max(0, index - list_h + 1)

                cur = filtered_items[index]
                if last_cursor_chat_id != cur.chat_id:
                    last_cursor_chat_id = cur.chat_id
                    cursor_changed_at = time.monotonic()

                # Запланировать on-demand подкачку превью (вариант B)
                if fetch_mode == "on_demand" and preview_messages_count > 1:
                    cached = preview_cache.get(cur.chat_id)
                    now = time.monotonic()
                    is_fresh = (
                        cached is not None
                        and (cache_ttl_s <= 0 or (now - cached[0]) <= cache_ttl_s)
                        and len(cached[1]) >= 1
                    )
                    should_fetch = (not is_fresh) and (inflight is None) and ((now - cursor_changed_at) >= debounce_s)
                    if should_fetch:
                        inflight_chat_id = cur.chat_id
                        inflight = asyncio.create_task(_fetch_preview(cur.chat_id, preview_messages_count))

                # Рендер списка
                selected_pos: Dict[int, int] = {cid: (idx_o + 1) for idx_o, cid in enumerate(selected_order)}
                for row in range(list_h):
                    i = offset + row
                    if i >= len(filtered_items):
                        break
                    it = filtered_items[i]
                    if it.chat_id in selected_pos:
                        pos = selected_pos[it.chat_id]
                        mark = f"[{pos:02d}]" if pos <= 99 else "[**]"
                    else:
                        mark = "[  ]"
                    chat_id_suffix = f" ({it.chat_id})" if show_chat_id else ""
                    line = f"{mark} {_type_label(it.chat_type)} {it.title}{chat_id_suffix}"
                    if i == index:
                        attr = selected_attr if selected_attr is not None else (curses.A_REVERSE | list_attr)
                    else:
                        attr = list_attr
                    _addstr_safe(stdscr, 1 + row, 0, line, max(0, list_w - 1), attr)

                # Рендер превью
                if preview_w >= preview_min_width:
                    _addstr_safe(stdscr, 1, list_w, "│", 1, sep_attr)

                    header_lines: List[str] = [f"{_type_label(cur.chat_type)} {cur.title}"]
                    if show_chat_id:
                        header_lines.append(f"chat_id: {cur.chat_id}")

                    # Получить тело превью
                    body_lines: List[str] = []
                    if preview_messages_count <= 1 or fetch_mode == "off":
                        label = label_single
                        text = cur.last_message_preview or "<пусто>"
                        if wrap_enabled:
                            body_lines = _wrap_lines(text, max(0, preview_w - 2))
                        else:
                            body_lines = [_truncate(text, max(0, preview_w - 2))]
                    else:
                        label = label_multi
                        cached = preview_cache.get(cur.chat_id)
                        now = time.monotonic()
                        fresh = (
                            cached is not None
                            and (cache_ttl_s <= 0 or (now - cached[0]) <= cache_ttl_s)
                            and len(cached[1]) >= 1
                        )
                        if fresh:
                            msgs = cached[1][:preview_messages_count]
                            prefix_w = len("10. ")
                            for idx_m, msg in enumerate(msgs, 1):
                                prefix = f"{idx_m}. "
                                if wrap_enabled:
                                    wrapped = _wrap_lines(msg, max(0, (preview_w - 2) - len(prefix)))
                                    for j, wl in enumerate(wrapped):
                                        body_lines.append((prefix if j == 0 else " " * len(prefix)) + wl)
                                else:
                                    body_lines.append(prefix + _truncate(msg, max(0, (preview_w - 2) - len(prefix))))
                                if len(body_lines) >= max_preview_lines:
                                    break
                        elif show_loading and inflight is not None and inflight_chat_id == cur.chat_id:
                            body_lines = [loading_text]
                        elif last_fetch_error and last_fetch_error_chat_id == cur.chat_id:
                            body_lines = [_truncate(last_fetch_error, max(0, preview_w - 2))]
                        else:
                            # Фолбэк (без сети пока нет данных)
                            text = cur.last_message_preview or "<пусто>"
                            body_lines = _wrap_lines(text, max(0, preview_w - 2)) if wrap_enabled else [_truncate(text, max(0, preview_w - 2))]

                    # Собрать и вывести, ограничив высоту
                    info_lines: List[str] = []
                    info_lines.extend(header_lines)
                    info_lines.append("")
                    info_lines.append(label)
                    if not body_lines:
                        body_lines = ["<пусто>"]
                    if wrap_enabled:
                        info_lines.extend(body_lines[:max_preview_lines])
                    else:
                        info_lines.extend([_truncate(x, max(0, preview_w - 2)) for x in body_lines[:max_preview_lines]])

                    y = 1
                    for idx_line, part in enumerate(info_lines):
                        if y >= height:
                            break
                        attr_line = curses.A_BOLD if idx_line == 0 else curses.A_NORMAL
                        # Чтобы curses не переносил wide‑символы в колонку 0 следующей строки,
                        # оставляем небольшой запас (−1 колонка).
                        _addstr_safe(stdscr, y, list_w + 1, part, max(0, preview_w - 3), attr_line)
                        y += 1

                meta = (
                    f"Режим: {'выбранные' if show_selected_only else 'все'} | "
                    f"Фильтр: {_filter_label(filter_mode)} | "
                    f"Поиск: {search_query or '—'} | "
                    f"Показано: {len(filtered_items)}/{len(items)} | "
                    f"Выбрано: {len(selected)}"
                )
                if height > 1:
                    _addstr_safe(stdscr, height - 1, 0, meta, max(0, width - 1), footer_attr)

                stdscr.refresh()
                key = stdscr.getch()

                if key != -1:
                    if key in keymap["quit"]:
                        return []
                    if key in keymap["confirm"]:
                        break
                    if key in keymap["show_selected"]:
                        show_selected_only = not show_selected_only
                        index = 0
                        offset = 0
                    if key in keymap["toggle"]:
                        cid = filtered_items[index].chat_id
                        if cid in selected:
                            selected.remove(cid)
                            try:
                                selected_order.remove(cid)
                            except ValueError:
                                pass
                        else:
                            selected.add(cid)
                            selected_order.append(cid)
                    elif key in keymap["move_up"]:
                        cid = filtered_items[index].chat_id
                        if cid in selected:
                            try:
                                p = selected_order.index(cid)
                            except ValueError:
                                p = -1
                            if p > 0:
                                selected_order[p - 1], selected_order[p] = selected_order[p], selected_order[p - 1]
                                # В режиме "только выбранные" курсор должен ехать вместе с чатом
                                if show_selected_only:
                                    index = p - 1
                    elif key in keymap["move_down"]:
                        cid = filtered_items[index].chat_id
                        if cid in selected:
                            try:
                                p = selected_order.index(cid)
                            except ValueError:
                                p = -1
                            if 0 <= p < (len(selected_order) - 1):
                                selected_order[p + 1], selected_order[p] = selected_order[p], selected_order[p + 1]
                                # В режиме "только выбранные" курсор должен ехать вместе с чатом
                                if show_selected_only:
                                    index = p + 1
                    elif key in keymap["clear"]:
                        search_query = ""
                        index = 0
                        offset = 0
                    elif key in keymap["search"]:
                        search_query = _prompt_input(
                            stdscr,
                            str(
                                text_cfg.get(
                                    "search_prompt",
                                    self._DEFAULT_TUI_CONFIG["text"]["search_prompt"],
                                )
                            ),
                            initial=search_query,
                        ).strip()
                        index = 0
                        offset = 0
                    elif key in keymap["filter"]:
                        filter_mode = {
                            "all": "groups_channels",
                            "groups_channels": "channels",
                            "channels": "groups",
                            "groups": "users",
                            "users": "all",
                        }.get(filter_mode, "all")
                        index = 0
                        offset = 0
                    elif key in keymap["up"]:
                        index = max(0, index - 1)
                    elif key in keymap["down"]:
                        index = min(len(filtered_items) - 1, index + 1)
                    elif key in keymap["page_up"]:
                        index = max(0, index - max(1, list_h))
                    elif key in keymap["page_down"]:
                        index = min(len(filtered_items) - 1, index + max(1, list_h))
                    elif key in keymap["home"]:
                        index = 0
                    elif key in keymap["end"]:
                        index = len(filtered_items) - 1

                # Ограничить частоту перерисовки, чтобы UI не "мигал" на быстрых повторах клавиш
                await asyncio.sleep(poll_interval_s)
        finally:
            if inflight is not None and not inflight.done():
                inflight.cancel()
            if stdscr is not None:
                try:
                    stdscr.keypad(False)
                except Exception:
                    pass
            try:
                curses.nocbreak()
                curses.echo()
                curses.endwin()
            except Exception:
                pass
            # Восстановить stdout/stderr и обработчики логгера
            try:
                sys.stdout = saved_stdout
                sys.stderr = saved_stderr
            except Exception:
                pass
            try:
                if tui_file_handler is not None:
                    try:
                        root_logger.removeHandler(tui_file_handler)
                    except Exception:
                        pass
                    try:
                        tui_file_handler.close()
                    except Exception:
                        pass
                # Восстановить исходные handlers как были
                root_logger.handlers = saved_handlers  # type: ignore[assignment]
                root_logger.setLevel(saved_root_level)
            except Exception:
                pass
            try:
                if tui_log_file is not None:
                    tui_log_file.flush()
                    tui_log_file.close()
            except Exception:
                pass

        by_id: Dict[int, ChatListItem] = {i.chat_id: i for i in items}
        out: List[Tuple[int, str, str]] = []
        used: Set[int] = set()
        for cid in selected_order:
            it = by_id.get(cid)
            if it is None:
                continue
            if cid not in selected:
                continue
            out.append((it.chat_id, it.title, it.chat_type))
            used.add(cid)
        # На всякий случай добавить выбранные, которые почему-то не попали в order
        for it in items:
            if it.chat_id in selected and it.chat_id not in used:
                out.append((it.chat_id, it.title, it.chat_type))
        return out

    async def select_chats(
        self,
        allow_multiple: bool = True,
        ui: str = "classic",
        preselected_chat_ids: Optional[Set[int]] = None,
        preselected_chat_id_order: Optional[Sequence[int]] = None,
    ) -> List[Tuple[int, str, str]]:
        """
        Получить и выбрать чаты.

        Parameters
        ----------
        allow_multiple: bool
            Разрешить выбор нескольких чатов.
        ui: str
            Интерфейс выбора: "classic" (текущий) или "tui" (клавиатурный).
        preselected_chat_ids: Optional[Set[int]]
            Предварительно выбранные chat_id (например из config.yaml).
        preselected_chat_id_order: Optional[Sequence[int]]
            Предварительно выбранные chat_id в порядке очереди (например из config.yaml).

        Returns
        -------
        List[Tuple[int, str, str]]
            Список выбранных чатов.
        """
        # Включаем локаль по умолчанию, чтобы curses принимал UTF-8 (русские буквы в поиске).
        try:
            locale.setlocale(locale.LC_ALL, "")
        except Exception:
            pass
        self.console.print("[bold cyan]Получение списка чатов...[/bold cyan]")
        if ui == "tui":
            items = await self.get_available_chat_items()
            if allow_multiple:
                return await self._select_chats_tui(items, preselected_chat_ids, preselected_chat_id_order)
            chats = [(i.chat_id, i.title, i.chat_type) for i in items]
        else:
            chats = await self.get_available_chats()

        if not chats:
            self.console.print("[red]Нет доступных чатов[/red]")
            return []

        if allow_multiple:
            return self.select_chats_interactive(chats)
        else:
            # Выбор одного чата
            self.display_chats(chats)
            while True:
                try:
                    choice = int(
                        Prompt.ask(
                            f"Выберите номер чата (1-{len(chats)})",
                            default="1",
                        )
                    )
                    if 1 <= choice <= len(chats):
                        return [chats[choice - 1]]
                    else:
                        self.console.print(
                            f"[red]Неверный номер. Выберите от 1 до {len(chats)}[/red]"
                        )
                except ValueError:
                    self.console.print("[red]Введите число[/red]")
