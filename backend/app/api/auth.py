import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy.orm import Session

from app.api.dependencies import get_auth_context, get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.domain.auth import ACCESS_TOKEN_LIFETIME, REFRESH_TOKEN_LIFETIME, PasswordPolicyError
from app.models import User
from app.services import auth_service
from app.services.auth_service import AuthContext, IssuedTokens

router = APIRouter(prefix="/api/auth", tags=["auth"])

# The refresh cookie is only sent to auth endpoints.
REFRESH_COOKIE_PATH = "/api/auth"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    reporting_currency: str


def _cookie_options(httponly: bool) -> dict:
    return {"httponly": httponly, "secure": settings.cookie_secure, "samesite": "lax"}


def _set_auth_cookies(response: Response, tokens: IssuedTokens) -> None:
    response.set_cookie(
        settings.access_cookie_name, tokens.access_token,
        max_age=int(ACCESS_TOKEN_LIFETIME.total_seconds()), path="/", **_cookie_options(httponly=True),
    )
    response.set_cookie(
        settings.refresh_cookie_name, tokens.refresh_token,
        max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()), path=REFRESH_COOKIE_PATH, **_cookie_options(httponly=True),
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.access_cookie_name, path="/", **_cookie_options(httponly=True))
    response.delete_cookie(settings.refresh_cookie_name, path=REFRESH_COOKIE_PATH, **_cookie_options(httponly=True))


def _set_csrf_cookie(response: Response) -> None:
    # Readable by the frontend on purpose: it echoes the value in the CSRF header.
    response.set_cookie(
        settings.csrf_cookie_name, secrets.token_urlsafe(32),
        max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()), path="/", **_cookie_options(httponly=False),
    )


@router.get("/csrf", status_code=status.HTTP_204_NO_CONTENT)
def issue_csrf_token(response: Response) -> None:
    _set_csrf_cookie(response)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
def register(body: RegisterRequest, db: Session = Depends(get_session)) -> User:
    try:
        return auth_service.register(db, body.email, body.password)
    except PasswordPolicyError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    except auth_service.EmailAlreadyRegistered:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists") from None


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_session)) -> User:
    try:
        user, tokens = auth_service.login(db, body.email, body.password)
    except auth_service.InvalidCredentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password") from None
    _set_auth_cookies(response, tokens)
    _set_csrf_cookie(response)
    return user


@router.post("/refresh", status_code=status.HTTP_204_NO_CONTENT)
def refresh(request: Request, response: Response, db: Session = Depends(get_session)) -> Response:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    try:
        if not refresh_token:
            raise auth_service.NotAuthenticated
        tokens = auth_service.refresh(db, refresh_token)
    except auth_service.NotAuthenticated:
        rejected = JSONResponse({"detail": "Session expired or invalid"}, status_code=status.HTTP_401_UNAUTHORIZED)
        _clear_auth_cookies(rejected)
        return rejected
    _set_auth_cookies(response, tokens)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, context: AuthContext = Depends(get_auth_context), db: Session = Depends(get_session)) -> None:
    auth_service.logout(db, context.session_id)
    _clear_auth_cookies(response)
    _set_csrf_cookie(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
