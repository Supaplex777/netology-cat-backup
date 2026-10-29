"""Клиент REST API Яндекс.Диска для задачи резервного копирования."""

import logging
import time
from urllib.parse import urlsplit

import requests

from config import (
    REQUEST_TIMEOUT,
    UPLOAD_POLL_INTERVAL,
    UPLOAD_WAIT_SECONDS,
    YANDEX_DISK_API_URL,
)

logger = logging.getLogger(__name__)

# Сколько вариантов имени «Имя (N)» перебирать при совпадении имён.
MAX_NAME_ATTEMPTS = 100


class YandexDiskError(Exception):
    """Общая ошибка при работе с Яндекс.Диском."""


class AuthorizationError(YandexDiskError):
    """Токен отсутствует, недействителен или у него нет прав."""


class ConnectionProblemError(YandexDiskError):
    """Сеть недоступна или сервер не ответил вовремя."""


class RateLimitError(YandexDiskError):
    """Превышено ограничение API на количество запросов."""


class ForbiddenError(YandexDiskError):
    """Доступ запрещён или хранилище переполнено (HTTP 403)."""


class NotFoundError(YandexDiskError):
    """Ресурс не найден (HTTP 404)."""


class ConflictError(YandexDiskError):
    """Ресурс с таким путём уже существует."""


class ServerError(YandexDiskError):
    """Сервер Яндекс.Диска временно недоступен (HTTP 5xx)."""


class UnexpectedResponseError(YandexDiskError):
    """Ответ API не похож на описанный в документации."""


class UploadFailedError(YandexDiskError):
    """Яндекс.Диск не смог скачать файл по ссылке."""


class UploadTimeoutError(YandexDiskError):
    """Загрузка не завершилась за отведённое время."""


# Понятные пояснения к кодам, которые перечислены в документации API.
STATUS_HINTS = {
    400: "некорректные данные запроса",
    404: "ресурс не найден",
    406: "ресурс нельзя представить в нужном формате",
    413: "файл слишком большой для вашего тарифа",
    423: "технические работы или исчерпан лимит загрузок",
    507: "на Диске не хватает свободного места",
}

MAX_DETAILS_LENGTH = 200


def _error_text(response):
    """Достать из ответа API понятное описание ошибки (без секретов).

    Тело ответа может быть пустым, не-JSON или JSON не того типа
    (список, строка, число, null) — во всех случаях возвращается строка.
    """
    fallback = (STATUS_HINTS.get(getattr(response, "status_code", 0)) or
                getattr(response, "reason", "") or
                "нет описания")
    try:
        data = response.json()
    except ValueError:
        return fallback
    if isinstance(data, dict):
        for key in ("description", "message", "error"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:MAX_DETAILS_LENGTH]
    return fallback


def _json_object(response, action):
    """Вернуть тело ответа как словарь или выбросить понятную ошибку."""
    try:
        data = response.json()
    except ValueError as error:
        raise UnexpectedResponseError(
            f"{action}: Яндекс.Диск вернул ответ не в формате JSON "
            f"(HTTP {response.status_code})."
        ) from error
    if not isinstance(data, dict):
        raise UnexpectedResponseError(
            f"{action}: ожидался JSON-объект, получен "
            f"{type(data).__name__} (HTTP {response.status_code})."
        )
    return data


def _link_href(response, action):
    """Достать ссылку href из объекта Link, который возвращает API."""
    data = _json_object(response, action)
    href = data.get("href")
    if not isinstance(href, str) or not href:
        raise UnexpectedResponseError(
            f"{action}: в ответе нет ссылки на операцию."
        )
    if data.get("templated") is True:
        raise UnexpectedResponseError(
            f"{action}: получена шаблонная ссылка, её нельзя использовать."
        )
    return href


