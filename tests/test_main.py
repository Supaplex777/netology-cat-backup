import json
import logging
import re
import subprocess
from pathlib import Path

import pytest

import main
from conftest import FAKE_TOKEN, ROOT
from yandex_disk import (
    AuthorizationError,
    UploadFailedError,
    UploadTimeoutError,
    YandexDiskError,
)


class FakeDisk:
    """Диск в памяти, повторяющий нужное поведение YandexDiskClient."""

    def __init__(self, token=FAKE_TOKEN, mime="image/jpeg", size=152430,
                 files=(), fail_on=None, folder_exists=False):
        self.token = token
        self.mime = mime
        self.size = size
        self.files = {p: {"size": 1, "mime_type": "image/jpeg"}
                      for p in files}
        self.fail_on = fail_on
        self.folder_exists = folder_exists
        self.uploads = []

    def _maybe_fail(self, step):
        if self.fail_on and self.fail_on[0] == step:
            raise self.fail_on[1]

    def check_access(self):
        self._maybe_fail("check_access")
        return {}

    def ensure_folder(self, path):
        return not self.folder_exists

    def exists(self, path):
        return path in self.files

    def find_free_path(self, folder, stem, extension):
        attempt = 0
        while True:
            suffix = f" ({attempt})" if attempt else ""
            path = f"{folder}/{stem}{suffix}{extension}"
            if path not in self.files:
                return path
            attempt += 1

    def upload_from_url(self, url, path):
        self._maybe_fail("upload")
        self.uploads.append((url, path))
        self._pending = path
        return "op"

    def wait_for_operation(self, href):
        self._maybe_fail("wait")
        self.files[self._pending] = {"size": self.size,
                                     "mime_type": self.mime}

    def get_metadata(self, path):
        if path not in self.files:
            return None
        info = self.files[path]
        return {"name": path.rsplit("/", 1)[1], "type": "file",
                "path": f"disk:/{path}", **info}

    def move(self, source, destination):
        self.files[destination] = self.files.pop(source)


