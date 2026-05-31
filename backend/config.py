"""Application configuration.

Stores config paths and settings for the ACP Chat App.
Config directory: ~/.pi/acp-chat-app/
"""

import logging
import os
import json
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_config_dir() -> Path:
    """Get the config directory, creating it if needed."""
    config_dir = Path.home() / ".pi" / "acp-chat-app"
    config_dir.mkdir(parents=True, exist_ok=True)
    logger.debug("Config directory: %s", config_dir)
    return config_dir

def _load_config() -> dict:
    """Load config from environment variables or defaults."""
    config = {
        "enable_auth": False,
        "username": os.getenv("ACP_CHAT_USERNAME", "admin"),
        "password_hash": os.getenv("ACP_CHAT_PASSWORD", "password_hash"),
        "token": os.getenv("ACP_CHAT_TOKEN", "acp_chat_token_is_not_secure"),
        "idel_timeout_seconds": int(os.getenv("ACP_CHAT_IDLE_TIMEOUT_SECONDS", 300)),
        "files_dir": None
    }
    if os.path.exists(os.path.join(_get_config_dir(), "config.json")):
        try:
            with open(os.path.join(_get_config_dir(), "config.json"), "r") as f:
                config_loaded = json.load(f)
                config.update(config_loaded)
                logger.debug("Config loaded from file: %s", config)
        except Exception as e:
            logger.error("Failed to load config from file: %s", e)
    else:
        logger.debug("No config file found, creating default config: %s", config)
        # Save the default config to a file
        with open(os.path.join(_get_config_dir(), "config.json"), "w") as f:
            json.dump(config, f)
    return config

CONFIG_DIR: Path = _get_config_dir()
CONFIG: dict = _load_config()
AGENTS_FILE: Path = CONFIG_DIR / "agents.json"
DATABASE_FILE: Path = CONFIG_DIR / "chat.db"
FILES_DIR: Path = CONFIG_DIR / "files" if CONFIG.get("files_dir") is None else Path(CONFIG["files_dir"])

# Ensure files dir exists
FILES_DIR.mkdir(parents=True, exist_ok=True)
logger.debug("Files directory: %s", FILES_DIR)

# Database path as string for aiosqlite
DATABASE_PATH: str = str(DATABASE_FILE)

DEFAULT_AGENT = {
    "id": "pi-agent",
    "name": "Pi Agent",
    "type": "cli",
    "command": "npx",
    "args": ["-y", "pi-acp"],
    "env_vars": {},
    "description": "Default Pi ACP agent",
}

CLAUDE_AGENT = {
    "id": "claude-agent",
    "name": "Claude Agent",
    "type": "cli",
    "command": "npx",
    "args": ["-y", "@agentclientprotocol/claude-agent-acp"],
    "env_vars": {
        "ANTHROPIC_API_KEY": "$ANTHROPIC_AUTH_TOKEN",
    },
    "description": "Claude Agent SDK via ACP",
}

# Idle timeout in seconds before terminating subprocess
IDLE_TIMEOUT_SECONDS: int = CONFIG.get("idel_timeout_seconds", 300)
