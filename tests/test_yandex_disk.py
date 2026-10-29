import pytest
import requests

from conftest import API, FAKE_TOKEN, FakeResponse
from yandex_disk import (
    AuthorizationError,
    ConflictError,
    ConnectionProblemError,
    RateLimitError,
    UploadFailedError,
    UploadTimeoutError,
    YandexDiskClient,
    YandexDiskError,
)

OPERATION = f"{API}/operations/abc123"


def test_authorization_header_format(make_client):
    client, session = make_client()
    assert session.headers["Authorization"] == f"OAuth {FAKE_TOKEN}"


def test_token_not_in_repr(make_client):
    client, _ = make_client()
    assert FAKE_TOKEN not in repr(client)


def test_missing_token_is_rejected():
    with pytest.raises(AuthorizationError):
        YandexDiskClient("")


def test_check_access_ok(make_client):
    client, session = make_client(FakeResponse(200, {"total_space": 10}))
    assert client.check_access() == {"total_space": 10}
    assert session.calls[0]["url"] == API
    assert session.calls[0]["timeout"] is not None


def test_invalid_token(make_client):
    client, _ = make_client(FakeResponse(401, {"error": "UnauthorizedError",
                                               "description": "Unauthorized"}))
    with pytest.raises(AuthorizationError) as info:
        client.check_access()
    assert FAKE_TOKEN not in str(info.value)


def test_forbidden(make_client):
    client, _ = make_client(FakeResponse(403, {"description": "no rights"}))
    with pytest.raises(AuthorizationError):
        client.check_access()


def test_rate_limit(make_client):
    client, _ = make_client(FakeResponse(429, {}))
    with pytest.raises(RateLimitError):
        client.check_access()


def test_http_server_error(make_client):
    client, _ = make_client(FakeResponse(503, None, reason="Unavailable"))
    with pytest.raises(YandexDiskError, match="503"):
        client.check_access()


def test_request_timeout(make_client):
    client, _ = make_client(requests.Timeout("slow"))
    with pytest.raises(ConnectionProblemError, match="тайм-аут"):
        client.check_access()


def test_network_error(make_client):
    client, _ = make_client(requests.ConnectionError("down"))
    with pytest.raises(ConnectionProblemError):
        client.check_access()


def test_create_folder(make_client):
    client, session = make_client(FakeResponse(201, {"href": "x"}))
    assert client.ensure_folder("AIE-9") is True
    call = session.calls[0]
    assert call["method"] == "PUT"
    assert call["params"] == {"path": "AIE-9"}


def test_existing_folder(make_client):
    client, _ = make_client(
        FakeResponse(409, {"error": "DiskPathPointsToExistentDirectoryError"}),
        FakeResponse(200, {"type": "dir", "name": "AIE-9"}),
    )
    assert client.ensure_folder("AIE-9") is False


def test_folder_path_is_a_file(make_client):
    client, _ = make_client(
        FakeResponse(409, {}),
        FakeResponse(200, {"type": "file", "name": "AIE-9"}),
    )
    with pytest.raises(ConflictError):
        client.ensure_folder("AIE-9")


def test_get_metadata_returns_size(make_client):
    meta = {"name": "Hi.jpg", "type": "file", "size": 1234,
            "mime_type": "image/jpeg"}
    client, session = make_client(FakeResponse(200, meta))
    assert client.get_metadata("AIE-9/Hi.jpg")["size"] == 1234
    assert "size" in session.calls[0]["params"]["fields"]


def test_get_metadata_missing(make_client):
    client, _ = make_client(FakeResponse(404, {}))
    assert client.get_metadata("AIE-9/none.jpg") is None


def test_find_free_path_without_conflict(make_client):
    client, _ = make_client(FakeResponse(404, {}))
    assert client.find_free_path("AIE-9", "Hi", ".jpg") == "AIE-9/Hi.jpg"


def test_find_free_path_with_duplicates(make_client):
    client, _ = make_client(
        FakeResponse(200, {"type": "file"}),
        FakeResponse(200, {"type": "file"}),
        FakeResponse(404, {}),
    )
    assert client.find_free_path("AIE-9", "Hi", ".jpg") == "AIE-9/Hi (2).jpg"


def test_upload_from_url(make_client):
    client, session = make_client(
        FakeResponse(202, {"href": OPERATION, "method": "GET"}))
    href = client.upload_from_url("https://cataas.com/cat/says/hi",
                                  "AIE-9/hi.jpg")
    assert href == OPERATION
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{API}/resources/upload"
    assert call["params"] == {"url": "https://cataas.com/cat/says/hi",
                              "path": "AIE-9/hi.jpg"}


def test_upload_conflict(make_client):
    client, _ = make_client(FakeResponse(409, {"description": "exists"}))
    with pytest.raises(ConflictError):
        client.upload_from_url("https://x", "AIE-9/hi.jpg")


def test_upload_without_href(make_client):
    client, _ = make_client(FakeResponse(202, {}))
    with pytest.raises(YandexDiskError):
        client.upload_from_url("https://x", "AIE-9/hi.jpg")


def test_wait_async_operation_success(make_client, clock):
    client, session = make_client(
        FakeResponse(200, {"status": "in-progress"}),
        FakeResponse(200, {"status": "in-progress"}),
        FakeResponse(200, {"status": "success"}),
    )
    client.wait_for_operation(OPERATION, max_wait=10, interval=2)
    assert len(session.calls) == 3
    assert clock.sleeps == [2, 2]


def test_wait_operation_failed(make_client):
    client, _ = make_client(FakeResponse(200, {"status": "failed"}))
    with pytest.raises(UploadFailedError):
        client.wait_for_operation(OPERATION)


def test_wait_operation_timeout_is_limited(make_client, clock):
    responses = [FakeResponse(200, {"status": "in-progress"})
                 for _ in range(20)]
    client, session = make_client(*responses)
    with pytest.raises(UploadTimeoutError):
        client.wait_for_operation(OPERATION, max_wait=6, interval=2)
    # Ограниченное число проверок, без бесконечного цикла.
    assert len(session.calls) <= 4


def test_operation_on_foreign_host_is_refused(make_client):
    client, session = make_client()
    with pytest.raises(YandexDiskError):
        client.wait_for_operation("https://evil.example/operations/1")
    assert session.calls == []


def test_move_without_overwrite(make_client):
    client, session = make_client(FakeResponse(201, {"href": "x"}))
    client.move("AIE-9/a.jpg", "AIE-9/a.png")
    params = session.calls[0]["params"]
    assert params["overwrite"] == "false"
    assert params["from"] == "AIE-9/a.jpg"


def test_move_async(make_client):
    client, session = make_client(
        FakeResponse(202, {"href": OPERATION}),
        FakeResponse(200, {"status": "success"}),
    )
    client.move("AIE-9/a.jpg", "AIE-9/a.png")
    assert len(session.calls) == 2
