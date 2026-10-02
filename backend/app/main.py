from fastapi import FastAPI

from app.api import health

app = FastAPI(title="WealthWise API")
app.include_router(health.router)
