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
  app/integrations/  Clients for external APIs (Twelve Data), called only by services
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
    A sheet whose title is missing, invalid, or duplicated does not qualify; non-qualifying
    sheets never affect another sheet. Two qualifying sheets of the selected kind are
    rejected as ambiguous, and a workbook with no qualifying sheet is rejected.
  - On `Combined`, a row's `Instrument Type` must be `-` (equity); any other value,
    including blank, is reported as an unsupported-instrument-type row error, never
    imported as equity. Instruments are not classified by ISIN prefix (ETFs carry `INF`
    ISINs). A `Combined` sheet without an `Instrument Type` column does not qualify, so
    the `Equity` fallback applies. The `Equity` sheet has no `Instrument Type` column.
  - Other columns (discrepant/long-term/pledged quantities, `Sector`, `Previous Closing
    Price`, P&L columns) are not read.
- **Market data (Module 6, decisions M1-M14).** `services/market_service.py` resolves ISINs to
  Twelve Data listings and serves cached live prices; it never invents a price and never uses a
  broker file's closing price. Market data is public, so both caches are global (no user link).
  - Symbol lookup: Twelve Data `/symbol_search` by ISIN first, then by the broker's own ticker
    (Zerodha); never by name. Only Indian (INR) NSE/BSE listings qualify, NSE preferred; two
    distinct symbols on the deciding exchange are `ambiguous`, not guessed. Resolved mappings are
    permanent; `not_found`/`ambiguous` are retried after 7 days; transient failures are not cached.
  - Prices: `/price` per listing, cached in `market_prices`. A price younger than
    `MARKET_PRICE_TTL_MINUTES` (5-15, default 10) is served without a request; forced refresh
    has a 60-second floor; a failed refresh serves the last price as `stale` with its timestamp
    (no age limit), otherwise `unavailable`. Requests are capped per rolling minute
    (`TWELVE_DATA_CREDITS_PER_MINUTE`, default 8 = free plan); excess requests are deferred.
  - Every result has a status (`fresh`, `stale`, `unavailable`, `not_supported`) and, when not
    fresh, an issue code; Twelve Data messages, URLs and headers never leave the client.
    Mutual funds are `not_supported` (no Twelve Data call).
  - `TWELVE_DATA_API_KEY` lives only in `backend/.env`; it is optional (prices then report
    `not_configured`), sent only in the `Authorization` header, and never logged or returned.
    Tests run offline with the key blanked; the live check is opt-in:
    `RUN_LIVE_TWELVE_DATA=1 TWELVE_DATA_LIVE_SYMBOL=<ticker> TWELVE_DATA_LIVE_ISIN=<isin>
    .venv/bin/pytest -m live_twelve_data -s tests/test_twelve_data_live.py`
- **Import persistence and Portfolio Engine (Module 7).**
  - `services/import_service.import_statement(db, user_id, file, name)` picks the adapter by
    content (`can_parse`), parses, and stores an `ImportBatch` + `Holding` rows for the
    authenticated user. Snapshot identity is user + broker + asset class + statement date: the
    same identity is replaced; a newer statement becomes current; an older one is kept as history
    but never current; an import with row errors and no valid rows is not stored at all. A valid
    but empty statement (e.g. every fund redeemed) is a real, current, empty snapshot. Only the base
    file name is stored.
  - Import history is separate from snapshots: `import_attempts` records every attempt with its
    result at import time - `current`, `history` (older than the current statement), `failed` (row
    errors, no valid rows; never a snapshot) or `rejected` (unsupported, ambiguous or invalid file) -
    plus file name, broker/asset class/statement date where known, row counts and whether it
    replaced the same snapshot. `import_batches` holds only successful snapshots; a replaced
    snapshot's attempt keeps its history row (its `batch_id` becomes NULL).
  - `POST /api/imports` (session auth + CSRF, multipart field `file`, one `.xlsx` up to 5 MB): 201
    with the result for stored imports; 422 with the result and row errors for a failed import;
    422 `{detail, code}` for `unsupported_statement` / `ambiguous_statement` / `invalid_statement`;
    415 `not_xlsx`; 413 `file_too_large`. The upload is streamed to a private temporary file that is
    always deleted. Responses never include paths, holdings or cell values.
  - Effective holdings: the user's latest snapshot per (broker, asset class), user-filtered in both
    the snapshot selection and the result. Positions stay per broker even for the same ISIN.
  - Valuation (`domain/portfolio.py`, pure Decimal, no internal rounding):
    `invested = quantity x average_buy_price`; a price is usable only if Module 6 says fresh/stale,
    a price exists and its currency is the holding's; then `current = quantity x price`,
    `pnl = current - invested`, `pnl% = pnl / invested x 100` (none when invested is 0). Otherwise
    current value and P&L stay empty, never zero or cost. Mutual funds are never priced; they show
    a labelled `cost_floor_value` (their cost), never part of current value. Stale prices count but
    flag the totals and expose the oldest price time.
  - Totals are per currency (never summed across currencies): invested, priced invested, current,
    P&L (against priced cost only), unpriced cost, fund cost floor, status counts, oldest price.
    `reporting_totals` is filled only when every holding is in the user's reporting currency;
    otherwise `fx_required` (Module 8). Breakdowns by asset class and broker.
  - `holdings` enforce `quantity > 0` and `average_buy_price >= 0` (migration 0004); invalid stored
    data yields a controlled 409, before any market request.
  - API: `GET /api/portfolio`, `POST /api/portfolio/refresh` (CSRF; forces a price refresh within
    Module 6's 60 s floor). User from the session only. One Market Service call per request, equities
    only. Numbers are strings: money 2 dp, P&L % 2 dp, prices 4 dp (half-even), quantities as stored.
- **Currency service (Module 8, decisions F1-F8).** `services/fx_service.py` gets live rates from
  Frankfurter v2 (`GET /v2/rate/{base}/{quote}`, no API key) through `integrations/frankfurter.py`
  (10 s timeout, rates parsed straight to Decimal, fixed error messages, upstream text never leaked).
  - Same-currency conversion is exactly `Decimal("1")` with no request. Each (base, quote) pair is
    cached in the process for `FX_CACHE_TTL_MINUTES` (60) and requested in the needed direction (no
    locally derived inverses). A failed refresh serves the last good rate as `stale` (no age limit
    within the process); with nothing cached the rate is `unavailable`. `FX_ENABLED=false` makes no
    requests. The cache is per process and lost on restart (no table, no migration).
  - Only top-level portfolio totals are converted into the user's reporting currency (holdings and
    broker/asset-class breakdowns stay native). `reporting_totals` adds `fx_rates` (rate, date,
    status, issue) and `fx_stale`; if any needed rate is unusable, `reporting_totals` is null and
    `fx_unavailable` lists the currencies, never a partial total. Converted P&L uses today's rate for
    cost and value (spec 2.7; no separate FX gain/loss). The fund cost floor is converted separately
    and never joins current value; FX never fills a missing or mismatched market price.
    `POST /api/portfolio/refresh` does not force FX.
  - `PATCH /api/auth/me {"reporting_currency": "EUR"}` (session + CSRF) changes only the user's
    reporting currency: three upper-case letters, supported by Frankfurter (`/v2/currencies`, cached
    24 h); 422 `invalid_currency` / `unsupported_currency`, or 503 `currency_validation_unavailable`
    when Frankfurter cannot be reached (nothing unverified is stored). Default stays INR.
  - Tests run offline (`FX_ENABLED=false`); the live check is opt-in:
    `RUN_LIVE_FRANKFURTER=1 .venv/bin/pytest -m live_frankfurter -s tests/test_frankfurter_live.py`
