"""Настройки программы и чтение OAuth-токена Яндекс.Диска."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Название папки на Яндекс.Диске совпадает с названием учебной группы.
GROUP_FOLDER = "AIE-9"

CATAAS_BASE_URL = "https://cataas.com"
YANDEX_DISK_API_URL = "https://cloud-api.yandex.net/v1/disk"

# Имя переменной окружения, в которой хранится токен.
TOKEN_ENV_VAR = "YANDEX_DISK_TOKEN"

RESULT_FILE = "result.json"

# Тайм-аут одного HTTP-запроса (подключение, чтение), в секундах.
REQUEST_TIMEOUT = (5, 30)

# Сколько всего ждать завершения загрузки и как часто проверять статус.
UPLOAD_WAIT_SECONDS = 60
UPLOAD_POLL_INTERVAL = 2

# Максимальная длина текста надписи.
MAX_TEXT_LENGTH = 100


# Файл .env ищется только в папке проекта (рядом с этим файлом),
# а не в текущей папке и не в родительских каталогах.
DOTENV_PATH = Path(__file__).resolve().parent / ".env"


def load_token(dotenv_path=DOTENV_PATH):
    """Вернуть токен из окружения или из файла .env, иначе None.

    Уже заданная переменная окружения важнее значения из .env.
    """
    load_dotenv(dotenv_path, override=False)
    token = os.getenv(TOKEN_ENV_VAR, "").strip()
    return token or None
