import uuid

import pytest

from app.core.exceptions import StorageError
from app.services.storage import LocalStorage


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path / "uploads")


def test_save_and_read_back(storage):
    job_id = uuid.uuid4()

    key = storage.save(job_id, ".geojson", b"{}")

    assert key == f"{job_id}/upload.geojson"
    assert storage.get_path(key).read_bytes() == b"{}"


def test_storage_key_never_contains_user_filename(storage):
    key = storage.save(uuid.uuid4(), ".zip", b"data")
    assert "parcels" not in key
    assert key.endswith("/upload.zip")


@pytest.mark.parametrize("extension", ["../../evil", ".geo/json", "zip", ".GEOJSON", ""])
def test_save_rejects_unsafe_extensions(storage, extension):
    with pytest.raises(StorageError):
        storage.save(uuid.uuid4(), extension, b"data")


def test_save_rejects_non_uuid_job_id(storage):
    with pytest.raises(StorageError):
        storage.save("../escape", ".zip", b"data")


@pytest.mark.parametrize("key", ["../outside.txt", "../../etc/passwd", "a/../../outside"])
def test_get_path_rejects_path_traversal(storage, key):
    with pytest.raises(StorageError):
        storage.get_path(key)


def test_delete_removes_file_and_empty_folder(storage):
    key = storage.save(uuid.uuid4(), ".zip", b"data")
    path = storage.get_path(key)

    storage.delete(key)

    assert not path.exists()
    assert not path.parent.exists()


def test_delete_missing_file_is_a_no_op(storage):
    storage.delete(f"{uuid.uuid4()}/upload.zip")