- **Personal Finance (Module 9, decisions P1-P15).** `domain/finance.py` (pure Decimal rules),
  `repositories/finance_repository.py`, `services/finance_service.py`, `api/finance.py`; tables
  `finance_entries` and `budgets` (migration 0006). Backend only; the finance UI is Module 10.
  - Entries: kind `income`/`expense`, category, amount, date (`YYYY-MM-DD`, 1900-9999, future allowed),
    optional description (<= 255). Amounts are JSON strings, > 0, at most 2 decimals (up to the column's
    18 whole digits); no floats, signs, exponents, NaN, whitespace or rounding. Money is stored exactly
    (`ExactMoney` in `models/finance_entry.py`): `NUMERIC(20, 2)` on PostgreSQL, canonical decimal text
    on SQLite (whose NUMERIC columns hold floats), always read back as `Decimal`; the CHECK compares
    `CAST(amount AS NUMERIC) > 0` so it works on both. Categories are trimmed with internal whitespace
    collapsed and matched exactly (case-sensitive, 1-50 characters, no category table).
  - Currency: every entry and budget takes the user's reporting currency when it is created and keeps
    it; the client never sends one and PUT never changes it, so changing the reporting currency never
    reinterprets existing rows.
  - Budgets: one per category per specific year-month (`YYYY-MM`), amount > 0, expenses only. The summary
    compares each budget with that month's expenses in the same category and currency (`actual`,
    `remaining`, `utilization_pct`, `over_budget`); other expenses are listed as `unbudgeted`.
  - Cash position = income - expenses over all entries, per currency. Net worth = priced portfolio
    current value (already in the reporting currency, Module 8) + cash converted at today's rate.
    Unpriced holdings are left out (`complete=false`, `unpriced_holdings`); the mutual-fund
    `cost_floor_value` is shown separately and never added. Any unusable FX rate gives a null total
    (never a partial one) and lists the currency in `fx_unavailable`.
  - API (`/api/finance`, session auth; POST/PUT/DELETE need CSRF): `POST/GET /entries` (filters `start`,
    `end`, `kind`, `category`; newest first; no pagination), `GET/PUT/DELETE /entries/{id}` (PUT replaces
    the whole entry), `POST /budgets`, `GET /budgets?month=`, `GET /budgets/summary?month=`,
    `PUT /budgets/{id}` (amount only), `DELETE /budgets/{id}`, `GET /net-worth`. Another user's id is a
    404. Money is strings at 2 dp. Errors are `{detail, code}` with fixed messages; schema errors on
    finance routes omit FastAPI's echoed input. Finance values are never logged.
  - The shared number formatter lives in `api/formatting.py` (moved unchanged from `api/portfolio.py`).
  - Live demo (opt-in): `RUN_LIVE_FRANKFURTER=1 [RUN_LIVE_TWELVE_DATA=1 TWELVE_DATA_LIVE_ISIN=<isin>
    TWELVE_DATA_LIVE_SYMBOL=<ticker>] .venv/bin/pytest -m live_frankfurter -s tests/test_finance_live.py`
- **Sample broker files** live in the git-ignored `sample_data/` folder as private reference
  fixtures. Tests that use them skip when they are absent; all other adapter tests generate
  small synthetic `.xlsx` files, so a fresh clone has a fully runnable test suite.
- **Never commit** secrets, `.env`, SQLite databases, virtual environments,
  `node_modules`, or personal/sample broker files (`*.xlsx`, `*.csv` are ignored).
