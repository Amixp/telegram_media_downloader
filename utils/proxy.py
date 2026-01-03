"""Модуль для работы с прокси."""
import logging
from typing import Any, Dict, Optional

import socks

logger = logging.getLogger(__name__)


def get_proxy_config(config: Dict[str, Any]) -> Optional[tuple]:
    """
    Получить конфигурацию прокси из настроек.

    Parameters
    ----------
    config: Dict[str, Any]
        Конфигурация приложения.

    Returns
    -------
    Optional[tuple]
        Кортеж (proxy_type, addr, port, rdns, username, password) или None.
    """
    proxy_config = config.get("proxy")
    if not proxy_config:
        return None

    # Проверка обязательных полей
    if not all(k in proxy_config for k in ["scheme", "hostname", "port"]):
        logger.warning("⚠️ Прокси настроен неполностью (нужны: scheme, hostname, port)")
        return None

    # Преобразование scheme в тип прокси
    scheme = proxy_config["scheme"].lower()
    proxy_type_map = {
        "socks4": socks.SOCKS4,
        "socks5": socks.SOCKS5,
        "http": socks.HTTP,
    }

    if scheme not in proxy_type_map:
        logger.error(
            f"❌ Неподдерживаемый тип прокси: {scheme}. "
            f"Допустимые: {', '.join(proxy_type_map.keys())}"
        )
        return None

    proxy_type = proxy_type_map[scheme]
    hostname = proxy_config["hostname"]
    port = proxy_config["port"]
    username = proxy_config.get("username")
    password = proxy_config.get("password")

    # Валидация порта
    if not isinstance(port, int) or not (1 <= port <= 65535):
        logger.error(f"❌ Некорректный порт прокси: {port}")
        return None

    logger.info(f"🔐 Использую прокси: {scheme}://{hostname}:{port}")
    if username:
        logger.info(f"   Аутентификация: {username}")

    # Возвращаем кортеж в формате Telethon
    return (proxy_type, hostname, port, True, username, password)


def validate_proxy_config(proxy_config: Optional[Dict[str, Any]]) -> bool:
    """
    Валидация конфигурации прокси.

    Parameters
    ----------
    proxy_config: Optional[Dict[str, Any]]
        Конфигурация прокси.

    Returns
    -------
    bool
        True если конфигурация валидна или отсутствует, False если невалидна.
    """
    if not proxy_config:
        return True

    # Проверка типа
    if not isinstance(proxy_config, dict):
        logger.error("❌ proxy должен быть словарем")
        return False

    # Проверка обязательных полей
    required_fields = ["scheme", "hostname", "port"]
    for field in required_fields:
        if field not in proxy_config:
            logger.error(f"❌ Отсутствует обязательное поле прокси: {field}")
            return False

    # Проверка scheme
    valid_schemes = ["socks4", "socks5", "http"]
    scheme = proxy_config["scheme"].lower()
    if scheme not in valid_schemes:
        logger.error(
            f"❌ Некорректный тип прокси: {scheme}. "
            f"Допустимые: {', '.join(valid_schemes)}"
        )
        return False

    # Проверка hostname
    hostname = proxy_config["hostname"]
    if not isinstance(hostname, str) or not hostname.strip():
        logger.error("❌ hostname прокси должен быть непустой строкой")
        return False

    # Проверка port
    port = proxy_config["port"]
    if not isinstance(port, int) or not (1 <= port <= 65535):
        logger.error(f"❌ Некорректный порт прокси: {port} (должен быть 1-65535)")
        return False

    # Проверка необязательных полей
    if "username" in proxy_config:
        username = proxy_config["username"]
        if not isinstance(username, str):
            logger.error("❌ username прокси должен быть строкой")
            return False

    if "password" in proxy_config:
        password = proxy_config["password"]
        if not isinstance(password, str):
            logger.error("❌ password прокси должен быть строкой")
            return False

    return True
