from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_session
from app.models import User
from app.services import auth_service
from app.services.auth_service import AuthContext


def get_auth_context(request: Request, db: Session = Depends(get_session)) -> AuthContext:
    """The authenticated user comes only from the access-token cookie, never from request data."""
    access_token = request.cookies.get(settings.access_cookie_name)
    if not access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        return auth_service.authenticate(db, access_token)
    except auth_service.NotAuthenticated:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated") from None


def get_current_user(context: AuthContext = Depends(get_auth_context)) -> User:
    return context.user
