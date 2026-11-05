from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.file_status import FileStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="PreloadFile")


@_attrs_define
class PreloadFile:
    """
    Attributes:
        id (int):
        path (str): Path as seen by Jellyfin and webdav, e.g. /media2/Videos/tvshows/...
        size_bytes (int):
        status (FileStatus):
        title (str): e.g. Dark Matter S02E08
        error (str | Unset):
        fetched_bytes (int | Unset):
        updated_at (datetime.datetime | Unset):
    """

    id: int
    path: str
    size_bytes: int
    status: FileStatus
    title: str
    error: str | Unset = UNSET
    fetched_bytes: int | Unset = UNSET
    updated_at: datetime.datetime | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        path = self.path

        size_bytes = self.size_bytes

        status = self.status.value

        title = self.title

        error = self.error

        fetched_bytes = self.fetched_bytes

        updated_at: str | Unset = UNSET
        if not isinstance(self.updated_at, Unset):
            updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "path": path,
                "sizeBytes": size_bytes,
                "status": status,
                "title": title,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error
        if fetched_bytes is not UNSET:
            field_dict["fetchedBytes"] = fetched_bytes
        if updated_at is not UNSET:
            field_dict["updatedAt"] = updated_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        path = d.pop("path")

        size_bytes = d.pop("sizeBytes")

        status = FileStatus(d.pop("status"))

        title = d.pop("title")

        error = d.pop("error", UNSET)

        fetched_bytes = d.pop("fetchedBytes", UNSET)

        _updated_at = d.pop("updatedAt", UNSET)
        updated_at: datetime.datetime | Unset
        if isinstance(_updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = datetime.datetime.fromisoformat(_updated_at)

        preload_file = cls(
            id=id,
            path=path,
            size_bytes=size_bytes,
            status=status,
            title=title,
            error=error,
            fetched_bytes=fetched_bytes,
            updated_at=updated_at,
        )

        preload_file.additional_properties = d
        return preload_file

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
