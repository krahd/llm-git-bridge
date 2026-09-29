"""Harness domain exceptions."""


class HarnessError(RuntimeError):
    """Base class for harness failures."""


class ValidationError(HarnessError):
    """Input or transition is invalid."""


class NotFound(HarnessError):
    """Requested durable object does not exist."""


class Conflict(HarnessError):
    """A compare-and-swap, lease, or handoff precondition failed."""
