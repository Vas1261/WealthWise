# WealthWise

Broker-independent personal wealth management platform. The authoritative build
specification is `WealthWise_Build_Specification_v2.docx`.

## Layout

```
backend/    FastAPI + SQLAlchemy + Alembic (Python 3.11)
  app/api/           Presentation: route handlers, no calculation logic
  app/services/      Service: orchestration/sequencing only
  app/domain/        Business/Domain: pure functions, no DB/HTTP/framework imports
  app/adapters/      Broker file adapters (Adapter Pattern): read exports into NormalizedHolding, no DB access
  app/repositories/  Repository: persistence via SQLAlchemy only (no raw SQL)
  app/models/        ORM models (User, ImportBatch, Holding)
  app/core/          Settings and database session
  alembic/           Migrations
frontend/   React + Vite + TypeScript
```

## Running locally

```sh
# Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then set JWT_SECRET (see the comment in the file)
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload        # http://127.0.0.1:8000/health
.venv/bin/pytest

# Frontend (proxies /api and /health to the backend)
cd frontend
npm install
npm run dev                                    # http://localhost:5173
```

## Recorded decisions (beyond the spec)

- **Snapshot identity** is user + broker + asset class + statement date, enforced by the
  `uq_import_batches_snapshot_identity` unique constraint. Groww equity
  and Groww mutual funds are separate snapshots. Re-importing the same identity
  replaces it; a newer statement date replaces the older snapshot for the same
  user + broker + asset class; an older statement uploaded after a newer one must
  not overwrite the newer snapshot.
- **Frontend** is React + Vite + TypeScript.
- **Authentication** (Module 2):
  - Access JWT (15 min; claims `sub`, `sid`, `type`, `iat`, `exp`) and an opaque refresh
    token (7 days) in HttpOnly, SameSite=Lax cookies. `COOKIE_SECURE` defaults to true and
    the API refuses to start with `APP_ENV=production` and `COOKIE_SECURE=false`.
  - Each login creates a row in `sessions`, which stores only the SHA-256 hash of the
    current refresh token. Refresh rotates it in place; presenting an already-rotated
    token revokes the session. Every authenticated request checks the session is active,
    so logout takes effect immediately. Logout revokes only the current session.
  - CSRF: double-submit token (readable `ww_csrf` cookie echoed in `X-CSRF-Token`) on
    every state-changing request, including register and login (`GET /api/auth/csrf`
    issues the cookie).
  - Passwords: bcrypt, 8–72 UTF-8 bytes (rejected, never truncated). Emails are trimmed
    and lowercased. Registering does not log the user in.
- **Broker adapters** implement `BrokerAdapter`. `can_parse(file_path)` matches the spec;
  `parse(file_path, import_batch_id) -> ParseResult` extends the spec's `parse(file_path)`
  so it can stamp the import batch id and report per-row errors and the statement date.
  Financial values are exact `Decimal`s; invalid rows are reported, never coerced to zero.
- **Zerodha equity recognition (D1)**, for the Module 4 adapter. The real export titles its
  sheets "Combined/Equity/Mutual Funds Holdings Statement as on <date>", while spec 3.2/4.2
  quote only "Equity Holdings Statement"; the spec is unchanged and this rule applies it:
  - A sheet qualifies only if it has a `Client ID` label, exactly one title that is
    `Combined Holdings Statement as on YYYY-MM-DD` or `Equity Holdings Statement as on
    YYYY-MM-DD` (whitespace-collapsed, case-sensitive; `Mutual Funds Holdings Statement` is
    never accepted), and, below that title, a header row with exactly matched `Symbol`,
    `ISIN`, `Quantity Available` and `Average Price`. Recognition is by content only, never
    by file name, sheet name, or file date.
  - Sheet priority: the qualifying `Combined` sheet, else the qualifying `Equity` sheet,
    else the workbook is rejected. Only one sheet is read; sheets are never merged.
  - The statement date comes only from the selected sheet's title (strict `YYYY-MM-DD`);
    never from the file name, file dates, `Previous Closing Price`, or transaction dates.
    A missing, invalid, or duplicated title rejects the workbook rather than guessing.
  - On `Combined`, a row's `Instrument Type` must be `-` (equity); any other value,
    including blank, is reported as an unsupported-instrument-type row error, never
    imported as equity. Instruments are not classified by ISIN prefix (ETFs carry `INF`
    ISINs). A `Combined` sheet without an `Instrument Type` column does not qualify, so
    the `Equity` fallback applies. The `Equity` sheet has no `Instrument Type` column.
  - Other columns (discrepant/long-term/pledged quantities, `Sector`, `Previous Closing
    Price`, P&L columns) are not read.
- **Sample broker files** live in the git-ignored `sample_data/` folder as private reference
  fixtures. Tests that use them skip when they are absent; all other adapter tests generate
  small synthetic `.xlsx` files, so a fresh clone has a fully runnable test suite.
- **Never commit** secrets, `.env`, SQLite databases, virtual environments,
  `node_modules`, or personal/sample broker files (`*.xlsx`, `*.csv` are ignored).
