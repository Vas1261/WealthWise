"""User settings (Module 8): the reporting currency, validated against Frankfurter (decision F2)."""

from sqlalchemy.orm import Session

from app.domain.fx import is_currency_code
from app.models import User
from app.repositories import user_repository
from app.services import fx_service


class InvalidCurrencyCode(Exception):
    """Not three upper-case letters."""


class UnsupportedCurrency(Exception):
    """Well-formed, but Frankfurter does not support it."""


class CurrencyValidationUnavailable(Exception):
    """Frankfurter's currency list cannot be obtained, so nothing unverified is stored."""


def set_reporting_currency(db: Session, user: User, currency: object) -> User:
    if not is_currency_code(currency) or not isinstance(currency, str):
        raise InvalidCurrencyCode
    try:
        supported = fx_service.supported_currencies()
    except fx_service.FxUnavailableError:
        raise CurrencyValidationUnavailable from None
    if currency not in supported:
        raise UnsupportedCurrency
    user_repository.set_reporting_currency(db, user, currency)
    db.commit()
    return user
