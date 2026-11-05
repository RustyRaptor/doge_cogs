from enum import StrEnum


class FileStatus(StrEnum):
    CACHED = "cached"
    CANCELED = "canceled"
    FAILED = "failed"
    FETCHING = "fetching"
    NOT_CACHEABLE = "not_cacheable"
    QUEUED = "queued"

    def __str__(self) -> str:
        return str(self.value)
