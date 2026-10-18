from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

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
