"""Validation and normalization shared by Telegram and the Mini App."""
import math

import httpx

from config import CUSTOM_PROVIDER_ORIGINS

NUMERIC_LIMITS = {
    "temperature": (0, 1, 0.75),
    "min_delay": (0, 60, 6),
    "max_delay": (0, 60, 14),
    "human_takeover_minutes": (0, 1440, 15),
}
TOGGLES = {"simulate_typing", "split_messages", "is_paused"}


def number(settings, name):
    minimum, maximum, default = NUMERIC_LIMITS[name]
    try:
        value = float(settings.get(name, default))
    except (ValueError, TypeError):
        return default
    return value if math.isfinite(value) and minimum <= value <= maximum else default


def reply_delays(settings):
    minimum = number(settings, "min_delay")
    return minimum, max(minimum, number(settings, "max_delay"))


def custom_endpoint(value):
    try:
        url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        raise ValueError("Укажите корректный URL провайдера.") from None
    if (
        url.scheme not in {"http", "https"} or not url.host or url.userinfo
        or url.query or url.fragment or any(char in value for char in ("\\", "?", "#"))
    ):
        raise ValueError("Укажите HTTP(S) URL без логина, параметров и фрагмента.")
    origin = f"{url.scheme}://{url.netloc.decode('ascii')}"
    if origin not in CUSTOM_PROVIDER_ORIGINS:
        raise ValueError("Адрес провайдера не входит в список CUSTOM_PROVIDER_ORIGINS.")
    path = url.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return str(url.copy_with(path=path))


def validate_settings(data, current):
    changes = {}
    for key, value in data.items():
        if not isinstance(value, str):
            raise ValueError("Значения настроек должны быть строками.")
        value = value.strip()
        if key in NUMERIC_LIMITS:
            minimum, maximum, _ = NUMERIC_LIMITS[key]
            try:
                parsed = float(value)
            except ValueError:
                raise ValueError(f"{key}: укажите число от {minimum} до {maximum}.") from None
            if not minimum <= parsed <= maximum:
                raise ValueError(f"{key}: укажите число от {minimum} до {maximum}.")
            value = str(parsed)
        elif key in TOGGLES:
            if value not in {"0", "1"}:
                raise ValueError("Значение переключателя должно быть 0 или 1.")
        elif key == "custom_api_key":
            if "****" in value:
                raise ValueError("Введите ключ целиком вместо маски.")
            if len(value) > 2048 or (value and (not value.isascii() or not value.isprintable() or any(c.isspace() for c in value))):
                raise ValueError("Ключ API имеет недопустимый формат.")
        elif key == "custom_base_url":
            if value:
                custom_endpoint(value)
        elif key in {"custom_prompt", "custom_model_name"}:
            if len(value) > (4000 if key == "custom_prompt" else 256):
                raise ValueError("Значение настройки слишком длинное.")
        else:
            raise ValueError("Неизвестная настройка.")
        changes[key] = value

    if "custom_base_url" in changes and "custom_api_key" not in changes:
        old_url = current.get("custom_base_url", "")
        new_url = changes["custom_base_url"]
        if old_url != new_url:
            try:
                old_origin = httpx.URL(old_url).copy_with(path="/", query=None, fragment=None)
                new_origin = httpx.URL(new_url).copy_with(path="/", query=None, fragment=None)
                same_origin = old_origin == new_origin
            except httpx.InvalidURL:
                same_origin = False
            if not same_origin:
                changes["custom_api_key"] = ""

    merged = {**current, **changes}
    if {"min_delay", "max_delay"} & changes.keys():
        if number(merged, "min_delay") > number(merged, "max_delay"):
            raise ValueError("Минимальная задержка не может превышать максимальную.")
    if {"custom_base_url", "custom_model_name"} & changes.keys():
        if not merged.get("custom_base_url") or not merged.get("custom_model_name"):
            raise ValueError("Укажите Base URL и ID модели.")
        custom_endpoint(merged["custom_base_url"])

    return changes
