"""Модуль для интерактивного выбора чатов."""
from typing import Dict, List, Optional, Tuple

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from telethon import TelegramClient
from telethon.tl.types import Chat, User

from utils.i18n import get_i18n


class ChatSelector:
    """Класс для интерактивного выбора чатов."""

    def __init__(self, client: TelegramClient, language: str = "ru"):
        """
        Инициализация ChatSelector.

        Parameters
        ----------
        client: TelegramClient
            Клиент Telethon.
        language: str
            Язык интерфейса.
        """
        self.client = client
        self.i18n = get_i18n(language)
        self.console = Console()
        self.page_size = 50  # Количество чатов на странице

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

    async def select_chats(
        self, allow_multiple: bool = True
    ) -> List[Tuple[int, str, str]]:
        """
        Получить и выбрать чаты.

        Parameters
        ----------
        allow_multiple: bool
            Разрешить выбор нескольких чатов.

        Returns
        -------
        List[Tuple[int, str, str]]
            Список выбранных чатов.
        """
        self.console.print("[bold cyan]Получение списка чатов...[/bold cyan]")
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