class YandexDiskClient:
    """Небольшая обёртка над REST API Яндекс.Диска."""

    def __init__(self, token, api_url=YANDEX_DISK_API_URL, session=None,
                 timeout=REQUEST_TIMEOUT, sleep=time.sleep,
                 clock=time.monotonic):
        if not token:
            raise AuthorizationError("Не указан OAuth-токен Яндекс.Диска.")
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._session = session or requests.Session()
        self._session.headers.update({
            "Authorization": f"OAuth {token}",
            "Accept": "application/json",
        })

    def __repr__(self):
        # Токен намеренно не показывается.
        return f"YandexDiskClient(api_url={self.api_url!r})"

    # --- Низкоуровневые запросы -------------------------------------------

    def _request(self, method, url, **kwargs):
        """Выполнить запрос и превратить сетевые сбои в понятные ошибки."""
        try:
            return self._session.request(
                method, url, timeout=self.timeout, **kwargs
            )
        except requests.Timeout as error:
            raise ConnectionProblemError(
                "Яндекс.Диск не ответил вовремя (тайм-аут запроса)."
            ) from error
        except requests.ConnectionError as error:
            raise ConnectionProblemError(
                "Не удалось подключиться к Яндекс.Диску. "
                "Проверьте подключение к интернету."
            ) from error
        except requests.RequestException as error:
            raise YandexDiskError(
                f"Ошибка HTTP-запроса: {type(error).__name__}"
            ) from error

    @staticmethod
    def _raise_for_status(response, action):
        """Проверить код ответа и выбросить подходящее исключение."""
        code = response.status_code
        if code < 400:
            return
        details = _error_text(response)
        message = f"{action}: HTTP {code} — {details}"
        if code == 401:
            raise AuthorizationError(
                f"{action}: токен недействителен или устарел (HTTP 401)."
            )
        if code == 403:
            # По документации 403 у загрузки и создания папки означает
            # переполненное хранилище, у других методов — запрет доступа.
            raise ForbiddenError(
                f"{action}: доступ запрещён или хранилище переполнено "
                f"(HTTP 403) — {details}"
            )
        if code == 404:
            raise NotFoundError(message)
        if code == 409:
            raise ConflictError(message)
        if code == 429:
            raise RateLimitError(
                f"{action}: слишком много запросов (HTTP 429). "
                "Подождите немного и повторите."
            )
        if code >= 500:
            raise ServerError(
                f"{action}: сервер Яндекс.Диска временно недоступен "
                f"(HTTP {code}). Повторите позже."
            )
        raise YandexDiskError(message)

    def _resources_url(self, suffix=""):
        return f"{self.api_url}/resources{suffix}"

    # --- Операции ---------------------------------------------------------

    def check_access(self):
        """Проверить доступность API и действительность токена."""
        response = self._request("GET", self.api_url,
                                 params={"fields": "total_space,used_space"})
        action = "Проверка доступа к Яндекс.Диску"
        self._raise_for_status(response, action)
        return _json_object(response, action)

    def get_metadata(self, path):
        """Вернуть метаданные файла или папки, либо None, если их нет."""
        response = self._request(
            "GET", self._resources_url(),
            params={"path": path,
                    "fields": "name,path,type,size,mime_type"},
        )
        if response.status_code == 404:
            return None
        action = f"Получение сведений о «{path}»"
        self._raise_for_status(response, action)
        return _json_object(response, action)

    def exists(self, path):
        """Проверить, существует ли файл или папка."""
        return self.get_metadata(path) is not None

    def ensure_folder(self, path):
        """Создать папку. Вернуть True, если создана, False — если была."""
        response = self._request("PUT", self._resources_url(),
                                 params={"path": path})
        if response.status_code == 201:
            return True
        if response.status_code == 409:
            metadata = self.get_metadata(path)
            if metadata and metadata.get("type") == "dir":
                return False
            raise ConflictError(
                f"По пути «{path}» уже есть файл, а не папка."
            )
        self._raise_for_status(response, f"Создание папки «{path}»")
        return True

    def find_free_path(self, folder, stem, extension):
        """Подобрать имя, которого ещё нет: «Имя.jpg», «Имя (1).jpg»…"""
        for attempt in range(MAX_NAME_ATTEMPTS):
            suffix = f" ({attempt})" if attempt else ""
            path = f"{folder}/{stem}{suffix}{extension}"
            if not self.exists(path):
                return path
        raise ConflictError(
            f"Слишком много файлов с именем «{stem}» в папке «{folder}»."
        )

    def upload_from_url(self, source_url, path):
        """Запустить загрузку файла по URL. Вернуть ссылку на операцию.

        Яндекс.Диск сам скачивает файл, на компьютер он не сохраняется.
        Ответ 202 означает только запуск операции, а не её успех.
        """
        response = self._request(
            "POST", self._resources_url("/upload"),
            params={"url": source_url, "path": path},
        )
        action = f"Запуск загрузки «{path}»"
        self._raise_for_status(response, action)
        return _link_href(response, action)

    def _check_operation_url(self, href):
        """Не отправлять токен на чужие адреса."""
        expected = urlsplit(self.api_url)
        actual = urlsplit(href)
        if (actual.scheme, actual.netloc) != (expected.scheme,
                                              expected.netloc):
            raise YandexDiskError(
                "Ссылка на операцию указывает на неожиданный сервер."
            )

    def get_operation_status(self, href):
        """Вернуть статус операции: success, failed или in-progress."""
        self._check_operation_url(href)
        response = self._request("GET", href)
        action = "Проверка статуса операции"
        self._raise_for_status(response, action)
        status = _json_object(response, action).get("status")
        if not isinstance(status, str):
            raise UnexpectedResponseError(
                f"{action}: в ответе нет статуса операции."
            )
        return status

    def wait_for_operation(self, href, max_wait=UPLOAD_WAIT_SECONDS,
                           interval=UPLOAD_POLL_INTERVAL):
        """Ждать завершения операции ограниченное время."""
        deadline = self._clock() + max_wait
        checks = 0
        while True:
            checks += 1
            status = self.get_operation_status(href)
            logger.info("Статус операции (проверка %d): %s", checks, status)
            if status == "success":
                return
            if status == "failed":
                raise UploadFailedError(
                    "Яндекс.Диск не смог скачать картинку по ссылке."
                )
            if self._clock() + interval > deadline:
                raise UploadTimeoutError(
                    f"Загрузка не завершилась за {max_wait} с. "
                    "Проверьте папку на Диске позже."
                )
            self._sleep(interval)

    def move(self, source, destination):
        """Переименовать файл без перезаписи существующего."""
        response = self._request(
            "POST", self._resources_url("/move"),
            params={"from": source, "path": destination,
                    "overwrite": "false"},
        )
        action = f"Переименование «{source}»"
        self._raise_for_status(response, action)
        if response.status_code == 202:
            self.wait_for_operation(_link_href(response, action))
