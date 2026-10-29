"""Резервное копирование картинки кошки с надписью на Яндекс.Диск.

Запуск:
    python main.py              # текст будет запрошен с клавиатуры
    python main.py "Привет"     # текст передан аргументом
"""

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path, PurePosixPath

from cataas_api import (
    DEFAULT_EXTENSION,
    InvalidTextError,
    build_cat_url,
    extension_for,
    make_file_stem,
    validate_text,
)
from config import GROUP_FOLDER, RESULT_FILE, TOKEN_ENV_VAR, load_token
from yandex_disk import YandexDiskClient, YandexDiskError

logger = logging.getLogger("cat_backup")


class SecretFilter(logging.Filter):
    """Заменяет токен на «***», если он случайно попал в сообщение."""

    def __init__(self, secret):
        super().__init__()
        self.secret = secret

    def filter(self, record):
        if self.secret:
            message = record.getMessage()
            if self.secret in message:
                record.msg = message.replace(self.secret, "***")
                record.args = None
        return True


def configure_console():
    """Не падать, если консоль не умеет показывать какой-то символ.

    В консоли со старой кодировкой (например, cp1251 или ascii) символы,
    которые нельзя отобразить, выводятся как \\uXXXX, а не вызывают
    UnicodeEncodeError. Глобальные настройки системы не меняются.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (OSError, ValueError):
                pass


def setup_logging(token=None, level=logging.INFO):
    """Настроить вывод хода работы в консоль."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    ))
    handler.addFilter(SecretFilter(token))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Отладочные сообщения библиотеки HTTP не нужны студенту.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def save_report(record, report_path=RESULT_FILE):
    """Добавить запись о файле в JSON-отчёт (UTF-8, кириллица как есть)."""
    path = Path(report_path)
    records = []
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("ожидался список")
        except ValueError:
            backup = path.with_name(path.name + ".bak")
            path.replace(backup)
            logger.warning("Старый отчёт повреждён, сохранён как %s",
                           backup.name)
            records = []
    records.append(record)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def backup_cat(text, client, folder=GROUP_FOLDER, report_path=RESULT_FILE):
    """Загрузить картинку с надписью на Диск и записать её в отчёт."""
    logger.info("Готовлю ссылку на картинку с надписью «%s»", text)
    cat_url = build_cat_url(text)
    stem = make_file_stem(text)
    logger.info("Ссылка CATAAS: %s", cat_url)

    logger.info("Проверяю доступ к Яндекс.Диску")
    client.check_access()

    if client.ensure_folder(folder):
        logger.info("Папка «%s» создана", folder)
    else:
        logger.info("Папка «%s» уже существует", folder)

    disk_path = client.find_free_path(folder, stem, DEFAULT_EXTENSION)
    if disk_path != f"{folder}/{stem}{DEFAULT_EXTENSION}":
        logger.warning("Файл «%s%s» уже есть, новый файл будет назван «%s»",
                       stem, DEFAULT_EXTENSION, PurePosixPath(disk_path).name)

    logger.info("Запускаю загрузку на Диск по URL: %s", disk_path)
    operation = client.upload_from_url(cat_url, disk_path)

    logger.info("Жду, пока Яндекс.Диск скачает картинку")
    client.wait_for_operation(operation)

    logger.info("Проверяю, что файл появился на Диске")
    metadata = client.get_metadata(disk_path)
    if not metadata or metadata.get("type") != "file":
        raise YandexDiskError(
            f"После загрузки файл «{disk_path}» не найден на Диске."
        )

    correct_ext = extension_for(metadata.get("mime_type"))
    if correct_ext != DEFAULT_EXTENSION:
        current_stem = PurePosixPath(disk_path).stem
        new_path = client.find_free_path(folder, current_stem, correct_ext)
        logger.info("Формат картинки %s, переименовываю в «%s»",
                    metadata.get("mime_type"), PurePosixPath(new_path).name)
        client.move(disk_path, new_path)
        disk_path = new_path
        metadata = client.get_metadata(disk_path)
        if not metadata:
            raise YandexDiskError(
                f"После переименования файл «{disk_path}» не найден."
            )

    size = metadata.get("size")
    # bool — подкласс int в Python, поэтому исключаем его явно.
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise YandexDiskError("Яндекс.Диск не сообщил размер файла.")

    record = {
        "file_name": metadata.get("name") or PurePosixPath(disk_path).name,
        "size": size,
    }
    logger.info("Файл загружен: %s, размер %d байт", record["file_name"],
                size)
    saved = save_report(record, report_path)
    logger.info("Сведения сохранены в %s", saved)
    return record


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Резервное копирование картинки кошки на Яндекс.Диск."
    )
    parser.add_argument("text", nargs="*", help="текст надписи на картинке")
    parser.add_argument("--result", default=RESULT_FILE,
                        help="путь к JSON-отчёту (по умолчанию result.json)")
    return parser.parse_args(argv)


# Коды завершения программы.
EXIT_OK = 0
EXIT_DISK_ERROR = 1
EXIT_BAD_TEXT = 2
EXIT_NO_INPUT = 3
EXIT_REPORT_ERROR = 4
EXIT_INTERRUPTED = 130


class InputUnavailableError(Exception):
    """Нет интерактивного ввода (например, stdin закрыт или пуст)."""


def ask(prompt, hidden=False):
    """Запросить строку у пользователя; EOF превратить в понятную ошибку."""
    try:
        if hidden:
            return getpass.getpass(prompt)
        return input(prompt)
    except EOFError as error:
        raise InputUnavailableError from error


def main(argv=None, client_factory=YandexDiskClient):
    args = parse_args(argv)
    configure_console()
    token = load_token()
    setup_logging(token)
    logger.info("Запуск резервного копирования")

    try:
        if args.text:
            text = " ".join(args.text)
        else:
            text = ask("Введите текст для картинки: ")
        text = validate_text(text)

        if not token:
            logger.info("Переменная %s не задана, токен будет запрошен",
                        TOKEN_ENV_VAR)
            token = ask("Введите OAuth-токен Яндекс.Диска (ввод скрыт): ",
                        hidden=True).strip()
            setup_logging(token)

        client = client_factory(token)
        backup_cat(text, client, report_path=args.result)
    except InputUnavailableError:
        logger.error(
            "Не удалось прочитать ввод с клавиатуры. Передайте текст "
            "аргументом (python main.py \"Текст\") и задайте токен в "
            "переменной %s или в файле .env.", TOKEN_ENV_VAR)
        return EXIT_NO_INPUT
    except InvalidTextError as error:
        logger.error("Некорректный текст: %s", error)
        return EXIT_BAD_TEXT
    except YandexDiskError as error:
        logger.error("Ошибка Яндекс.Диска: %s", error)
        return EXIT_DISK_ERROR
    except OSError as error:
        logger.error("Не удалось записать отчёт %s: %s. Файл на Диске "
                     "при этом мог быть загружен.", args.result,
                     error.strerror or type(error).__name__)
        return EXIT_REPORT_ERROR
    except KeyboardInterrupt:
        logger.error("Остановлено пользователем (Ctrl+C)")
        return EXIT_INTERRUPTED
    logger.info("Готово!")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
