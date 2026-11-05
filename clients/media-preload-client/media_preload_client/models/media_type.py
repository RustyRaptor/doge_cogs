from enum import StrEnum


class MediaType(StrEnum):
    MOVIE = "movie"
    SERIES = "series"

    def __str__(self) -> str:
        return str(self.value)
