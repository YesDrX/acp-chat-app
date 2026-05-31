# ACP Chat App

ChatGPT-style web interface for interacting with [ACP](https://agentclientprotocol.com) agents.

Built with **Python FastAPI + Jinja2**, running as a single process with WebSocket streaming, SQLite persistence, and subprocess-based agent lifecycle management.

## Screenshots

```
┌─────────────────────────────────────────────────────────┐
│  ┌──────────┐  ┌──────────────────────────────────────┐│
│  │ ACP Chat │  │  💬 pi-agent · gpt-4o          🟢    ││
│  │          │  │  ───────────────────────────────────  ││
│  │ 💬 Ses.. │  │                                      ││
│  │ 🤖 Age.. │  │  ┌──────────────────────────┐        ││
│  │ 📋 Tem.. │  │  │ Hello! How can I help?   │        ││
│  │ 📁 Files │  │  └──────────────────────────┘        ││
│  │ ⚙️ Sett.. │  │        ┌─────────────────────────┐   ││
│  │          │  │        │ I need to write a test. │   ││
│  │          │  │        └─────────────────────────┘   ││
│  │          │  │                                      ││
│  └──────────┘  │  [⏸] [⏹] [📎] [________________] [↑]││
│                └──────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- **Python 3.11+**
- **npx** (for `pi-acp` agent — install via Node.js)
- **pip** or **uv** for package management

### Install

```bash
cd acp-chat-app
pip install -e ".[dev]"
```

### Run

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
# change config at ~/.pi/acp-chat-app/config.json
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### First Use

1. **Add an agent** — Go to **Agents** page, click **Add Agent**. The default is `npx -y pi-acp` which uses the Pi ACP agent.
2. **Create a session** — Go to **Sessions** page, click **New Session**, pick your agent and configure settings.
3. **Chat** — Click on the session to open the chat interface. Type a message and press Enter.

### Development

```bash
# Run tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_e2e.py -v
```

## Features

- **Agent Management** — CRUD for ACP agent configurations (command, args, env vars, type)
- **Session Management** — Create/list/resume/delete sessions per agent, with templates
- **Real-time Chat** — WebSocket-based streaming, character-by-character text display
- **Smart Bubbles** — Consecutive text chunks auto-merge; tool calls shown in collapsible JSON panels
- **Slash Commands** — Type `/` to see available commands from the agent
- **File Upload** — Drag-and-drop or click to attach files in chat
- **Session Settings** — Change model, permission mode, and effort level mid-session
- **Idle Teardown** — Agent subprocess auto-terminates after configurable idle timeout (default 5 min)
- **Session Resume** — Reconnect to existing sessions using ACP `session/load`
- **File Manager** — Browse, upload, download, rename, and delete files with grid/list views
- **Templates** — Named presets for quickly creating sessions with pre-configured settings
- **Dark Theme** — ChatGPT-inspired dark UI with smooth animations
- **Debug Logging** — `console.log` in browser, `logging.debug` in Python backend

## Architecture

```
Browser (Jinja + Vanilla JS)
    │
    ├── HTTP ──→ FastAPI (Python)
    │              ├── Routes (REST API + Page rendering)
    │              ├── Models (Agents, Sessions, Templates, Files)
    │              ├── Services (Business logic)
    │              └── ACP Core (Agent subprocess management)
    │
    └── WebSocket ──→ WS Handler
                        └── ACP Bridge (asyncio.Queue)
                              └── ACP Connection Manager
                                    └── Subprocess (pi-acp)
```

### Module Structure

```
backend/
├── main.py              # FastAPI app, lifespan, static mounts
├── config.py            # Config paths, default settings
├── database.py          # SQLite connection, schema initialization
├── template_config.py   # Shared Jinja2Templates instance
├── models/
│   ├── agent.py         # Agent config file CRUD
│   ├── session.py       # Session SQLite CRUD
│   ├── template.py      # Template SQLite CRUD
│   └── file.py          # File metadata SQLite CRUD
├── routes/
│   ├── agents.py        # /api/agents + /agents page
│   ├── sessions.py      # /api/sessions + /sessions page
│   ├── templates.py     # /api/templates + /templates page
│   ├── files.py         # /api/files + /files page
│   ├── chat.py          # /sessions/{id} chat page
│   ├── settings.py      # /api/settings + /settings page
│   └── ws.py            # /ws/{session_id} WebSocket handler
├── acp_core/
│   ├── client.py        # AcpClient (implements SDK Client interface)
│   ├── manager.py       # AcpConnectionManager (subprocess lifecycle)
│   └── bridge.py        # AcpBridge (async queue bridge)
└── services/
    ├── agent_service.py
    ├── session_service.py
    └── file_service.py

templates/
├── base.html            # Layout shell (sidebar, header)
└── pages/
    ├── index.html       # Home / landing page
    ├── agents.html      # Agent management
    ├── sessions.html    # Session list + creation
    ├── session_chat.html # Chat interface (WebSocket)
    ├── templates.html   # Template management
    ├── files.html       # File manager
    └── settings.html    # Global settings

static/
├── css/style.css        # ChatGPT-inspired dark theme
└── js/
    ├── chat.js          # WebSocket client + message rendering
    ├── bubbles.js       # Smart bubble grouping manager
    ├── file_upload.js   # File upload/drag-drop manager
    └── utils.js         # Shared helpers

tests/
├── conftest.py          # Test fixtures
├── test_database.py     # DB schema tests
├── test_agents.py       # Agent model tests
├── test_agent_routes.py # Agent API route tests
├── test_sessions.py     # Session model tests
├── test_session_routes.py # Session API route tests
├── test_templates.py    # Template model tests
├── test_template_routes.py # Template API route tests
├── test_file_routes.py  # File API route tests
├── test_acp_client.py   # ACP client tests
├── test_acp_bridge.py   # ACP bridge tests
├── test_acp_manager.py  # Connection manager tests
├── test_ws.py           # WebSocket handler tests
├── test_chat.py         # Chat integration tests
├── test_bubbles.py      # Bubble manager tests (JS)
├── test_idle.py         # Idle timeout tests
├── test_resume.py       # Session resume tests
├── test_main.py         # Main app tests
├── test_settings.py     # Settings tests
└── test_e2e.py          # End-to-end lifecycle tests
```

## Configuration

Configuration files live in `~/.pi/acp-chat-app/`:

| File | Purpose |
|------|---------|
| `agents.json` | Agent configurations (name, command, args, env vars) |
| `settings.json` | Global settings (idle timeout, theme) |
| `chat.db` | SQLite database (sessions, templates, file metadata) |
| `files/` | Uploaded files directory |

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `idle_timeout_seconds` | 300 | Seconds before idle agent subprocess is terminated |
| `theme` | dark | UI theme (`dark` or `light`) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Home page |
| `GET` | `/agents` | Agent management page |
| `GET` | `/api/agents` | List agents |
| `POST` | `/api/agents` | Create agent |
| `PUT` | `/api/agents/{id}` | Update agent |
| `DELETE` | `/api/agents/{id}` | Delete agent |
| `GET` | `/sessions` | Sessions page |
| `GET` | `/api/sessions` | List sessions |
| `POST` | `/api/sessions` | Create session |
| `GET` | `/api/sessions/{id}` | Get session details |
| `POST` | `/api/sessions/{id}` | Update session settings |
| `DELETE` | `/api/sessions/{id}` | Delete session |
| `GET` | `/sessions/{id}` | Chat page |
| `WS` | `/ws/{session_id}` | WebSocket for session chat |
| `GET` | `/templates` | Templates page |
| `GET` | `/api/templates` | List templates |
| `POST` | `/api/templates` | Create template |
| `PUT` | `/api/templates/{id}` | Update template |
| `DELETE` | `/api/templates/{id}` | Delete template |
| `GET` | `/files` | File manager page |
| `GET` | `/api/files` | List files |
| `POST` | `/api/files` | Upload file |
| `DELETE` | `/api/files/{id}` | Delete file |
| `PUT` | `/api/files/{id}` | Rename file |
| `GET` | `/api/files/{id}/download` | Download file |
| `POST` | `/api/files/folder` | Create folder |
| `POST` | `/api/files/bulk-delete` | Bulk delete files |
| `POST` | `/api/files/bulk-download` | Bulk download files |
| `GET` | `/settings` | Settings page |
| `GET` | `/api/settings` | Get settings |
| `PUT` | `/api/settings` | Update settings |

## Debugging

**Backend:** All ACP messages and route operations are logged at `DEBUG` level:
```python
logging.basicConfig(level=logging.DEBUG)
```

**Frontend:** All WebSocket events and ACP messages are logged to browser console with `[ACP Chat]` prefix. Open DevTools (F12) to inspect.

## Requirements

- Python 3.11+
- Node.js (for `npx pi-acp`)
- Dependencies: `fastapi`, `uvicorn`, `jinja2`, `aiosqlite`, `python-multipart`, `websockets`, `agent-client-protocol`

## License

MIT
