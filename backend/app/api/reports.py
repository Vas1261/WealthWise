"""Report downloads (FR-23 monthly report, FR-24 portfolio report) as CSV or PDF.

The user always comes from the session cookie. Responses are attachments and never cached.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api import report_files
from app.api.dependencies import get_current_user
from app.api.finance import ISSUE_MESSAGES
from app.core.database import get_session
from app.domain.finance import FinanceValidationError
from app.models import User
from app.services import portfolio_service, report_service

router = APIRouter(prefix="/api/reports", tags=["reports"])

CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_session)]
Format = Annotated[Literal["csv", "pdf"], Query()]
MEDIA_TYPES = {"csv": "text/csv; charset=utf-8", "pdf": "application/pdf"}


def _download(content: bytes, file_format: str, name: str) -> Response:
    return Response(content, media_type=MEDIA_TYPES[file_format], headers={
        "Content-Disposition": f'attachment; filename="{name}.{file_format}"',
        "Cache-Control": "no-store",
    })


@router.get("/monthly")
def monthly_report(user: CurrentUser, db: DbSession, month: Annotated[str, Query()], format: Format = "pdf") -> Response:
    try:
        report = report_service.monthly(db, user, month)
    except FinanceValidationError as error:
        return JSONResponse({"detail": ISSUE_MESSAGES[error.issue], "code": error.issue.value},
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    now = datetime.now(UTC)
    render = report_files.monthly_csv if format == "csv" else report_files.monthly_pdf
    return _download(render(report, now), format, f"wealthwise-monthly-{report.year:04d}-{report.month:02d}")


@router.get("/portfolio")
def portfolio_report(user: CurrentUser, db: DbSession, format: Format = "pdf") -> Response:
    try:
        summary = report_service.portfolio(db, user)
    except portfolio_service.PortfolioDataError as error:  # same conflict as GET /api/portfolio
        return JSONResponse(
            {"detail": f"{error}; re-import the latest {error.broker} {error.asset_class} statement"},
            status_code=status.HTTP_409_CONFLICT,
        )
    now = datetime.now(UTC)
    render = report_files.portfolio_csv if format == "csv" else report_files.portfolio_pdf
    return _download(render(summary, now), format, f"wealthwise-portfolio-{now:%Y-%m-%d}")
