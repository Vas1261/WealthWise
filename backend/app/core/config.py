from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./wealthwise.db"

    # Required by the API (checked at startup), not by Alembic migrations.
    jwt_secret: str = ""

    # Secure cookies are only sent over HTTPS; false is for local HTTP development only.
    cookie_secure: bool = True
    access_cookie_name: str = "ww_access"
    refresh_cookie_name: str = "ww_refresh"
    csrf_cookie_name: str = "ww_csrf"
    csrf_header_name: str = "X-CSRF-Token"

    # Twelve Data market prices (Module 6). Optional: without a key the API still starts and
    # market data reports NOT_CONFIGURED. SecretStr keeps the key out of repr() and logs.
    twelve_data_api_key: SecretStr = SecretStr("")
    # Spec 4.5: prices are cached for a short interval (5-15 minutes).
    market_price_ttl_minutes: int = Field(default=10, ge=5, le=15)
    # Requests allowed per rolling minute, matching the plan's credit limit (free Basic plan: 8).
    twelve_data_credits_per_minute: int = Field(default=8, ge=1)


def check_app_settings(s: Settings) -> None:
    """Fail fast at API startup on missing or unsafe security configuration."""
    if len(s.jwt_secret) < MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(f"JWT_SECRET must be set to at least {MIN_JWT_SECRET_LENGTH} characters")
    if s.app_env == "production" and not s.cookie_secure:
        raise RuntimeError("COOKIE_SECURE must be true when APP_ENV=production")


settings = Settings()
