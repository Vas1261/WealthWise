from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./wealthwise.db"
    # Built frontend (frontend/dist) to serve from the API in one container; empty: API only.
    frontend_dist: str = ""

    # Required by the API (checked at startup), not by Alembic migrations.
    jwt_secret: str = ""

    # Secure cookies are only sent over HTTPS; false is for local HTTP development only.
    cookie_secure: bool = True
    access_cookie_name: str = "ww_access"
    refresh_cookie_name: str = "ww_refresh"
    csrf_cookie_name: str = "ww_csrf"
    csrf_header_name: str = "X-CSRF-Token"

    # Upstox market prices (Module 6). Optional: without a token the API still starts and
    # market data reports NOT_CONFIGURED. SecretStr keeps the token out of repr() and logs.
    upstox_analytics_token: SecretStr = SecretStr("")
    # Spec 4.5: prices are cached for a short interval (5-15 minutes).
    market_price_ttl_minutes: int = Field(default=10, ge=5, le=15)

    # Frankfurter FX rates (Module 8). No API key exists or is needed.
    fx_enabled: bool = True
    frankfurter_base_url: str = "https://api.frankfurter.dev"
    fx_cache_ttl_minutes: int = Field(default=60, ge=1)

    @field_validator("database_url")
    @classmethod
    def _postgres_driver(cls, url: str) -> str:
        """Hosts such as Render hand out postgres:// or postgresql:// URLs; use the installed psycopg 3 driver."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url


def check_app_settings(s: Settings) -> None:
    """Fail fast at API startup on missing or unsafe security configuration."""
    if len(s.jwt_secret) < MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(f"JWT_SECRET must be set to at least {MIN_JWT_SECRET_LENGTH} characters")
    if s.app_env == "production" and not s.cookie_secure:
        raise RuntimeError("COOKIE_SECURE must be true when APP_ENV=production")


settings = Settings()
