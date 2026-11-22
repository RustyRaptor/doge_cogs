"""Shared sanitized errors for media service and season requests."""


class PreloadError(Exception):
        """A media service refused or failed a call."""


class SeasonError(ValueError):
        """A season change cannot proceed under the selected retention plan."""
