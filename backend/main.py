"""FastAPI application — main entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.config import DATABASE_PATH
from backend.database import init_db
from backend.template_config import templates
from backend.config import FILES_DIR, CONFIG
from backend.routes.auth import auth_router, token_validation_middleware
from backend.routes.agents import router as agents_router, page_router as agents_page_router
from backend.routes.templates import router as templates_router, page_router as templates_page_router
from backend.routes.sessions import sessions_api_router, sessions_page_router
from backend.routes.files import files_api_router, files_page_router
from backend.routes.settings import router as settings_router, page_router as settings_page_router
from backend.routes.ws import ws_router, set_manager
from backend.acp_core.manager import AcpConnectionManager

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: init DB on startup, cleanup on shutdown."""
    logger.info("Application starting up")
    logger.info("Database path: %s", DATABASE_PATH)
    await init_db()
    logger.info("Database initialized")

    # Ensure files directory exists
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Files directory: %s", FILES_DIR)

    # Initialize ACP connection manager
    manager = AcpConnectionManager()
    set_manager(manager)
    app.state.acp_manager = manager
    logger.debug("AcpConnectionManager initialized")

    # Start idle checker
    idle_task = manager.start_idle_checker()
    app.state.idle_checker_task = idle_task
    logger.debug("Idle checker started")

    yield

    # Cancel idle checker
    if hasattr(app.state, "idle_checker_task") and not app.state.idle_checker_task.done():
        app.state.idle_checker_task.cancel()
        try:
            await app.state.idle_checker_task
        except asyncio.CancelledError:
            pass
        logger.debug("Idle checker cancelled")

    # Shutdown: stop all active ACP sessions
    logger.info("Application shutting down: stopping %d active sessions",
                 len(manager.active_sessions))
    await manager.stop_all()
    logger.info("Application shut down complete")


app = FastAPI(
    title="ACP Chat App",
    description="Web chat for ACP agents",
    version="0.1.0",
    lifespan=lifespan,
)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

if CONFIG.get("enable_auth", False):
    app.middleware("http")(token_validation_middleware)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page."""
    logger.debug("GET / rendering index")
    return templates.TemplateResponse(request, "pages/index.html", {})

# Register routers
app.include_router(auth_router)
app.include_router(agents_router)
app.include_router(agents_page_router)
app.include_router(templates_router)
app.include_router(templates_page_router)
app.include_router(sessions_api_router)
app.include_router(sessions_page_router)
app.include_router(files_api_router)
app.include_router(files_page_router)
app.include_router(settings_router)
app.include_router(settings_page_router)
app.include_router(ws_router)
