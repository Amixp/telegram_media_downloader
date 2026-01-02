"""Модуль локализации для поддержки русского и английского языков."""
import locale
from typing import Dict, Optional


class I18n:
    """Класс для управления переводами."""

    def __init__(self, language: Optional[str] = None):
        """
        Инициализация I18n.

        Parameters
        ----------
        language: Optional[str]
            Язык ('ru' или 'en'). Если None, определяется автоматически.
        """
        if language is None:
            language = self._detect_language()
        self.language = language
        self._translations = self._load_translations()

    @staticmethod
    def _detect_language() -> str:
        """
        Определить язык системы.

        Returns
        -------
        str
            Код языка ('ru' или 'en').
        """
        try:
            system_lang = locale.getdefaultlocale()[0]
            if system_lang and system_lang.startswith("ru"):
                return "ru"
        except Exception:
            pass
        return "en"

    def _load_translations(self) -> Dict[str, Dict[str, str]]:
        """
        Загрузить переводы.

        Returns
        -------
        Dict[str, Dict[str, str]]
            Словарь переводов.
        """
        return {
            "ru": {
                # Общие сообщения
                "config_not_found": "Файл конфигурации не найден: {path}",
                "config_loaded": "Конфигурация загружена",
                "config_saved": "Конфигурация сохранена",
                "config_updated": "Конфигурация обновлена",
                # Ошибки валидации
                "missing_field": "Отсутствует обязательное поле: {field}",
                "invalid_api_id": "api_id должен быть целым числом",
                "invalid_api_hash": "api_hash должен быть непустой строкой",
                "invalid_media_type": "Некорректный тип медиа: {type}",
                "invalid_file_formats": "file_formats должен быть словарем",
                # Загрузка
                "downloading": "Загрузка {file}",
                "downloaded": "Медиа загружено - {path}",
                "download_failed": "Не удалось загрузить {count} файлов",
                "retrying": "Повторная попытка загрузки файлов, которые не удалось загрузить ранее...",
                "file_reference_expired": "Ссылка на файл истекла для сообщения[{id}], повторная попытка...",
                "file_reference_expired_skip": "Ссылка на файл истекла для сообщения[{id}] после 3 попыток, пропуск загрузки.",
                "timeout_error": "Ошибка таймаута при загрузке сообщения[{id}], повтор через 5 секунд",
                "timeout_skip": "Таймаут для сообщения[{id}] после 3 попыток, пропуск загрузки.",
                "download_exception": "Не удалось загрузить сообщение[{id}] из-за исключения:\n[{error}].",
                "processed_batch": "Обработана партия из {count} сообщений",
                # Состояние
                "updated_message_id": "Обновлен ID последнего прочитанного сообщения в файле конфигурации",
                "failed_ids_added": "ID неудачных загрузок добавлены в файл конфигурации.\nЭти файлы будут загружены при следующем запуске.",
                # Фильтры
                "start_date_filter": "Фильтр начальной даты: {date}",
                "end_date_filter": "Фильтр конечной даты: {date}",
                "max_messages": "Максимум сообщений для загрузки: {count}",
                "download_directory": "Директория загрузки: {dir}",
                "download_directory_default": "Директория загрузки: По умолчанию",
                # История сообщений
                "saving_history": "Сохранение истории сообщений",
                "history_saved": "История сохранена: {path}",
                # Выбор чатов
                "selecting_chats": "Выбор чатов для загрузки",
                "no_chats_available": "Нет доступных чатов",
                "chat_selected": "Выбран чат: {title}",
                # Прогресс
                "progress_downloading": "Загрузка",
                "progress_completed": "Завершено",
                "progress_failed": "Ошибка",
            },
            "en": {
                # General messages
                "config_not_found": "Configuration file not found: {path}",
                "config_loaded": "Configuration loaded",
                "config_saved": "Configuration saved",
                "config_updated": "Configuration updated",
                # Validation errors
                "missing_field": "Missing required field: {field}",
                "invalid_api_id": "api_id must be an integer",
                "invalid_api_hash": "api_hash must be a non-empty string",
                "invalid_media_type": "Invalid media type: {type}",
                "invalid_file_formats": "file_formats must be a dictionary",
                # Download
                "downloading": "Downloading {file}",
                "downloaded": "Media downloaded - {path}",
                "download_failed": "Failed to download {count} files",
                "retrying": "Downloading files failed during last run...",
                "file_reference_expired": "File reference expired for message[{id}], refetching...",
                "file_reference_expired_skip": "File reference expired for message[{id}] after 3 retries, download skipped.",
                "timeout_error": "Timeout error occurred when downloading message[{id}], retrying after 5 seconds",
                "timeout_skip": "Timing out for message[{id}] after 3 retries, download skipped.",
                "download_exception": "Could not download message[{id}] due to following exception:\n[{error}].",
                "processed_batch": "Processed batch of {count} messages",
                # State
                "updated_message_id": "Updated last read message_id to config file",
                "failed_ids_added": "Failed message ids are added to config file.\nThese files will be downloaded on the next run.",
                # Filters
                "start_date_filter": "Start date filter: {date}",
                "end_date_filter": "End date filter: {date}",
                "max_messages": "Max messages to download: {count}",
                "download_directory": "Download directory: {dir}",
                "download_directory_default": "Download directory: Default",
                # Message history
                "saving_history": "Saving message history",
                "history_saved": "History saved: {path}",
                # Chat selection
                "selecting_chats": "Selecting chats for download",
                "no_chats_available": "No chats available",
                "chat_selected": "Chat selected: {title}",
                # Progress
                "progress_downloading": "Downloading",
                "progress_completed": "Completed",
                "progress_failed": "Failed",
            },
        }

    def t(self, key: str, **kwargs) -> str:
        """
        Получить перевод по ключу.

        Parameters
        ----------
        key: str
            Ключ перевода.
        **kwargs
            Параметры для форматирования строки.

        Returns
        -------
        str
            Переведенная строка.
        """
        translation = self._translations.get(self.language, {}).get(
            key, self._translations.get("en", {}).get(key, key)
        )
        try:
            return translation.format(**kwargs)
        except KeyError:
            return translation

    def set_language(self, language: str) -> None:
        """
        Установить язык.

        Parameters
        ----------
        language: str
            Код языка ('ru' или 'en').
        """
        if language in self._translations:
            self.language = language
        else:
            self.language = "en"


# Глобальный экземпляр для использования в проекте
_i18n_instance: Optional[I18n] = None


def get_i18n(language: Optional[str] = None) -> I18n:
    """
    Получить экземпляр I18n.

    Parameters
    ----------
    language: Optional[str]
        Язык. Если None, используется существующий экземпляр или создается новый.

    Returns
    -------
    I18n
        Экземпляр I18n.
    """
    global _i18n_instance
    if _i18n_instance is None or language is not None:
        _i18n_instance = I18n(language)
    return _i18n_instance


def _(key: str, **kwargs) -> str:
    """
    Удобная функция для получения перевода.

    Parameters
    ----------
    key: str
        Ключ перевода.
    **kwargs
        Параметры для форматирования.

    Returns
    -------
    str
        Переведенная строка.
    """
    return get_i18n().t(key, **kwargs)
