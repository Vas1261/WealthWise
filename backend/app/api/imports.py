"""Statement upload: POST /api/imports (one .xlsx file, multipart field "file").

The user comes from the session cookie only; any user_id in the request is ignored. CSRF is enforced
by the global middleware like every other state-changing route. The upload is streamed to a private
temporary file (deleted afterwards) and handed to the existing ImportService, which does adapter
detection, parsing and the snapshot rules. Responses never contain filesystem paths, holdings or
cell values; row errors carry only row number, column and a value-free message.

Status codes:
- 201: stored; result "current" (now effective) or "history" (an equal-or-newer statement is current)
- 422 with the import result: "failed" (row errors, no valid rows); nothing replaced
- 422 {"detail", "code"}: unsupported_statement | ambiguous_statement | invalid_statement
- 415 {"detail", "code": "not_xlsx"}; 413 {"detail", "code": "file_too_large"}
"""

import os
import tempfile
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.models import User
from app.services import import_service
from app.services.import_service import ImportOutcome, ImportResult, StatementRejectedError

router = APIRouter(prefix="/api/imports", tags=["imports"])

CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_session)]

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # broker statements are a few hundred KB at most
CHUNK_BYTES = 64 * 1024
ALLOWED_SUFFIX = ".xlsx"


class RowErrorOut(BaseModel):
    row: int
    field: str
    message: str


class ImportOut(BaseModel):
    attempt_id: int
    result: str  # "current" | "history" | "failed"
    broker: str
    asset_class: str
    statement_date: date
    file_name: str  # base name only
    rows_succeeded: int
    rows_failed: int
    replaced_existing: bool
    errors: list[RowErrorOut]


class _UploadTooLarge(Exception):
    pass


def _problem(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status_code)


def _out(outcome: ImportOutcome) -> ImportOut:
    if outcome.attempt_id is None:  # the service records every attempt; guard the invariant explicitly
        raise RuntimeError("import attempt was not recorded")
    return ImportOut(
        attempt_id=outcome.attempt_id,
        result=outcome.result.value,
        broker=outcome.broker,
        asset_class=outcome.asset_class,
        statement_date=outcome.statement_date,
        file_name=outcome.file_name,
        rows_succeeded=outcome.rows_succeeded,
        rows_failed=outcome.rows_failed,
        replaced_existing=outcome.replaced_existing,
        errors=[RowErrorOut(row=e.row_number, field=e.field, message=e.message) for e in outcome.errors],
    )


def _save_upload(upload: UploadFile, destination) -> None:
    written = 0
    while chunk := upload.file.read(CHUNK_BYTES):
        written += len(chunk)
        if written > MAX_UPLOAD_BYTES:
            raise _UploadTooLarge
        destination.write(chunk)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ImportOut,
    responses={413: {}, 415: {}, 422: {}},
)
def import_statement(
    user: CurrentUser, db: DbSession, file: Annotated[UploadFile, File()]
) -> ImportOut | JSONResponse:
    file_name = import_service.safe_file_name(file.filename)
    if not file_name.lower().endswith(ALLOWED_SUFFIX):
        return _problem(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "not_xlsx", "Upload a broker statement as an .xlsx file"
        )

    handle, temp_path = tempfile.mkstemp(suffix=ALLOWED_SUFFIX)
    try:
        with os.fdopen(handle, "wb") as destination:
            _save_upload(file, destination)
        outcome = import_service.import_statement(db, user.id, temp_path, file_name)
    except _UploadTooLarge:
        return _problem(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large",
                        f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    except StatementRejectedError as rejection:
        return _problem(status.HTTP_422_UNPROCESSABLE_CONTENT, rejection.reason.value, str(rejection))
    finally:
        file.file.close()
        if os.path.exists(temp_path):
            os.remove(temp_path)

    body = _out(outcome)
    if outcome.result == ImportResult.FAILED:
        return JSONResponse(body.model_dump(mode="json"), status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    return body
