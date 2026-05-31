"""Authentication routes and middleware."""

import logging
from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

# Adjust these imports to match your project structure
from backend.template_config import templates
from backend.config import CONFIG 

logger = logging.getLogger(__name__)

# Create the router for login endpoints
auth_router = APIRouter()

# --- Middleware Function ---
# Note: No decorator here. We will attach it to the main app in main.py.
async def token_validation_middleware(request: Request, call_next):
    # 1. Bypass authentication for the login page
    if request.url.path == "/login":
        return await call_next(request)

    def _not_authorized_response():
        if request.method == "GET":
            return templates.TemplateResponse(
                request,
                "pages/login.html",
                {"error" : "Session expired or unauthorized. Please log in again."},
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        else:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Unauthorized"},
            )

    # 2. Extract Token: Check Cookie first, then Header
    token_value = request.cookies.get("access_token")
    if not token_value:
        token_value = request.headers.get("Authorization")

    if not token_value:
        return _not_authorized_response()

    # 3. Validate the format
    scheme, _, token = token_value.partition(" ")
    if scheme.lower() == "bearer" and token:
        pass 
    else:
        token = token_value 

    # 4. Validate the token itself
    VALID_TOKEN = CONFIG.get("token", "")
    if token != VALID_TOKEN or not VALID_TOKEN:
        return _not_authorized_response()

    # 5. If valid, proceed
    response = await call_next(request)
    return response


# --- Login Endpoints ---

@auth_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    logger.debug("GET /login rendering login page")
    return templates.TemplateResponse(request, "pages/login.html", {})

@auth_router.post("/login")
async def login(request: Request):
    """Login endpoint to validate credentials and set cookie."""
    data = await request.form()
    username = data.get("username")
    password_hash = data.get("password_hash") 

    # Validate credentials
    if username == CONFIG.get("username") and password_hash == CONFIG.get("password_hash"):
        response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        cookie_max_age = 90 * 24 * 3600  # 90 days in seconds
        response.set_cookie(
            key="access_token",
            value=f"Bearer {CONFIG['token']}",
            httponly=True,
            max_age=cookie_max_age,
            expires=cookie_max_age,
            samesite="lax",
            secure=False, # Set to True in production with HTTPS
        )
        return response
    else:
        return templates.TemplateResponse(
            request,
            "pages/login.html",
            {"error": "Invalid username or password."},
            status_code=status.HTTP_401_UNAUTHORIZED
        )