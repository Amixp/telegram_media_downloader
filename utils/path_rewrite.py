"""
Преобразование путей к файлам для отображения под другой базовой папкой (media_links_base).
"""
import os


def to_display_path(real_path: str, base_directory: str, media_links_base: str) -> str:
    """
    Преобразовать фактический путь в «отображаемый» под media_links_base.

    Если media_links_base пустой — возвращается real_path без изменений.
    Иначе: если real_path находится под base_directory (нормализованные абсолютные пути),
    префикс заменяется на media_links_base с сохранением относительного хвоста.
    Пути вне base_directory возвращаются как есть.

    Parameters
    ----------
    real_path : str
        Фактический путь к файлу (абсолютный или относительный).
    base_directory : str
        Базовая директория загрузок (префикс для замены).
    media_links_base : str
        Новая базовая папка для ссылок. Пустая строка = без подмены.

    Returns
    -------
    str
        Путь для записи в историю/ClickHouse/HTML.
    """
    if not media_links_base or not media_links_base.strip():
        return real_path
    if not real_path or not real_path.strip():
        return real_path

    base = base_directory.strip()
    new_base = media_links_base.strip()
    path = real_path.strip()

    if not base:
        return real_path

    # Привести к абсолютным путям для сравнения (относительный — относительно base)
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(base, path))
    else:
        path = os.path.normpath(path)

    base_abs = os.path.normpath(os.path.abspath(base))
    new_base_abs = os.path.normpath(os.path.abspath(new_base))

    try:
        common = os.path.commonpath([path, base_abs])
    except ValueError:
        return real_path
    if common != base_abs:
        return real_path
    rel = os.path.relpath(path, base_abs)
    if rel.startswith(".."):
        return real_path
    return os.path.normpath(os.path.join(new_base_abs, rel))
