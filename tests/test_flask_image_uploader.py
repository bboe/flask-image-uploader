import io

import pytest
from gevent.event import AsyncResult
from gevent.queue import Queue
from PIL import Image

import flask_image_uploader as fiu


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    monkeypatch.setattr(fiu, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fiu, "broadcast_queue", Queue())


@pytest.fixture
def client():
    fiu.app.config.update(TESTING=True)
    return fiu.app.test_client()


def png_bytes(*, color=(255, 0, 0), size=(32, 32)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_broadcast_notifies_waiters():
    result: AsyncResult[str] = AsyncResult()
    fiu.broadcast_queue.put(result)
    fiu.broadcast("a message")
    assert result.get(timeout=1) == "a message"


def test_event_stream_formats_messages(monkeypatch):
    monkeypatch.setattr(fiu, "receive", lambda: iter(["hello", ""]))
    assert list(fiu.event_stream("203.0.113.5")) == ["data: hello\n\n", "data: \n\n"]


def test_home_lists_images(client):
    (fiu.DATA_DIR / "a.jpg").write_bytes(png_bytes())
    response = client.get("/")
    assert response.status_code == 200
    assert b"a.jpg" in response.data


def test_home_prunes_beyond_max(client, monkeypatch):
    monkeypatch.setattr(fiu, "MAX_IMAGES", 2)
    for index in range(4):
        (fiu.DATA_DIR / f"{index}.jpg").write_bytes(png_bytes())
    client.get("/")
    assert len(list(fiu.DATA_DIR.glob("*.jpg"))) == 2


def test_post_valid_image_succeeds(client):
    response = client.post("/post", data=png_bytes())
    assert response.data == b"success"
    assert list(fiu.DATA_DIR.glob("*.jpg"))


def test_safe_addr():
    assert fiu.safe_addr("203.0.113.5") == "203.0.xxx.xxx"


def test_save_normalized_image_invalid(tmp_path):
    target = tmp_path / "out.jpg"
    assert fiu.save_normalized_image(target, b"not an image") is False
    assert not target.exists()


def test_save_normalized_image_thumbnails_large(tmp_path):
    target = tmp_path / "out.jpg"
    assert fiu.save_normalized_image(target, png_bytes(size=(2000, 2000))) is True
    with Image.open(target) as image:
        assert image.width <= fiu.MAX_IMAGE_SIZE[0]
        assert image.height <= fiu.MAX_IMAGE_SIZE[1]


def test_save_normalized_image_valid(tmp_path):
    target = tmp_path / "out.jpg"
    assert fiu.save_normalized_image(target, png_bytes()) is True
    assert target.is_file()
    with Image.open(target) as image:
        assert image.mode == "RGB"
