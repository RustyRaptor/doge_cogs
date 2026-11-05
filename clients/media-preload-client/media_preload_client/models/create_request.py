from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CreateRequest")


@_attrs_define
class CreateRequest:
    """
    Attributes:
        item_id (str): Jellyfin item id from /v1/search
        requested_by (str): Who asked
        episodes (list[int] | Unset): Episode numbers within `season`
        jellyfin_user (str | Unset): Jellyfin user name whose watch progress decides "next"
        next_ (int | Unset): Next N unwatched episodes for jellyfinUser
        note (str | Unset):
        season (int | Unset):
    """

    item_id: str
    requested_by: str
    episodes: list[int] | Unset = UNSET
    jellyfin_user: str | Unset = UNSET
    next_: int | Unset = UNSET
    note: str | Unset = UNSET
    season: int | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        item_id = self.item_id

        requested_by = self.requested_by

        episodes: list[int] | Unset = UNSET
        if not isinstance(self.episodes, Unset):
            episodes = self.episodes

        jellyfin_user = self.jellyfin_user

        next_ = self.next_

        note = self.note

        season = self.season

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "itemId": item_id,
                "requestedBy": requested_by,
            }
        )
        if episodes is not UNSET:
            field_dict["episodes"] = episodes
        if jellyfin_user is not UNSET:
            field_dict["jellyfinUser"] = jellyfin_user
        if next_ is not UNSET:
            field_dict["next"] = next_
        if note is not UNSET:
            field_dict["note"] = note
        if season is not UNSET:
            field_dict["season"] = season

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        item_id = d.pop("itemId")

        requested_by = d.pop("requestedBy")

        episodes = cast(list[int], d.pop("episodes", UNSET))

        jellyfin_user = d.pop("jellyfinUser", UNSET)

        next_ = d.pop("next", UNSET)

        note = d.pop("note", UNSET)

        season = d.pop("season", UNSET)

        create_request = cls(
            item_id=item_id,
            requested_by=requested_by,
            episodes=episodes,
            jellyfin_user=jellyfin_user,
            next_=next_,
            note=note,
            season=season,
        )

        create_request.additional_properties = d
        return create_request

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
