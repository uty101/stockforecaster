"""Schema validation that fails closed.

Never coerce, never default a missing field, never accept a string where a float
was declared. A validator that repairs its input is how a forecast built from
three nulls and a coerced empty string reaches the sheet looking whole.

A deliberately small subset of JSON Schema: object, array, string, number,
integer, boolean, null, union types written as a list, enum, required,
additionalProperties. That is everything the response schemas here use, and the
Anthropic structured-output endpoint rejects the constraint keywords we would
otherwise be tempted to add.
"""

from __future__ import annotations

from typing import Any


class SchemaError(Exception):
    """Raised when a model response does not match its declared schema."""


_PYTHON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> Any:
    """Return the value unchanged if it matches, otherwise raise."""
    declared = schema.get("type")
    if declared is None:
        raise SchemaError(f"{path}: schema has no declared type; refusing to guess")

    types = declared if isinstance(declared, list) else [declared]
    if not any(_matches(value, name) for name in types):
        raise SchemaError(
            f"{path}: expected {'|'.join(types)}, got {type(value).__name__} ({value!r:.80})"
        )

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, dict) and "object" in types:
        _validate_object(value, schema, path)
    elif isinstance(value, list) and "array" in types:
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate(item, item_schema, f"{path}[{index}]")

    return value


def _matches(value: Any, type_name: str) -> bool:
    expected = _PYTHON_TYPES.get(type_name)
    if expected is None:
        raise SchemaError(f"unsupported schema type {type_name!r}")
    # bool is a subclass of int in Python; a boolean is never a number here.
    if type_name in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def _validate_object(value: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    missing = [name for name in required if name not in value]
    if missing:
        raise SchemaError(f"{path}: missing required field(s) {', '.join(missing)}")

    if schema.get("additionalProperties") is False:
        extra = [name for name in value if name not in properties]
        if extra:
            raise SchemaError(f"{path}: unexpected field(s) {', '.join(sorted(extra))}")

    for name, sub_schema in properties.items():
        if name in value:
            validate(value[name], sub_schema, f"{path}.{name}")


def obj(
    properties: dict[str, Any],
    required: list[str] | None = None,
    *,
    description: str | None = None,
) -> dict[str, Any]:
    """Build an object schema with the strictness the API requires.

    additionalProperties is always false and every property is required by
    default, because an optional field is a field the model can silently omit,
    and a silently omitted forecast reads downstream as an abstention nobody
    chose.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }
    if description:
        schema["description"] = description
    return schema


def nullable(type_name: str, description: str) -> dict[str, Any]:
    return {"type": [type_name, "null"], "description": description}


def array_of(items: dict[str, Any], description: str) -> dict[str, Any]:
    return {"type": "array", "items": items, "description": description}


def text(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def number(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}
