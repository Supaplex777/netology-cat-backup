"""Работа с CATAAS: проверка текста, URL картинки и имя файла."""

import re
from urllib.parse import quote

from config import CATAAS_BASE_URL, MAX_TEXT_LENGTH

# Символы, которые нельзя использовать в именах файлов
# (Windows, Linux, macOS и Яндекс.Диск), и управляющие символы.
FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
SPACES = re.compile(r"\s+")

# Расширения файлов для форматов, которые может вернуть CATAAS.
EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
DEFAULT_EXTENSION = ".jpg"


class InvalidTextError(ValueError):
    """Текст надписи пустой или не подходит для картинки."""


def validate_text(text):
    """Проверить текст надписи и вернуть его без лишних пробелов."""
    if text is None:
        raise InvalidTextError("Текст надписи не указан.")
    cleaned = SPACES.sub(" ", text).strip()
    if not cleaned:
        raise InvalidTextError("Текст надписи не может быть пустым.")
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise InvalidTextError(
            f"Текст слишком длинный: {len(cleaned)} символов "
            f"(максимум {MAX_TEXT_LENGTH})."
        )
    return cleaned


def build_cat_url(text, base_url=CATAAS_BASE_URL):
    """Сформировать URL картинки кошки с надписью.

    Весь текст кодируется (включая «/», «?» и «#»), поэтому пробелы,
    кириллица и спецсимволы попадают в надпись, а не ломают адрес.
    """
    cleaned = validate_text(text)
    encoded = quote(cleaned, safe="")
    return f"{base_url.rstrip('/')}/cat/says/{encoded}"


def make_file_stem(text):
    """Сделать из текста надписи безопасную основу имени файла."""
    cleaned = validate_text(text)
    stem = FORBIDDEN_CHARS.sub("_", cleaned)
    stem = SPACES.sub(" ", stem).strip(" .")
    if not stem.strip("_ "):
        raise InvalidTextError(
            "После удаления недопустимых символов имя файла пустое. "
            "Используйте буквы или цифры."
        )
    return stem


def extension_for(mime_type):
    """Вернуть расширение файла для MIME-типа картинки."""
    if not mime_type:
        return DEFAULT_EXTENSION
    return EXTENSIONS.get(mime_type.split(";")[0].strip().lower(),
                          DEFAULT_EXTENSION)
