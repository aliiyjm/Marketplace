"""Errors every repository raises, whatever the database behind it.
They are ValidationError subclasses, so the View shows a friendly message."""
from Interfaces.Validators import ValidationError


class DatabaseUnavailable(ValidationError):
    """The database could not be reached or failed (details only in the server log)."""


class DuplicateKey(ValidationError):
    """A row with that primary/unique key already exists."""


class StaleWrite(ValidationError):
    """The record changed since it was read (optimistic concurrency check)."""
