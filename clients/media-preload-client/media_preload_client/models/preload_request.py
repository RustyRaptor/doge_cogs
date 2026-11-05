from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.request_status import RequestStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.preload_file import PreloadFile


T = TypeVar("T", bound="PreloadRequest")


@_attrs_define
class PreloadRequest:
    """
    Attributes:
        created_at (datetime.datetime):
        files (list[PreloadFile]):
        id (int):
        item_id (str):
        requested_by (str):
        status (RequestStatus):
        title (str):
        note (str | Unset):
    """

    created_at: datetime.datetime
    files: list[PreloadFile]
    id: int
    item_id: str
    requested_by: str
    status: RequestStatus
    title: str
    note: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        files = []
        for files_item_data in self.files:
            files_item = files_item_data.to_dict()
            files.append(files_item)

        id = self.id

        item_id = self.item_id

        requested_by = self.requested_by

        status = self.status.value

        title = self.title

        note = self.note

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "createdAt": created_at,
                "files": files,
                "id": id,
                "itemId": item_id,
                "requestedBy": requested_by,
                "status": status,
                "title": title,
            }
        )
        if note is not UNSET:
            field_dict["note"] = note

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.preload_file import PreloadFile  # noqa: PLC0415

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("createdAt"))

        files = []
        _files = d.pop("files")
        for files_item_data in _files:
            files_item = PreloadFile.from_dict(files_item_data)

            files.append(files_item)

        id = d.pop("id")

        item_id = d.pop("itemId")

        requested_by = d.pop("requestedBy")

        status = RequestStatus(d.pop("status"))

        title = d.pop("title")

        note = d.pop("note", UNSET)

        preload_request = cls(
            created_at=created_at,
            files=files,
            id=id,
            item_id=item_id,
            requested_by=requested_by,
            status=status,
            title=title,
            note=note,
        )

        preload_request.additional_properties = d
        return preload_request

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
