"""JSON Schema validation without external reference retrieval or payload disclosure."""

from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker, SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable


def validate_tool_schema(schema: dict, value, *, label: str) -> None:
    checker = FormatChecker()
    checker.checks("path")(
        lambda item: not isinstance(item, str) or bool(item.strip()) and "\x00" not in item
    )
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, registry=Registry(), format_checker=checker)
        error = next(validator.iter_errors(value), None)
    except (SchemaError, Unresolvable, RecursionError):
        raise HTTPException(422, f"Invalid {label} schema or unresolved local reference") from None
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        raise HTTPException(422, f"{label} at {path} failed {error.validator} validation")
