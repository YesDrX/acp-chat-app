"""File management routes — REST API + Jinja pages.

Provides file upload, download, rename, delete, folder management,
and bulk operations. Disk-only by design (no DB metadata table).
"""

from __future__ import annotations

import io
import logging
import mimetypes
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Generator

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
    PlainTextResponse,
)

from backend.config import FILES_DIR
from backend.template_config import templates


def _files_root() -> str:
    """Return the real path of FILES_DIR (computed lazily so test patches work)."""
    return os.path.realpath(FILES_DIR)


def _safe_path(path_str: str) -> Path | None:
    """Resolve a subpath under FILES_DIR and verify it stays within bounds.
    Returns the resolved Path, or None if the path escapes the files root."""
    root = _files_root()
    full = Path(os.path.realpath(os.path.join(root, path_str)))
    if str(full).startswith(root):
        return full
    return None

logger = logging.getLogger(__name__)

# /api/files router
files_api_router = APIRouter(prefix="/api/files", tags=["files"])


# --- API endpoints ---


@files_api_router.get("")
async def list_files(
    session_id: str | None = Query(None),
    folder: str | None = Query(None),
):
    """List all files and folders, optionally filtered by session_id and/or subfolder.

    Reads directly from disk. No DB indexing.
    """
    logger.debug("GET /api/files — listing: session_id=%s folder=%s", session_id, folder)

    scan_dir = _files_root()
    if folder:
        safe = _safe_path(folder)
        if not safe:
            return JSONResponse(content={"detail": "Invalid path"}, status_code=400)
        scan_dir = safe

    folders = []
    files = []
    if os.path.isdir(scan_dir):
        for entry in sorted(os.listdir(scan_dir)):
            full = os.path.join(scan_dir, entry)
            rel = str(Path(full).relative_to(Path(_files_root())))
            try:
                st = os.stat(full)
            except OSError:
                continue
            if os.path.isdir(full):
                folders.append({
                    "id": entry,
                    "name": entry,
                    "path": rel,
                    "size": 0,
                    "type": "folder",
                    "created_at": "",
                })
            else:
                files.append({
                    "id": rel,
                    "session_id": "",
                    "name": entry,
                    "path": rel,
                    "size": st.st_size,
                    "created_at": "",
                })

    result = folders + files
    logger.debug("GET /api/files — %d folders, %d files", len(folders), len(files))
    return JSONResponse(content=result)


@files_api_router.post("")
async def upload_file(
    file: UploadFile = File(...),
    session_id: str = Form(""),
    folder: str = Form(""),
):
    """Upload a file.

    Stores the file on disk under FILES_DIR and creates a DB metadata record.
    """
    logger.debug(
        "POST /api/files — uploading: name=%s size=%s session=%s folder=%s",
        file.filename, file.size if file.size else "unknown", session_id, folder,
    )

    if not file.filename:
        logger.debug("POST /api/files — no filename provided")
        return JSONResponse(
            content={"detail": "No file provided"},
            status_code=400,
        )

    # Build target path (with realpath check)
    target_folder = _files_root()
    if folder:
        safe = _safe_path(folder)
        if not safe:
            return JSONResponse(content={"detail": "Invalid path"}, status_code=400)
        target_folder = str(safe)
        os.makedirs(target_folder, exist_ok=True)
        logger.debug("Created/ensured upload folder: %s", target_folder)

    # Sanitize filename
    filename = file.filename.replace("/", "_").replace("\\", "_")
    target_path = os.path.join(target_folder, filename)

    # Write file to disk
    content = await file.read()
    with open(target_path, 'wb') as f:
        f.write(content)
    logger.debug("File written to disk: %s (%d bytes)", target_path, len(content))

    # Build disk-only metadata response (no DB — design change removed files table).
    relative_path = str(Path(target_path).relative_to(Path(_files_root())))
    file_id = relative_path  # Use relative path as the id for downstream endpoints.
    return JSONResponse(
        content={
            "id": file_id,
            "session_id": session_id,
            "name": filename,
            "path": relative_path,
            "size": len(content),
        },
        status_code=201,
    )


# ── Text/code extensions to serve inline as plain text ──
_TEXT_EXTENSIONS: set[str] = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hh", ".hxx",
    ".nim", ".nims", ".cfg", ".nimble",
    ".rs", ".go", ".rb", ".php", ".pl", ".pm", ".lua",
    ".java", ".kt", ".scala", ".clj", ".ex", ".exs",
    ".swift", ".m", ".mm",
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".css", ".scss", ".less", ".sass",
    ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".editorconfig", ".gitignore", ".dockerfile",
    ".sql", ".r", ".mjs", ".cjs", ".vue", ".svelte",
    ".gradle", ".properties", ".lock", ".log",
}

