"""Settings routes for ACP Chat App.

GET  /settings           — render settings page
GET  /api/settings       — get current settings as JSON
GET  /api/skills         — list discovered skills
GET  /api/skills/{id}    — get skill markdown rendered as HTML
GET  /api/mcp            — list discovered MCP servers
"""

import json
import logging
import re
from pathlib import Path

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.config import CONFIG_DIR, IDLE_TIMEOUT_SECONDS, FILES_DIR
from backend.template_config import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])
page_router = APIRouter(tags=["settings-pages"])

SETTINGS_FILE: Path = CONFIG_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "idle_timeout_seconds": IDLE_TIMEOUT_SECONDS,
    "theme": "dark",
    "files_directory": str(FILES_DIR),
    "config_directory": str(CONFIG_DIR),
    "mcp_config_paths": [
        {"agent": "Claude Code", "path": str(Path.home() / ".claude" / "settings.json")},
        {"agent": "Claude Code", "path": str(Path.home() / ".claude.json")},
    ],
    "skill_search_dirs": [
        Path.home() / ".claude" / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".pi" / "skills",
        Path.home() / "skills"
    ]
}


def _load_settings() -> dict:
    logger.debug("Loading settings from %s", SETTINGS_FILE)
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load settings: %s — using defaults", e)
        return dict(DEFAULT_SETTINGS)


def _save_settings(settings: dict) -> None:
    logger.debug("Saving settings to %s: %s", SETTINGS_FILE, settings)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


SETTINGS = _load_settings()

# ---------------------------------------------------------------------------
# Skills discovery
# ---------------------------------------------------------------------------

# Directories to scan, in priority order
_SKILL_SEARCH_DIRS = SETTINGS.get("skill_search_dirs", [])


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Extract YAML frontmatter. Handles plain, folded (>), and literal (|) block scalars."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if not match:
        return {}, raw

    meta = {}
    body = raw[match.end():]
    lines = match.group(1).splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]
        if ":" not in line or line.startswith(" "):
            i += 1
            continue

        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()

        if val in (">", "|"):
            # Collect indented continuation lines
            block_lines = []
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                block_lines.append(lines[i].strip())
                i += 1
            if val == ">":
                # Folded: join into single line with spaces
                meta[key] = " ".join(block_lines)
            else:
                # Literal: preserve newlines
                meta[key] = "\n".join(block_lines)
        else:
            meta[key] = val
            i += 1

    return meta, body


def _discover_skills() -> list[dict]:
    seen: set[str] = set()
    skills: list[dict] = []

    for base in _SKILL_SEARCH_DIRS:
        if not base.is_dir():
            continue

        for md_file in sorted(base.glob("*.md")):
            name = md_file.stem
            if name in seen:
                continue
            seen.add(name)
            raw = md_file.read_text(encoding="utf-8", errors="replace")
            meta, _ = _parse_frontmatter(raw)
            skills.append({
                "id": name,
                "name": meta.get("name") or name.replace("-", " ").replace("_", " ").title(),
                "description": meta.get("description", ""),
                "path": str(md_file),
                "source_dir": str(base),
            })

        for subdir in sorted(p for p in base.iterdir() if p.is_dir()):
            name = subdir.name
            if name in seen:
                continue
            for candidate in ("SKILL.md", "README.md"):
                md_file = subdir / candidate
                if md_file.exists():
                    seen.add(name)
                    raw = md_file.read_text(encoding="utf-8", errors="replace")
                    meta, _ = _parse_frontmatter(raw)
                    skills.append({
                        "id": name,
                        "name": meta.get("name") or name.replace("-", " ").replace("_", " ").title(),
                        "description": meta.get("description", ""),
                        "path": str(md_file),
                        "source_dir": str(base),
                    })
                    break

    return skills


def _skill_by_id(skill_id: str) -> dict | None:
    for s in _discover_skills():
        if s["id"] == skill_id:
            return s
    return None


# ---------------------------------------------------------------------------
# MCP discovery
# ---------------------------------------------------------------------------

# Config files that may contain MCP server definitions, keyed by agent name
_MCP_CONFIG_PATHS: list[tuple[str, Path]] = SETTINGS.get("mcp_config_paths", [])

def _discover_mcp() -> list[dict]:
    current = _load_settings()
    config_path_entries = current.get("mcp_config_paths", [])

    results: list[dict] = []
    seen_files: set[str] = set()

    for entry in config_path_entries:
        agent_name = entry.get("agent", "Unknown")
        config_path = Path(entry.get("path", "")).expanduser()

        if not config_path.exists():
            continue
        if str(config_path) in seen_files:
            continue
        seen_files.add(str(config_path))

        try:
            with open(config_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read MCP config %s: %s", config_path, e)
            continue

        mcp_servers = data.get("mcpServers") or data.get("mcp_servers") or {}
        for server_name, cfg in mcp_servers.items():
            if not isinstance(cfg, dict):
                continue
            command = cfg.get("command", "")
            args = cfg.get("args", [])
            description = cfg.get("description") or cfg.get("desc") or ""
            if not description and command:
                description = f"{command} {' '.join(str(a) for a in args[:3])}".strip()
            results.append({
                "agent": agent_name,
                "name": server_name,
                "display_name": server_name.replace("-", " ").replace("_", " ").title(),
                "command": command,
                "args": args,
                "description": description,
                "tools": cfg.get("tools") or [],
                "config_file": str(config_path),
            })

    logger.debug("Discovered %d MCP servers", len(results))
    return results

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@page_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    logger.debug("GET /settings")
    current = _load_settings()
    return templates.TemplateResponse(request, "pages/settings.html", {
        "settings": current,
    })


@router.get("/api/settings")
async def get_settings():
    return _load_settings()


@router.get("/api/skills")
async def list_skills():
    skills = _discover_skills()
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],   # ← was missing
            "source_dir": s["source_dir"],
        }
        for s in skills
    ]

@router.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str):
    if not re.fullmatch(r"[a-zA-Z0-9_\-]+", skill_id):
        return JSONResponse({"detail": "Invalid skill id"}, status_code=400)
    skill = _skill_by_id(skill_id)
    if not skill:
        return JSONResponse({"detail": "Skill not found"}, status_code=404)
    try:
        raw = Path(skill["path"]).read_text(encoding="utf-8")
    except OSError as e:
        return JSONResponse({"detail": "Could not read skill file"}, status_code=500)
    html = markdown.markdown(raw, extensions=["fenced_code", "tables", "toc"])
    return {"id": skill["id"], "name": skill["name"], "html": html, "path": skill["path"]}

@router.get("/api/mcp")
async def list_mcp():
    return _discover_mcp()

class McpConfigPathsUpdate(BaseModel):
    mcp_config_paths: list[dict]  # [{"agent": "...", "path": "..."}]

@router.put("/api/settings/mcp-paths")
async def update_mcp_paths(body: McpConfigPathsUpdate):
    """Add/remove MCP config file paths."""
    current = _load_settings()
    current["mcp_config_paths"] = body.mcp_config_paths
    _save_settings(current)
    return current