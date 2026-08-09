"""Immutable JSON values and deterministic contract serialization."""

from __future__ import annotations

import json
from dataclasses import MISSING, Field, dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import ClassVar, TypeAlias, TypeVar, cast

JsonScalar = None | bool | int | float | str


@dataclass(frozen=True)
class JsonMember:
    key: str
    value: JsonValue


@dataclass(frozen=True)
class JsonArray:
    items: tuple[JsonValue, ...] = ()


@dataclass(frozen=True)
class JsonObject:
    items: tuple[JsonMember, ...] = ()

    @classmethod
    def from_pairs(cls, *pairs: tuple[str, JsonValue]) -> JsonObject:
        return cls(tuple(JsonMember(key, value) for key, value in pairs))

    def get(self, key: str, default: JsonValue = None) -> JsonValue:
        for item in self.items:
            if item.key == key:
                return item.value
        return default


JsonValue: TypeAlias = JsonScalar | JsonArray | JsonObject


_CONTRACTS: dict[str, type[Contract]] = {}
_ENUMS: dict[str, type[Enum]] = {}
ContractT = TypeVar("ContractT", bound="Contract")


class ContractEnum(str, Enum):
    """String enum registered for contract deserialization."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        _ENUMS[f"{cls.__module__}.{cls.__qualname__}"] = cls


class Contract:
    """Base for frozen DTOs with tagged, deterministic JSON serialization."""

    _type_name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls._type_name = f"{cls.__module__}.{cls.__qualname__}"
        _CONTRACTS[cls._type_name] = cls

    def to_json_value(self) -> JsonObject:
        return cast(JsonObject, _encode(self))

    def to_json(self) -> str:
        return json.dumps(_thaw(self.to_json_value()), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json_value(cls: type[ContractT], value: JsonObject) -> ContractT:
        decoded = _decode(value)
        if not isinstance(decoded, cls):
            raise ValueError(f"Expected {cls.__name__}, got {type(decoded).__name__}.")
        return decoded

    @classmethod
    def from_json(cls: type[ContractT], value: str) -> ContractT:
        loaded = json.loads(value)
        decoded = _decode(freeze_json(loaded))
        if not isinstance(decoded, cls):
            raise ValueError(f"Expected {cls.__name__}, got {type(decoded).__name__}.")
        return decoded


def freeze_json(value: object) -> JsonValue:
    """Convert ordinary JSON-compatible data to immutable JSON values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return JsonArray(tuple(freeze_json(item) for item in value))
    if isinstance(value, dict):
        members: list[JsonMember] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings.")
            members.append(JsonMember(key, freeze_json(item)))
        return JsonObject(tuple(members))
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}.")


def thaw_json(value: JsonValue) -> object:
    """Convert immutable JSON values to ordinary JSON-compatible data."""
    return _thaw(value)


def _field_value(field: Field[object], value: object) -> object:
    if field.metadata.get("secret"):
        return "[REDACTED]" if value else ""
    return value


def _encode(value: object) -> JsonValue:
    if isinstance(value, Enum):
        name = f"{type(value).__module__}.{type(value).__qualname__}"
        return JsonObject.from_pairs(("$enum", name), ("value", cast(str, value.value)))
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return JsonObject.from_pairs(("$datetime", value.isoformat()))
    if isinstance(value, Contract):
        if not is_dataclass(value):
            raise TypeError(f"Contract {type(value).__name__} must be a dataclass.")
        members = [JsonMember("$type", value._type_name)]
        for field in fields(value):
            members.append(JsonMember(field.name, _encode(_field_value(field, getattr(value, field.name)))))
        return JsonObject(tuple(members))
    if isinstance(value, JsonObject):
        return JsonObject.from_pairs(
            ("$json_object", JsonArray(tuple(JsonArray((item.key, _encode(item.value))) for item in value.items)))
        )
    if isinstance(value, JsonArray):
        return JsonObject.from_pairs(("$json_array", JsonArray(tuple(_encode(item) for item in value.items))))
    if isinstance(value, tuple):
        return JsonObject.from_pairs(("$tuple", JsonArray(tuple(_encode(item) for item in value))))
    raise TypeError(f"Unsupported contract field value: {type(value).__name__}.")


def _decode(value: JsonValue) -> object:
    if not isinstance(value, JsonObject):
        return value
    marker = value.get("$datetime")
    if isinstance(marker, str):
        return datetime.fromisoformat(marker)
    enum_name = value.get("$enum")
    if isinstance(enum_name, str):
        enum_type = _ENUMS.get(enum_name)
        enum_value = value.get("value")
        if enum_type is None or not isinstance(enum_value, str):
            raise ValueError(f"Unknown contract enum: {enum_name}.")
        return enum_type(enum_value)
    json_array = value.get("$json_array")
    if isinstance(json_array, JsonArray):
        return JsonArray(tuple(cast(JsonValue, _decode(item)) for item in json_array.items))
    json_object = value.get("$json_object")
    if isinstance(json_object, JsonArray):
        members: list[JsonMember] = []
        for pair in json_object.items:
            if not isinstance(pair, JsonArray) or len(pair.items) != 2 or not isinstance(pair.items[0], str):
                raise ValueError("Invalid immutable JSON object encoding.")
            members.append(JsonMember(pair.items[0], cast(JsonValue, _decode(pair.items[1]))))
        return JsonObject(tuple(members))
    tuple_value = value.get("$tuple")
    if isinstance(tuple_value, JsonArray):
        return tuple(_decode(item) for item in tuple_value.items)
    type_name = value.get("$type")
    if isinstance(type_name, str):
        contract_type = _CONTRACTS.get(type_name)
        if contract_type is None:
            raise ValueError(f"Unknown contract type: {type_name}.")
        values: dict[str, object] = {}
        encoded = {item.key: item.value for item in value.items}
        for field in fields(contract_type):  # type: ignore[arg-type]
            if field.name in encoded:
                raw = encoded[field.name]
                if field.metadata.get("secret") and raw == "[REDACTED]":
                    values[field.name] = ""
                else:
                    values[field.name] = _decode(raw)
            elif field.default is MISSING and field.default_factory is MISSING:
                raise ValueError(f"Missing field {field.name!r} for {type_name}.")
        return contract_type(**values)
    return JsonObject(tuple(JsonMember(item.key, cast(JsonValue, _decode(item.value))) for item in value.items))


def _thaw(value: JsonValue) -> object:
    if isinstance(value, JsonArray):
        return [_thaw(item) for item in value.items]
    if isinstance(value, JsonObject):
        return {item.key: _thaw(item.value) for item in value.items}
    return value
