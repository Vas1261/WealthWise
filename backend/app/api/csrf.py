from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.domain.auth import csrf_tokens_match

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def csrf_middleware(request: Request, call_next):
    """Double-submit check on every state-changing request, authenticated or not (spec 2.6)."""
    if request.method not in SAFE_METHODS and not csrf_tokens_match(
        request.cookies.get(settings.csrf_cookie_name), request.headers.get(settings.csrf_header_name)
    ):
        return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=status.HTTP_403_FORBIDDEN)
    return await call_next(request)
