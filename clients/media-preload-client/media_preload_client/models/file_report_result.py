from enum import StrEnum


class FileReportResult(StrEnum):
    ALREADY_CACHED = "already_cached"
    CACHED = "cached"
    FAILED = "failed"

    def __str__(self) -> str:
        return str(self.value)
