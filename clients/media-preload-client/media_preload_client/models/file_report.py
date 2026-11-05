from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.file_report_result import FileReportResult
from ..types import UNSET, Unset

T = TypeVar("T", bound="FileReport")


@_attrs_define
class FileReport:
    """
    Attributes:
        result (FileReportResult):
        bytes_ (int | Unset): Bytes read from home for this file
        error (str | Unset):
    """

    result: FileReportResult
    bytes_: int | Unset = UNSET
    error: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = self.result.value

        bytes_ = self.bytes_

        error = self.error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "result": result,
            }
        )
        if bytes_ is not UNSET:
            field_dict["bytes"] = bytes_
        if error is not UNSET:
            field_dict["error"] = error

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        result = FileReportResult(d.pop("result"))

        bytes_ = d.pop("bytes", UNSET)

        error = d.pop("error", UNSET)

        file_report = cls(
            result=result,
            bytes_=bytes_,
            error=error,
        )

        file_report.additional_properties = d
        return file_report

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
