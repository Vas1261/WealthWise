from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.services import health_service

router = APIRouter()


@router.get("/health")
def health(response: Response, session: Session = Depends(get_session)) -> dict:
    result = health_service.get_health(session)
    if result["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
