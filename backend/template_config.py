"""Shared Jinja2 templates instance — avoids circular imports between main and routes."""

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
