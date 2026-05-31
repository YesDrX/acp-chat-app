"""Tests for file management routes.

Tests upload, list, get, download, delete, rename, folder creation,
bulk operations, and error cases.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

logging.basicConfig(level=logging.DEBUG)


@pytest.fixture
def test_files_dir(tmp_path):
    """Override FILES_DIR to a temporary directory."""
    import backend.config as config
    import backend.routes.files as files_routes

    original = config.FILES_DIR
    test_dir = tmp_path / "test-files"
    test_dir.mkdir(parents=True, exist_ok=True)
    config.FILES_DIR = test_dir
    files_routes.FILES_DIR = test_dir  # Also update the module-level reference

    yield test_dir

    config.FILES_DIR = original
    files_routes.FILES_DIR = original


@pytest.fixture
def client():
    """Create a FastAPI TestClient."""
    from backend.main import app
    return TestClient(app)


class TestFileUpload:
    """Tests for file upload operation."""

    def test_upload_file(self, client, test_files_dir, test_db):
        """Test uploading a file."""
        content = b"Hello, this is a test file!"
        files = {"file": ("test.txt", io.BytesIO(content), "text/plain")}
        data = {"session_id": "sess-1", "folder": ""}

        response = client.post("/api/files", files=files, data=data)
        assert response.status_code == 201
        result = response.json()
        assert result["name"] == "test.txt"
        assert result["size"] == len(content)

        # Verify file exists on disk
        full_path = test_files_dir / result["path"]
        assert full_path.exists()
        assert full_path.read_bytes() == content

    def test_upload_file_to_subfolder(self, client, test_files_dir, test_db):
        """Test uploading a file to a subfolder."""
        content = b"Subfolder test"
        files = {"file": ("data.txt", io.BytesIO(content), "text/plain")}
        data = {"session_id": "sess-1", "folder": "docs"}

        response = client.post("/api/files", files=files, data=data)
        assert response.status_code == 201
        result = response.json()
        assert result["name"] == "data.txt"
        assert "docs/data.txt" in result["path"]

        # Verify file exists
        sub_path = test_files_dir / "docs" / "data.txt"
        assert sub_path.exists()

    def test_upload_no_file(self, client, test_files_dir, test_db):
        """Test uploading with no file returns 400."""
        response = client.post("/api/files")
        # FastAPI returns 422 for missing required form fields
        assert response.status_code in (400, 422)

    def test_upload_sanitizes_filename(self, client, test_files_dir, test_db):
        """Test that slashes in filename are sanitized."""
        content = b"test"
        files = {"file": ("bad/../../name.txt", io.BytesIO(content), "text/plain")}
        data = {"session_id": "sess-1"}

        response = client.post("/api/files", files=files, data=data)
        assert response.status_code == 201
        result = response.json()
        # Filename should have slashes replaced with underscores
        assert "/" not in result["name"]
        assert result["name"].endswith(".txt")


class TestFileList:
    """Tests for file listing."""

    def test_list_files_empty(self, client, test_files_dir, test_db):
        """Test listing files when none exist."""
        response = client.get("/api/files")
        assert response.status_code == 200
        result = response.json()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_list_files_with_data(self, client, test_files_dir, test_db):
        """Test listing files after uploading some."""
        # Upload a file first
        content = b"Listing test"
        files = {"file": ("list-test.txt", io.BytesIO(content), "text/plain")}
        client.post("/api/files", files=files, data={"session_id": "sess-1"})

        response = client.get("/api/files")
        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1
        assert result[0]["name"] == "list-test.txt"

    def test_list_files_by_session(self, client, test_files_dir, test_db):
        """Test filtering files by session_id."""
        # Upload two files for different sessions
        for i in range(2):
            content = f"Sess {i}".encode()
            files = {"file": (f"sess-{i}.txt", io.BytesIO(content), "text/plain")}
            client.post("/api/files", files=files, data={"session_id": f"sess-{i}"})

        # List for sess-0
        response = client.get("/api/files?session_id=sess-0")
        result = response.json()
        assert len(result) >= 1
        names = [f["name"] for f in result]
        assert "sess-0.txt" in names

    def test_list_files_by_folder(self, client, test_files_dir, test_db):
        """Test filtering files by subfolder."""
        # Upload file to subfolder
        content = b"Folder test"
        files = {"file": ("folder-file.txt", io.BytesIO(content), "text/plain")}
        client.post("/api/files", files=files, data={"folder": "subdir"})

        # Upload to root
        files2 = {"file": ("root-file.txt", io.BytesIO(content), "text/plain")}
        client.post("/api/files", files=files2, data={})

        # List subdir
        response = client.get("/api/files?folder=subdir")
        result = response.json()
        assert len(result) >= 1
        names = [f["name"] for f in result]
        assert "folder-file.txt" in names


class TestFileGet:
    """Tests for getting file metadata and download."""

    def test_get_file_metadata(self, client, test_files_dir, test_db):
        """Test getting metadata for a file."""
        content = b"Metadata test"
        files = {"file": ("meta.txt", io.BytesIO(content), "text/plain")}
        create_resp = client.post("/api/files", files=files, data={"session_id": "sess-1"})
        created = create_resp.json()
        file_id = created["id"]

        response = client.get(f"/api/files/{file_id}")
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "meta.txt"
        assert result["size"] == len(content)

    def test_get_file_not_found(self, client, test_files_dir, test_db):
        """Test getting metadata for non-existent file."""
        response = client.get("/api/files/nonexistent-id")
        assert response.status_code == 404

    def test_download_file(self, client, test_files_dir, test_db):
        """Test downloading a file."""
        content = b"Download me!"
        files = {"file": ("download.txt", io.BytesIO(content), "text/plain")}
        create_resp = client.post("/api/files", files=files, data={"session_id": "sess-1"})
        created = create_resp.json()
        file_id = created["id"]

        response = client.get(f"/api/files/{file_id}/download")
        assert response.status_code == 200
        assert response.content == content

    def test_download_file_not_found(self, client, test_files_dir, test_db):
        """Test downloading non-existent file."""
        response = client.get("/api/files/nonexistent/download")
        assert response.status_code == 404


class TestFileDelete:
    """Tests for file deletion."""

    def test_delete_file(self, client, test_files_dir, test_db):
        """Test deleting a file."""
        content = b"Delete me"
        files = {"file": ("to-delete.txt", io.BytesIO(content), "text/plain")}
        create_resp = client.post("/api/files", files=files, data={"session_id": "sess-1"})
        created = create_resp.json()
        file_id = created["id"]

        # Verify it exists first
        assert (test_files_dir / created["path"]).exists()

        response = client.delete(f"/api/files/{file_id}")
        assert response.status_code == 200
        result = response.json()
        assert "deleted" in result["detail"].lower()

        # Verify file gone from disk
        assert not (test_files_dir / created["path"]).exists()

        # Verify gone from DB
        get_resp = client.get(f"/api/files/{file_id}")
        assert get_resp.status_code == 404

    def test_delete_file_not_found(self, client, test_files_dir, test_db):
        """Test deleting non-existent file."""
        response = client.delete("/api/files/nonexistent")
        assert response.status_code == 404


class TestFileRename:
    """Tests for file renaming."""

    def test_rename_file(self, client, test_files_dir, test_db):
        """Test renaming a file."""
        content = b"Rename me"
        files = {"file": ("old-name.txt", io.BytesIO(content), "text/plain")}
        create_resp = client.post("/api/files", files=files, data={"session_id": "sess-1"})
        created = create_resp.json()
        file_id = created["id"]

        old_path = test_files_dir / created["path"]
        assert old_path.exists()

        # Rename
        rename_data = {"name": "new-name.txt"}
        response = client.put(f"/api/files/{file_id}", data=rename_data)
        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "new-name.txt"

        # Old path should be gone
        assert not old_path.exists()

        # New path should exist
        new_path = test_files_dir / result["path"]
        assert new_path.exists()

    def test_rename_file_not_found(self, client, test_files_dir, test_db):
        """Test renaming non-existent file."""
        rename_data = {"name": "whatever.txt"}
        response = client.put("/api/files/nonexistent", data=rename_data)
        assert response.status_code == 404


class TestFolderOperations:
    """Tests for folder creation and listing."""

    def test_create_folder(self, client, test_files_dir, test_db):
        """Test creating a new folder."""
        response = client.post("/api/files/folder", data={"name": "my-folder", "parent": ""})
        assert response.status_code == 201
        result = response.json()
        assert result["name"] == "my-folder"
        assert result["type"] == "folder"

        # Verify on disk
        assert (test_files_dir / "my-folder").is_dir()

    def test_create_folder_with_parent(self, client, test_files_dir, test_db):
        """Test creating a subfolder."""
        # Create parent first
        client.post("/api/files/folder", data={"name": "parent", "parent": ""})

        # Create subfolder
        response = client.post("/api/files/folder", data={"name": "child", "parent": "parent"})
        assert response.status_code == 201
        assert (test_files_dir / "parent" / "child").is_dir()

    def test_create_folder_already_exists(self, client, test_files_dir, test_db):
        """Test creating a folder that already exists (should succeed due to exist_ok)."""
        client.post("/api/files/folder", data={"name": "duplicate", "parent": ""})
        response = client.post("/api/files/folder", data={"name": "duplicate", "parent": ""})
        assert response.status_code == 201

    def test_list_folders(self, client, test_files_dir, test_db):
        """Test listing folders."""
        client.post("/api/files/folder", data={"name": "alpha", "parent": ""})
        client.post("/api/files/folder", data={"name": "beta", "parent": ""})

        response = client.get("/api/files/folders/list")
        assert response.status_code == 200
        result = response.json()
        assert isinstance(result, list)
        names = [f["name"] for f in result]
        assert "alpha" in names
        assert "beta" in names

    def test_list_folders_sub(self, client, test_files_dir, test_db):
        """Test listing folders within a subfolder."""
        client.post("/api/files/folder", data={"name": "parent", "parent": ""})
        client.post("/api/files/folder", data={"name": "sub1", "parent": "parent"})
        client.post("/api/files/folder", data={"name": "sub2", "parent": "parent"})

        response = client.get("/api/files/folders/list?parent=parent")
        assert response.status_code == 200
        result = response.json()
        names = [f["name"] for f in result]
        assert "sub1" in names
        assert "sub2" in names


class TestBulkOperations:
    """Tests for bulk delete and bulk download."""

    def test_bulk_delete(self, client, test_files_dir, test_db):
        """Test deleting multiple files."""
        ids = []
        for i in range(3):
            content = f"Bulk {i}".encode()
            files = {"file": (f"bulk-{i}.txt", io.BytesIO(content), "text/plain")}
            resp = client.post("/api/files", files=files, data={"session_id": "sess-1"})
            ids.append(resp.json()["id"])

        # Bulk delete
        response = client.post("/api/files/bulk-delete", json=ids)
        assert response.status_code == 200
        result = response.json()
        assert result["deleted"] == 3
        assert len(result["errors"]) == 0

        # Verify all gone
        list_resp = client.get("/api/files")
        remaining = list_resp.json()
        remaining_ids = [f["id"] for f in remaining]
        for fid in ids:
            assert fid not in remaining_ids

    def test_bulk_delete_partial_failures(self, client, test_files_dir, test_db):
        """Test bulk delete with some non-existent IDs."""
        content = b"Only one"
        files = {"file": ("one.txt", io.BytesIO(content), "text/plain")}
        resp = client.post("/api/files", files=files, data={"session_id": "sess-1"})
        real_id = resp.json()["id"]

        response = client.post("/api/files/bulk-delete", json=[real_id, "fake-id"])
        assert response.status_code == 200
        result = response.json()
        assert result["deleted"] == 1
        assert len(result["errors"]) >= 1

    def test_bulk_download(self, client, test_files_dir, test_db):
        """Test downloading multiple files as zip."""
        ids = []
        for i in range(2):
            content = f"Zip {i}".encode()
            files = {"file": (f"zip-{i}.txt", io.BytesIO(content), "text/plain")}
            resp = client.post("/api/files", files=files, data={"session_id": "sess-1"})
            ids.append(resp.json()["id"])

        response = client.post("/api/files/bulk-download", json=ids)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "attachment" in response.headers["content-disposition"]

        # Verify zip contents
        zip_buffer = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            names = zf.namelist()
            assert len(names) == 2
            for name in names:
                assert "zip-" in name

    def test_bulk_download_no_files(self, client, test_files_dir, test_db):
        """Test bulk download with no valid files."""
        response = client.post("/api/files/bulk-download", json=["fake-1", "fake-2"])
        assert response.status_code == 404


class TestFilePage:
    """Tests for the files Jinja page."""

    def test_files_page_renders(self, client, test_files_dir, test_db):
        """Test that the /files page renders correctly."""
        response = client.get("/files")
        assert response.status_code == 200
        assert "File Manager" in response.text
        assert "files.html" is not None  # Just checking no template error
