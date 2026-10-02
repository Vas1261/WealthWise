from fastapi import FastAPI

from app.api import auth, health, imports, portfolio
from app.api.csrf import csrf_middleware
from app.core.config import check_app_settings, settings

check_app_settings(settings)

app = FastAPI(title="WealthWise API")
app.middleware("http")(csrf_middleware)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(portfolio.router)
app.include_router(imports.router)
