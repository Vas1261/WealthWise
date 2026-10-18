from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from app.api import auth, finance, health, imports, portfolio, reports
from app.api.csrf import csrf_middleware
from app.core.config import check_app_settings, settings

check_app_settings(settings)

app = FastAPI(title="WealthWise API")
app.middleware("http")(csrf_middleware)
# Finance routes drop FastAPI's echoed input from schema errors; all other routes keep the default.
app.add_exception_handler(RequestValidationError, finance.finance_validation_handler)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(portfolio.router)
app.include_router(imports.router)
app.include_router(finance.router)
app.include_router(reports.router)

# One container (deployment): the built frontend is served by the API itself, so the browser sees a single
# origin and the SameSite auth cookies work exactly as with the Vite proxy in development. Mounted last, so
# /api and /health always win. The app uses hash routes (#/portfolio), so only index.html and assets exist.
if settings.frontend_dist:
    app.mount("/", StaticFiles(directory=Path(settings.frontend_dist), html=True), name="frontend")
