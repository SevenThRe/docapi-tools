#!/usr/bin/env python3
"""Structural validation for deterministic API config artifacts."""

from __future__ import annotations

from typing import Any


ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
REQUIRED_TOP_LEVEL_FIELDS = (
    ("cover", "API cover information"),
    ("cover.api_name", "API display name"),
    ("cover.api_id", "API identifier"),
    ("api_info", "API metadata"),
    ("api_info.method", "HTTP method"),
    ("api_info.url", "API URL"),
    ("request_params", "Request parameter list"),
    ("response_params", "Response parameter list"),
)
REQUIRED_PARAM_FIELDS = ("item_name", "required", "data_type", "data_length", "description", "example", "depth")


def validate_api_config(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for path, description in REQUIRED_TOP_LEVEL_FIELDS:
        value = _get_nested(config, path)
        if value is None:
            errors.append(f"Missing required field '{path}' ({description})")
        elif isinstance(value, str) and not value.strip():
            errors.append(f"Field '{path}' must not be empty ({description})")

    method = _get_nested(config, "api_info.method")
    if isinstance(method, str) and method and method.upper() not in ALLOWED_METHODS:
        errors.append(
            f"api_info.method must be one of {', '.join(sorted(ALLOWED_METHODS))}; got '{method}'"
        )

    for field_name in ("request_params", "response_params"):
        params = config.get(field_name)
        if not isinstance(params, list):
            errors.append(f"'{field_name}' must be an array")
            continue

        for index, param in enumerate(params):
            if not isinstance(param, dict):
                errors.append(f"{field_name}[{index}] must be an object")
                continue
            for required_field in REQUIRED_PARAM_FIELDS:
                value = param.get(required_field)
                if value is None:
                    errors.append(f"{field_name}[{index}].{required_field} is required")
                elif isinstance(value, str) and required_field != "example" and not value.strip():
                    errors.append(f"{field_name}[{index}].{required_field} must not be empty")
            depth = param.get("depth")
            if depth is not None and not isinstance(depth, int):
                errors.append(f"{field_name}[{index}].depth must be an integer")

    for optional_field in ("overview", "sequence", "processing_detail"):
        if optional_field not in config:
            warnings.append(f"Optional section '{optional_field}' is missing")

    return errors, warnings


def validate_and_report(config: dict[str, Any]) -> bool:
    errors, warnings = validate_api_config(config)

    if warnings:
        print(f"[warn] warnings: {len(warnings)}")
        for warning in warnings:
            print(f"  [warn] {warning}")

    if errors:
        print(f"[error] errors: {len(errors)}")
        for error in errors:
            print(f"  [error] {error}")
        return False

    print("[ok] api config validation: OK")
    return True


def _get_nested(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current
