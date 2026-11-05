from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.media_type import MediaType
from ..types import UNSET, Unset

T = TypeVar("T", bound="MediaItem")


@_attrs_define
class MediaItem:
    """
    Attributes:
        cacheable (bool): False when the item lives outside the cached folders
        id (str): Jellyfin item id
        name (str):
        type_ (MediaType):
        library (str | Unset): Jellyfin library path prefix, e.g. /media2/Videos/tvshows
        season_count (int | Unset):
        year (int | Unset):
    """

    cacheable: bool
    id: str
    name: str
    type_: MediaType
    library: str | Unset = UNSET
    season_count: int | Unset = UNSET
    year: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cacheable = self.cacheable

        id = self.id

        name = self.name

        type_ = self.type_.value

        library = self.library

        season_count = self.season_count

        year = self.year

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cacheable": cacheable,
                "id": id,
                "name": name,
                "type": type_,
            }
        )
        if library is not UNSET:
            field_dict["library"] = library
        if season_count is not UNSET:
            field_dict["seasonCount"] = season_count
        if year is not UNSET:
            field_dict["year"] = year

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cacheable = d.pop("cacheable")

        id = d.pop("id")

        name = d.pop("name")

        type_ = MediaType(d.pop("type"))

        library = d.pop("library", UNSET)

        season_count = d.pop("seasonCount", UNSET)

        year = d.pop("year", UNSET)

        media_item = cls(
            cacheable=cacheable,
            id=id,
            name=name,
            type_=type_,
            library=library,
            season_count=season_count,
            year=year,
        )

        media_item.additional_properties = d
        return media_item

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
