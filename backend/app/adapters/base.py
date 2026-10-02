from abc import ABC, abstractmethod

from app.domain.holdings import ParseResult


class StatementFormatError(Exception):
    """The file cannot be read as this broker's statement at all (file-level, not row-level)."""


class BrokerAdapter(ABC):
    """Spec 2.4 interface.

    parse() differs from the spec's `parse(file_path) -> list[NormalizedHolding]` in two ways, both
    needed by spec 4.2: it takes the import_batch_id that every NormalizedHolding carries, and it
    returns a ParseResult so per-row failures and the statement date are reported alongside the rows.
    """

    @abstractmethod
    def can_parse(self, file_path: str) -> bool: ...

    @abstractmethod
    def parse(self, file_path: str, import_batch_id: str) -> ParseResult: ...
