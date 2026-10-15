"""Tests for Storage subsystem and path security."""
import pytest
from app.core.exceptions import StorageError
from app.storage.local import LocalStorage

def test_local_storage_write_and_read(tmp_path):
    storage = LocalStorage(base_dir=str(tmp_path), base_url="http://testserver")
    rel_path = "subfolder/test_file.txt"
    test_data = b"Hello IncuBrix Storage"

    url = storage.save_bytes(rel_path, test_data)
    assert url == "http://testserver/api/v1/jobs/download/subfolder/test_file.txt"
    assert storage.exists(rel_path)
    assert storage.get_file_bytes(rel_path) == test_data

def test_local_storage_path_traversal_prevention(tmp_path):
    storage = LocalStorage(base_dir=str(tmp_path), base_url="http://testserver")
    malicious_path = "../../../etc/passwd"
    with pytest.raises(StorageError):
        storage.save_bytes(malicious_path, b"dangerous")
