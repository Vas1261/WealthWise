# WealthWise

A broker-independent personal wealth management platform. You import the holdings statements your brokers
export. WealthWise values them at live market prices in your chosen reporting currency and tracks income,
expenses and budgets alongside the portfolio.

Portfolio project for IU International University of Applied Sciences.

## Features

- **Accounts.** Users register and log in with email and password (bcrypt). Sessions use an HttpOnly JWT
  cookie and a rotating refresh token, and every state-changing request is CSRF-protected.
- **Import Center.** Accepts `.xlsx` statements from Zerodha (equity), Groww (equity) and Groww (mutual-fund
  order history). The file type is recognised by content, not by file name, and row errors are reported per
  row. Every import attempt is kept in a history.
- **Portfolio.** Live prices come from Upstox (NSE, else BSE). It shows per-holding and total P&L, totals per
  currency, and breakdowns by broker and asset class. Mutual funds are shown at cost, labelled as such.
- **Currencies.** Each user picks a reporting currency, converted with live rates from Frankfurter. Rates
  pinned to purchase dates split the total gain into market gain and currency gain.
- **Personal finance.** Income and expense entries, monthly budgets per category, budget vs actual, cash
  position and net worth.
- **Reports.** Monthly finance and portfolio reports as PDF and CSV.
- **Charts.** Allocation, gain/loss per holding, the gain split, and income vs expenses for the last 12
  months. Every chart has a caption and a table of the underlying numbers.

## Tech stack

| Part      | Technology                                                       |
|-----------|------------------------------------------------------------------|
| Backend   | Python 3.11, FastAPI, SQLAlchemy 2, Alembic, Pydantic            |
| Database  | SQLite (local), PostgreSQL 16 (deployment)                       |
| Frontend  | React, TypeScript, Vite, Chart.js                                |
| Reports   | ReportLab (PDF), standard CSV                                    |
| APIs      | Upstox (market prices), Frankfurter (exchange rates)             |
| Testing   | pytest, import-linter, Vitest, Testing Library                   |
| Hosting   | Docker, Render                                                   |

## Architecture

The backend is layered. The layer order `api -> services -> repositories | integrations | adapters -> domain`
is enforced by `.importlinter`, which also runs as part of the test suite.

```
backend/
  app/api/           HTTP route handlers, no calculation logic
  app/services/      Orchestration and sequencing
  app/domain/        Pure business rules (Decimal arithmetic), no DB/HTTP/framework imports
  app/adapters/      One adapter per broker file format (Adapter Pattern)
  app/integrations/  Clients for Upstox and Frankfurter
  app/repositories/  Persistence through SQLAlchemy only
  app/models/        ORM models
  app/core/          Settings and database session
  alembic/           Database migrations
frontend/
  src/               React app: pages, API client, charts
```

## Running locally

Backend (http://127.0.0.1:8000):

```sh
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                 # set JWT_SECRET; see the comments in the file
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --reload-dir app
```

Frontend (http://localhost:5173; it forwards `/api` to the backend):

```sh
cd frontend
npm install
npm run dev
```

## Configuration

All settings live in `backend/.env`. `backend/.env.example` lists them with explanations.

| Variable                 | Purpose                                                               |
|--------------------------|-----------------------------------------------------------------------|
| `DATABASE_URL`           | SQLite or PostgreSQL connection URL                                   |
| `JWT_SECRET`             | Required, at least 32 characters                                      |
| `APP_ENV`                | `development`, `test` or `production`                                 |
| `COOKIE_SECURE`          | `true` for HTTPS; must be `true` in production                        |
| `UPSTOX_ANALYTICS_TOKEN` | Optional; without it prices show as not configured                    |
| `FX_ENABLED`             | `false` disables exchange-rate requests                               |

Never commit `.env`.

## Tests

```sh
cd backend
.venv/bin/pytest                     # full suite, including the architecture rules
TEST_DATABASE_URL=postgresql://user@host/empty_db .venv/bin/pytest   # same suite on PostgreSQL

cd frontend
npm test                             # Vitest
npm run lint && npm run build        # oxlint, type check and production build
```

Some checks are opt-in. Live API checks run only when `RUN_LIVE_UPSTOX=1` or `RUN_LIVE_FRANKFURTER=1` is
set. Performance timings run with `RUN_PERFORMANCE=1 .venv/bin/pytest -m performance -s
tests/test_performance.py`.

## Docker and deployment

A single image (`Dockerfile`) builds the frontend and serves it from FastAPI, so the app runs on one origin.
On start it applies migrations and runs as a non-root user.

```sh
docker compose up --build            # app + PostgreSQL on http://localhost:8000
docker compose down                  # add -v to delete the local database
```

`render.yaml` deploys the same image to Render, together with a PostgreSQL database in Frankfurt. Render
generates `JWT_SECRET`, `UPSTOX_ANALYTICS_TOKEN` is entered in the Render dashboard, and no secret is
stored in the repository. On the free plan the service sleeps after 15 minutes without traffic, so the
first request afterwards can take up to a minute.

## Key design decisions

- **Holdings snapshots, not trade reconstruction.** Equity positions come from each broker's holdings
  statement. Groww mutual funds are the one exception: Groww exports only an order history, so purchases
  and redemptions are aggregated into units held and an average NAV.
- **Snapshot identity.** A snapshot is identified by user, broker, asset class and statement date.
  Re-importing the same statement replaces it. A newer statement becomes current. An older one is kept
  as history and never overrides a newer one.
- **No invented numbers.** Every price and exchange rate carries a status (fresh, stale, unavailable,
  not supported). A missing price leaves the value empty, never zero or cost. Totals are kept per currency
  and are converted only when every rate is available, never partially.
- **Exact money.** All calculations use `Decimal`, and the API sends amounts as strings. The frontend
  formats them but never calculates. A test enforces this, and `plot()` in `chartData.ts` is the one
  documented exception, used only to position chart marks.
- **Pinned exchange rates.** Each holding records the dates its cost was paid on. Historical rates for
  those dates are fetched once and stored permanently. Currency gain is the cost at today's rate minus
  the cost at the pinned rates.
- **Security.** Refresh tokens are stored only as SHA-256 hashes, and logout takes effect immediately. CSV
  exports are protected against formula injection. API tokens are never logged or returned.
- **Health check.** `GET /health` reports `ok` only when the database answers and is at the newest
  migration.

## Limitations and future scope

- Indian brokers and exchanges only. Other markets need further broker adapters and a global price
  provider.
- Mutual fund NAVs are not priced live.
- CSV statement import.
- Portfolio value over time (only the latest price is stored).
- On Render's free plan, the database expires 30 days after creation.

## Data and privacy

Broker statements contain personal data, so `.xlsx`/`.csv` files, `sample_data/`, databases and `.env`
are git-ignored and excluded from the Docker image. Tests that need the private sample files skip when
those files are absent. All other tests generate synthetic files, so a fresh clone runs the full suite.
