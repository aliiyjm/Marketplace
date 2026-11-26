"""Errors every repository raises, whatever the database behind it.
They are ValidationError subclasses, so the View shows a friendly message."""
from Interfaces.Validators import ValidationError


class DatabaseUnavailable(ValidationError):
    """The database could not be reached or failed.

    str(error) is the friendly text for the web page; `detail` is the technical
    cause (driver message + a hint) and is only printed in the server console."""

    def __init__(self, message, detail=""):
        super().__init__(message)
        self.detail = detail


class DuplicateKey(ValidationError):
    """A row with that primary/unique key already exists."""


class StaleWrite(ValidationError):
    """The record changed since it was read (optimistic concurrency check)."""


class ProcedureError(ValidationError):
    """A stored procedure refused the operation with SIGNAL SQLSTATE '45000'
    (MySQL) or RAISE EXCEPTION (PostgreSQL). `code` is the short reason the
    procedure gave, e.g. STALE_ORDER or INVALID_TRANSITION."""

    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code
