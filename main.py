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
    if not isinstance(size, int):
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


def main(argv=None, client_factory=YandexDiskClient):
    args = parse_args(argv)
    token = load_token()
    setup_logging(token)
    logger.info("Запуск резервного копирования")

    text = " ".join(args.text) if args.text else input(
        "Введите текст для картинки: ")
    try:
        text = validate_text(text)
    except InvalidTextError as error:
        logger.error("Некорректный текст: %s", error)
        return 2

    if not token:
        logger.info("Переменная %s не задана, токен будет запрошен",
                    TOKEN_ENV_VAR)
        token = getpass.getpass("Введите OAuth-токен Яндекс.Диска "
                                "(ввод скрыт): ").strip()
        setup_logging(token)

    try:
        client = client_factory(token)
        backup_cat(text, client, report_path=args.result)
    except InvalidTextError as error:
        logger.error("Некорректный текст: %s", error)
        return 2
    except YandexDiskError as error:
        logger.error("Ошибка Яндекс.Диска: %s", error)
        return 1
    except KeyboardInterrupt:
        logger.error("Остановлено пользователем")
        return 130
    logger.info("Готово!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