# ── Known media types the browser can open inline ──
_VIEWABLE_MEDIA: set[str] = {
    ".pdf",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".mp4", ".webm", ".ogv", ".mov", ".avi",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
}


@files_api_router.get("/download")
async def download_file(path: str = Query(...), dl: bool = False):
    """Serve a file by path with smart content-type handling."""
    logger.debug("GET /api/files/download — path=%s dl=%s", path, dl)

    file_path = _safe_path(path)
    if not file_path or not file_path.exists():
        return JSONResponse(content={"detail": "File not found"}, status_code=404)

    file_name = os.path.basename(path)

    ext = os.path.splitext(file_name)[1].lower()
    logger.debug("Serving file: %s (ext=%s, dl=%s)", file_name, ext, dl)

    # ── Force download as attachment ──
    if dl:
        media_type, _ = mimetypes.guess_type(file_name)
        return FileResponse(
            path=str(file_path),
            filename=file_name,
            media_type=media_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
        )

    # ── .md files: render as HTML ──
    if ext == ".md":
        import markdown as md_lib
        content = file_path.read_text(encoding="utf-8", errors="replace")
        html_body = md_lib.markdown(content, extensions=["fenced_code", "codehilite", "tables"])
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{file_name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 800px; margin: 0 auto; padding: 2rem 1rem;
         line-height: 1.6; color: #e0e0e0; background: #1a1a2e; }}
  pre {{ background: #16213e; padding: 1rem; border-radius: 6px; overflow-x: auto; }}
  code {{ background: #16213e; padding: 0.2em 0.4em; border-radius: 3px; font-size: 0.9em; }}
  pre code {{ padding: 0; background: none; }}
  a {{ color: #64b5f6; }}
  h1, h2, h3, h4 {{ color: #e0e0e0; }}
  blockquote {{ border-left: 4px solid #64b5f6; margin-left: 0; padding-left: 1rem; color: #aaa; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #333; padding: 0.5rem; text-align: left; }}
  th {{ background: #16213e; }}
  img {{ max-width: 100%; }}
</style>
</head>
<body>{html_body}</body>
</html>"""
        return HTMLResponse(content=html)

    # ── .html / .htm files: serve as rendered webpage ──
    if ext in (".html", ".htm"):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return HTMLResponse(content=content)

    # ── Text/code files: serve inline as plain text ──
    if ext in _TEXT_EXTENSIONS:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return PlainTextResponse(content=content)

    # ── Known media types: serve inline ──
    if ext in _VIEWABLE_MEDIA:
        media_type, _ = mimetypes.guess_type(file_name)
        if not media_type:
            media_type = "application/octet-stream"
        base = "inline"
        logger.debug("Serving inline: %s (%s)", file_name, media_type)
        return FileResponse(
            path=str(file_path),
            filename=file_name,
            media_type=media_type,
            headers={"Content-Disposition": f'{base}; filename="{file_name}"'},
        )

    # ── Everything else: download as attachment ──
    logger.debug("Serving as download: %s", file_name)
    return FileResponse(
        path=str(file_path),
        filename=file_name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@files_api_router.delete("")
async def delete_file(path: str = Query(...)):
    """Delete a file or folder from disk."""
    logger.debug("DELETE /api/files — path=%s", path)

    file_path = _safe_path(path)
    if not file_path or not file_path.exists():
        logger.debug("DELETE /api/files — path=%s not found", path)
        return JSONResponse(content={"detail": "File not found"}, status_code=404)

    try:
        if file_path.is_dir():
            import shutil
            shutil.rmtree(file_path)
        else:
            file_path.unlink()
        logger.debug("Deleted: %s", file_path)
    except Exception as e:
        logger.warning("Delete failed: %s", e)
        return JSONResponse(content={"detail": f"Delete failed: {e}"}, status_code=500)

    return JSONResponse(content={"detail": "File deleted"})


@files_api_router.put("")
async def rename_file(path: str = Query(...), name: str = Form(...)):
    """Rename a file."""
    logger.debug("PUT /api/files — path=%s new_name=%s", path, name)

    file_path = _safe_path(path)
    if not file_path or not file_path.exists():
        return JSONResponse(content={"detail": "File not found"}, status_code=404)

    new_path = file_path.parent / name
    new_relative = str(new_path.relative_to(_files_root())) if str(new_path).startswith(_files_root()) else name

    try:
        file_path.rename(new_path)
    except Exception as e:
        logger.error("Rename failed: %s", e, exc_info=True)
        return JSONResponse(content={"detail": f"Rename failed: {e}"}, status_code=500)

    logger.debug("File renamed: %s -> %s", file_path, new_path)
    return JSONResponse(content={"id": new_relative, "name": name, "path": new_relative})


@files_api_router.post("/folder")
async def create_folder(name: str = Form(...), parent: str = Form("")):
    """Create a new folder."""
    logger.debug("POST /api/files/folder — creating: name=%s parent=%s", name, parent)
    target = _files_root()
    if parent:
        safe = _safe_path(parent)
        if not safe:
            return JSONResponse(content={"detail": "Invalid path"}, status_code=400)
        target = str(safe)

    folder_path = os.path.join(target, name)
    os.makedirs(folder_path, exist_ok=True)
    logger.debug("Folder created: %s", folder_path)

    return JSONResponse(
        content={"name": name, "path": name, "type": "folder"},
        status_code=201,
    )


@files_api_router.delete("/folder/{folder_path:path}")
async def delete_folder(folder_path: str):
    """Delete a folder and all its contents."""
    logger.debug("DELETE /api/files/folder/%s — deleting folder", folder_path)
    target = _safe_path(folder_path)

    if not target:
        logger.warning("Attempted to escape files root: %s", folder_path)
        return JSONResponse(content={"detail": "Invalid path"}, status_code=400)

    # Prevent deleting the root
    if str(target) == _files_root():
        logger.warning("Attempted to delete root folder")
        return JSONResponse(content={"detail": "Cannot delete root folder"}, status_code=400)

    if not target.exists():
        logger.debug("Folder not found: %s", target)
        return JSONResponse(content={"detail": "Folder not found"}, status_code=404)

    if not target.is_dir():
        logger.debug("Path is not a folder: %s", target)
        return JSONResponse(content={"detail": "Not a folder"}, status_code=400)

    # Remove the directory tree
    import shutil
    shutil.rmtree(target)
    logger.debug("Folder deleted: %s", target)

    return JSONResponse(content={"detail": "Folder deleted", "records_cleaned": 0})


@files_api_router.post("/bulk-delete")
async def bulk_delete(file_ids: list[str]):
    """Delete multiple files."""
    logger.info("POST /api/files/bulk-delete — deleting %d files", len(file_ids))
    deleted = 0
    errors = []

    for fid in file_ids:
        try:
            full_path = _safe_path(fid)
            if full_path and full_path.exists():
                if full_path.is_dir():
                    shutil.rmtree(full_path)
                    deleted += 1
                    logger.debug("Bulk delete: removed folder %s", fid)
                else:
                    full_path.unlink()
                    logger.debug("Bulk delete: removed file %s", fid)
                    deleted += 1
            else:
                errors.append(f"File not found: {fid}")
        except Exception as e:
            logger.warning("Bulk delete error: %s for %s", e, fid)
            errors.append(f"Failed to delete {fid}: {e}")

    logger.info("Bulk delete complete: %d deleted, %d errors", deleted, len(errors))
    return JSONResponse(content={
        "deleted": deleted,
        "errors": errors,
    })


def _zip_generator(file_ids: list[str]) -> Generator[bytes, None, None]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fid in file_ids:
            full_path = _safe_path(fid)
            if not full_path.exists():
                continue
            if full_path.is_dir():
                for root, dirs, files in os.walk(full_path):
                    for file in files:
                        file_full = os.path.join(root, file)
                        arcname = os.path.relpath(file_full, _files_root())
                        zf.write(file_full, arcname=arcname)
            else:
                arcname = os.path.relpath(full_path, _files_root())
                zf.write(str(full_path), arcname=arcname)

    buffer.seek(0)
    while chunk := buffer.read(64 * 1024):  # 64KB chunks
        yield chunk

@files_api_router.post("/bulk-download")
async def bulk_download(file_ids: list[str]):
    if not file_ids:
        return JSONResponse(content={"detail": "No files specified"}, status_code=404)
    valid = [fid for fid in file_ids if (p := _safe_path(fid)) and p.exists()]
    if not valid:
        return JSONResponse(content={"detail": "No valid files found"}, status_code=404)
    return StreamingResponse(
        _zip_generator(valid),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=files.zip"},
    )


# --- Folder listing helper ---


@files_api_router.get("/folders/list")
async def list_folders(parent: str = Query("")):
    """List folders under the given parent path."""
    logger.debug("GET /api/files/folders/list — parent=%s", parent)
    target = _files_root()
    if parent:
        safe = _safe_path(parent)
        if not safe:
            return JSONResponse(content={"detail": "Invalid path"}, status_code=400)
        target = str(safe)

    folders = []
    if os.path.isdir(target):
        for entry in sorted(os.listdir(target)):
            full = os.path.join(target, entry)
            if os.path.isdir(full):
                folders.append({
                    "name": entry,
                    "path": entry,
                })

    logger.debug("Found %d folders in %s", len(folders), target)
    return JSONResponse(content=folders)


# --- Per-id endpoints (id == relative path under FILES_DIR) ---
# These must come AFTER the static-path routes above so that
# `/api/files/download`, `/api/files/folder`, `/api/files/bulk-*`, and
# `/api/files/folders/list` continue to match their specific routes.


def _file_meta_from_disk(file_id: str) -> dict | None:
    """Build a metadata dict for a file at the given relative path.

    Returns None if the file doesn't exist or the path is invalid.
    """
    file_path = _safe_path(file_id)
    if not file_path or not file_path.exists() or not file_path.is_file():
        return None
    st = file_path.stat()
    return {
        "id": file_id,
        "name": file_path.name,
        "path": file_id,
        "size": st.st_size,
    }


@files_api_router.get("/{file_id:path}/download")
async def download_file_by_id(file_id: str, dl: bool = False):
    """Download a file by its relative path id.

    Registered BEFORE the catch-all metadata route so the `/download` suffix
    is matched specifically.
    """
    logger.debug("GET /api/files/%s/download — dl=%s", file_id, dl)
    file_path = _safe_path(file_id)
    if not file_path or not file_path.exists() or not file_path.is_file():
        return JSONResponse(content={"detail": "File not found"}, status_code=404)
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_path.name}"'},
    )


@files_api_router.get("/{file_id:path}")
async def get_file_by_id(file_id: str):
    """Return disk-based metadata for a file by its relative path id."""
    logger.debug("GET /api/files/%s — metadata", file_id)
    meta = _file_meta_from_disk(file_id)
    if not meta:
        return JSONResponse(content={"detail": "File not found"}, status_code=404)
    return JSONResponse(content=meta)


@files_api_router.delete("/{file_id:path}")
async def delete_file_by_id(file_id: str):
    """Delete a file by its relative path id."""
    logger.debug("DELETE /api/files/%s — by id", file_id)
    file_path = _safe_path(file_id)
    if not file_path or not file_path.exists():
        return JSONResponse(content={"detail": "File not found"}, status_code=404)
    try:
        if file_path.is_dir():
            shutil.rmtree(file_path)
        else:
            file_path.unlink()
    except Exception as e:
        logger.warning("Delete failed: %s", e)
        return JSONResponse(content={"detail": f"Delete failed: {e}"}, status_code=500)
    return JSONResponse(content={"detail": "File deleted"})


@files_api_router.put("/{file_id:path}")
async def rename_file_by_id(file_id: str, name: str = Form(...)):
    """Rename a file by its relative path id."""
    logger.debug("PUT /api/files/%s — rename to %s", file_id, name)
    file_path = _safe_path(file_id)
    if not file_path or not file_path.exists() or file_path.is_dir():
        return JSONResponse(content={"detail": "File not found"}, status_code=404)
    new_path = file_path.parent / name
    new_relative = (
        str(new_path.relative_to(Path(_files_root())))
        if str(new_path).startswith(_files_root())
        else name
    )
    try:
        file_path.rename(new_path)
    except Exception as e:
        logger.error("Rename failed: %s", e, exc_info=True)
        return JSONResponse(content={"detail": f"Rename failed: {e}"}, status_code=500)
    return JSONResponse(content={"id": new_relative, "name": name, "path": new_relative})


# --- Page routes ---

files_page_router = APIRouter(prefix="", tags=["pages"])


@files_page_router.get("/files", response_class=HTMLResponse)
async def files_page(request: Request):
    """Render the file management page."""
    logger.debug("GET /files — rendering files page")
    return templates.TemplateResponse(
        "pages/files.html",
        {"request": request},
    )
