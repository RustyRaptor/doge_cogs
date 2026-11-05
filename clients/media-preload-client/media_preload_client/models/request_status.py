from enum import StrEnum


class RequestStatus(StrEnum):
    CANCELED = "canceled"
    DONE = "done"
    FAILED = "failed"
    FETCHING = "fetching"
    PARTIAL = "partial"
    QUEUED = "queued"

    def __str__(self) -> str:
        return str(self.value)