def read_report(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_successful_backup_writes_json(tmp_path):
    report = tmp_path / "result.json"
    disk = FakeDisk()
    record = main.backup_cat("Hello", disk, report_path=report)
    assert record == {"file_name": "Hello.jpg", "size": 152430}
    assert read_report(report) == [record]
    url, path = disk.uploads[0]
    assert url == "https://cataas.com/cat/says/Hello"
    assert path == "AIE-9/Hello.jpg"


def test_json_keeps_cyrillic_utf8(tmp_path):
    report = tmp_path / "result.json"
    main.backup_cat("Привет мир", FakeDisk(), report_path=report)
    raw = report.read_text(encoding="utf-8")
    assert "Привет мир.jpg" in raw
    assert "\\u" not in raw


def test_report_appends_records(tmp_path):
    report = tmp_path / "result.json"
    disk = FakeDisk()
    main.backup_cat("one", disk, report_path=report)
    main.backup_cat("two", disk, report_path=report)
    names = [r["file_name"] for r in read_report(report)]
    assert names == ["one.jpg", "two.jpg"]


def test_broken_report_is_kept_as_backup(tmp_path):
    report = tmp_path / "result.json"
    report.write_text("{broken", encoding="utf-8")
    main.backup_cat("cat", FakeDisk(), report_path=report)
    assert (tmp_path / "result.json.bak").read_text() == "{broken"
    assert len(read_report(report)) == 1


def test_duplicate_name_does_not_overwrite(tmp_path):
    report = tmp_path / "result.json"
    disk = FakeDisk(files=["AIE-9/Hello.jpg"])
    record = main.backup_cat("Hello", disk, report_path=report)
    assert record["file_name"] == "Hello (1).jpg"
    assert disk.files["AIE-9/Hello.jpg"]["size"] == 1  # старый файл цел


def test_png_gets_correct_extension(tmp_path):
    report = tmp_path / "result.json"
    disk = FakeDisk(mime="image/png", size=999)
    record = main.backup_cat("Hi", disk, report_path=report)
    assert record == {"file_name": "Hi.png", "size": 999}
    assert "AIE-9/Hi.jpg" not in disk.files


def test_size_is_taken_from_disk_metadata(tmp_path):
    record = main.backup_cat("x", FakeDisk(size=4242),
                             report_path=tmp_path / "r.json")
    assert record["size"] == 4242


@pytest.mark.parametrize("step, error", [
    ("check_access", AuthorizationError("bad token")),
    ("upload", YandexDiskError("HTTP 500")),
    ("wait", UploadTimeoutError("timeout")),
    ("wait", UploadFailedError("failed")),
])
def test_errors_do_not_create_false_records(tmp_path, step, error):
    report = tmp_path / "result.json"
    with pytest.raises(YandexDiskError):
        main.backup_cat("Hello", FakeDisk(fail_on=(step, error)),
                        report_path=report)
    assert not report.exists()


def test_missing_file_after_upload_is_error(tmp_path):
    disk = FakeDisk()
    disk.wait_for_operation = lambda href: None  # файл так и не появился
    with pytest.raises(YandexDiskError, match="не найден"):
        main.backup_cat("Hello", disk, report_path=tmp_path / "r.json")


def test_main_success_and_no_token_in_output(tmp_path, monkeypatch,
                                             capsys):
    monkeypatch.setenv("YANDEX_DISK_TOKEN", FAKE_TOKEN)
    monkeypatch.chdir(tmp_path)
    created = []

    def factory(token):
        created.append(token)
        return FakeDisk(token)

    report = tmp_path / "result.json"
    code = main.main(["Привет", "котик", "--result", str(report)],
                     client_factory=factory)
    assert code == 0
    assert created == [FAKE_TOKEN]
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.out + captured.err
    assert "Запуск резервного копирования" in captured.err
    assert "Сведения сохранены" in captured.err
    assert FAKE_TOKEN not in report.read_text(encoding="utf-8")


def test_main_asks_token_with_hidden_input(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("YANDEX_DISK_TOKEN", raising=False)
    monkeypatch.setattr(main, "load_token", lambda: None)
    monkeypatch.setattr(main.getpass, "getpass", lambda prompt: FAKE_TOKEN)
    monkeypatch.setattr("builtins.input", lambda prompt: "Hello")
    code = main.main(["--result", str(tmp_path / "r.json")],
                     client_factory=FakeDisk)
    assert code == 0
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.out + captured.err


def test_main_empty_text(monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda prompt: "   ")
    monkeypatch.setattr(main, "load_token", lambda: FAKE_TOKEN)
    assert main.main(["--result", str(tmp_path / "r.json")],
                     client_factory=FakeDisk) == 2
    assert not (tmp_path / "r.json").exists()


def test_main_invalid_token(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main, "load_token", lambda: FAKE_TOKEN)
    disk = FakeDisk(fail_on=("check_access",
                             AuthorizationError("токен недействителен")))
    code = main.main(["Hi", "--result", str(tmp_path / "r.json")],
                     client_factory=lambda token: disk)
    assert code == 1
    assert "токен недействителен" in capsys.readouterr().err


def test_secret_filter_masks_token(caplog):
    record = logging.LogRecord("t", logging.INFO, __file__, 1,
                               "header OAuth %s", (FAKE_TOKEN,), None)
    main.SecretFilter(FAKE_TOKEN).filter(record)
    assert record.getMessage() == "header OAuth ***"


# Похожие на настоящие токены Яндекса строки (y0_…, AQAAAA…) и
# заголовок Authorization с длинным значением.
TOKEN_PATTERNS = [
    re.compile(r"y0_[A-Za-z0-9_-]{20,}"),
    re.compile(r"AQAAAA[A-Za-z0-9_-]{20,}"),
    re.compile(r"OAuth [A-Za-z0-9_-]{30,}"),
]


def project_files():
    try:
        output = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        return [ROOT / name for name in output.splitlines()]
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
        return [p for p in ROOT.rglob("*")
                if p.is_file() and not skip & set(p.parts)
                and p.name != ".env"]


def test_no_tokens_in_project_files():
    for path in project_files():
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in TOKEN_PATTERNS:
            assert not pattern.search(text), f"Похоже на токен в {path.name}"


def test_env_file_is_ignored():
    rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in rules
    assert "!.env.example" in rules


# --- Регрессия: ошибки ввода и прерывание ---------------------------------

def raise_eof(prompt=""):
    raise EOFError


def raise_interrupt(prompt=""):
    raise KeyboardInterrupt


def test_eof_on_text_input(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main, "load_token", lambda: FAKE_TOKEN)
    monkeypatch.setattr("builtins.input", raise_eof)
    code = main.main(["--result", str(tmp_path / "r.json")],
                     client_factory=FakeDisk)
    assert code == main.EXIT_NO_INPUT == 3
    err = capsys.readouterr().err
    assert "Не удалось прочитать ввод" in err
    assert "Traceback" not in err
    assert not (tmp_path / "r.json").exists()


def test_eof_on_token_input(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main, "load_token", lambda: None)
    monkeypatch.setattr(main.getpass, "getpass", raise_eof)
    created = []
    code = main.main(["Hello", "--result", str(tmp_path / "r.json")],
                     client_factory=lambda token: created.append(token))
    assert code == main.EXIT_NO_INPUT
    assert created == []  # клиент даже не создавался
    assert "Traceback" not in capsys.readouterr().err


def test_ctrl_c_on_text_input(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main, "load_token", lambda: FAKE_TOKEN)
    monkeypatch.setattr("builtins.input", raise_interrupt)
    code = main.main(["--result", str(tmp_path / "r.json")],
                     client_factory=FakeDisk)
    assert code == main.EXIT_INTERRUPTED == 130
    assert "Остановлено пользователем" in capsys.readouterr().err


def test_ctrl_c_on_token_input(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "load_token", lambda: None)
    monkeypatch.setattr(main.getpass, "getpass", raise_interrupt)
    assert main.main(["Hi", "--result", str(tmp_path / "r.json")],
                     client_factory=FakeDisk) == main.EXIT_INTERRUPTED


def test_ctrl_c_while_waiting_for_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "load_token", lambda: FAKE_TOKEN)
    disk = FakeDisk(fail_on=("wait", KeyboardInterrupt()))
    report = tmp_path / "r.json"
    assert main.main(["Hi", "--result", str(report)],
                     client_factory=lambda token: disk) == 130
    assert not report.exists()


def test_empty_token_from_prompt(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main, "load_token", lambda: None)
    monkeypatch.setattr(main.getpass, "getpass", lambda prompt: "   ")
    # Настоящий клиент отвергает пустой токен до любых запросов.
    code = main.main(["Hi", "--result", str(tmp_path / "r.json")])
    assert code == main.EXIT_DISK_ERROR
    assert "Не указан OAuth-токен" in capsys.readouterr().err


def test_report_write_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(main, "load_token", lambda: FAKE_TOKEN)
    report = tmp_path / "no-such-dir" / "r.json"
    code = main.main(["Hi", "--result", str(report)],
                     client_factory=FakeDisk)
    assert code == main.EXIT_REPORT_ERROR
    err = capsys.readouterr().err
    assert "Не удалось записать отчёт" in err
    assert "Traceback" not in err


@pytest.mark.parametrize("size", [None, "123", True, -1, 1.5])
def test_invalid_size_from_disk(tmp_path, size):
    report = tmp_path / "r.json"
    with pytest.raises(YandexDiskError, match="размер"):
        main.backup_cat("Hi", FakeDisk(size=size), report_path=report)
    assert not report.exists()


def test_real_process_without_stdin(tmp_path):
    """Запуск как у пользователя: stdin закрыт, токена нет — без traceback."""
    import os
    import sys

    env = {k: v for k, v in os.environ.items() if k != "YANDEX_DISK_TOKEN"}
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py")],
        cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 3
    assert "Traceback" not in result.stderr
    assert "Не удалось прочитать ввод" in result.stderr


def test_env_variable_has_priority_over_dotenv(tmp_path, monkeypatch):
    """На этом основана проверка ошибок без настоящего токена в docs/."""
    import config

    dotenv = tmp_path / ".env"
    dotenv.write_text("YANDEX_DISK_TOKEN=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "from-environment")
    assert config.load_token(dotenv) == "from-environment"


def test_token_is_read_from_dotenv(tmp_path, monkeypatch):
    import config

    dotenv = tmp_path / ".env"
    dotenv.write_text("YANDEX_DISK_TOKEN=from-dotenv\n", encoding="utf-8")
    # setenv + delenv: monkeypatch запомнит исходное состояние и вернёт его,
    # даже если load_dotenv запишет значение в os.environ.
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "placeholder")
    monkeypatch.delenv("YANDEX_DISK_TOKEN")
    assert config.load_token(dotenv) == "from-dotenv"


def test_missing_dotenv_gives_none(tmp_path, monkeypatch):
    import config

    # setenv + delenv: monkeypatch запомнит исходное состояние и вернёт его,
    # даже если load_dotenv запишет значение в os.environ.
    monkeypatch.setenv("YANDEX_DISK_TOKEN", "placeholder")
    monkeypatch.delenv("YANDEX_DISK_TOKEN")
    assert config.load_token(tmp_path / "absent.env") is None


def test_dotenv_is_looked_up_in_project_folder():
    import config

    assert config.DOTENV_PATH == ROOT / ".env"


def test_dotenv_tests_do_not_leak_token():
    import os

    assert os.environ.get("YANDEX_DISK_TOKEN") != "from-dotenv"
