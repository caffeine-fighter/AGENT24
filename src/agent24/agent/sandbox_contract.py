"""Canonical JSON contract for the bounded SandboxGym demo surface.

The manifest loader uses this module as a data-only compatibility gate.  It is
deliberately independent from the runtime gym and from repository code, so a
schema mismatch fails before an entrypoint can be considered for execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SANDBOX_GYM_CONTRACT_ID = "agent24.sandboxgym.life.v1"
SANDBOX_GYM_PYTHON_VERSION = ">=3.11,<3.14"
SANDBOX_GYM_TOOL_NAMES: tuple[str, ...] = (
    "catalog.search",
    "payment.charge",
    "payment.status",
    "calendar.create",
)
SANDBOX_GYM_PERMISSIONS: dict[str, Any] = {
    "max_spend_krw": 50_000,
    "max_purchase_count": 1,
    "network_access": "disabled",
    "allowed_side_effect_tools": ["payment.charge", "calendar.create"],
}
SANDBOX_GYM_TOOL_FLAGS: dict[str, dict[str, Any]] = {
    "catalog.search": {
        "side_effect": False,
        "irreversible": False,
        "category_hint": None,
    },
    "payment.charge": {
        "side_effect": True,
        "irreversible": True,
        "category_hint": "privileged_sink",
    },
    "payment.status": {
        "side_effect": False,
        "irreversible": False,
        "category_hint": None,
    },
    "calendar.create": {
        "side_effect": True,
        "irreversible": False,
        "category_hint": None,
    },
}

SANDBOX_GYM_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "catalog.search": {
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_price_krw": {"type": ["integer", "null"], "minimum": 0},
            },
            "required": ["query", "max_price_krw"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "ok": {"const": True},
                "query": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string"},
                            "title": {"type": "string"},
                            "price_krw": {"type": "integer", "minimum": 0},
                            "available": {"type": "boolean"},
                        },
                        "required": ["product_id", "title", "price_krw", "available"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["ok", "query", "items"],
            "additionalProperties": False,
        },
    },
    "payment.charge": {
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
                "idempotency_key": {"type": ["string", "null"]},
            },
            "required": ["product_id", "quantity", "idempotency_key"],
            "additionalProperties": False,
        },
        "output_schema": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "ok": {"const": True},
                        "status": {"const": "committed"},
                        "payment_id": {"type": "string"},
                        "order_id": {"type": "string"},
                        "charged_krw": {"type": "integer", "minimum": 0},
                        "idempotent_replay": {"type": "boolean"},
                    },
                    "required": [
                        "ok",
                        "status",
                        "payment_id",
                        "order_id",
                        "charged_krw",
                        "idempotent_replay",
                    ],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "ok": {"const": False},
                        "status": {"const": "unknown"},
                        "error": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                            },
                            "required": ["code", "message"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["ok", "status", "error"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "ok": {"const": False},
                        "status": {"const": "rejected"},
                        "error": {"type": "string"},
                    },
                    "required": ["ok", "status", "error"],
                    "additionalProperties": False,
                },
            ]
        },
    },
    "payment.status": {
        "input_schema": {
            "type": "object",
            "properties": {
                "payment_id": {"type": ["string", "null"]},
                "idempotency_key": {"type": ["string", "null"]},
            },
            "required": ["payment_id", "idempotency_key"],
            "additionalProperties": False,
        },
        "output_schema": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "found": {"const": True},
                        "status": {"const": "committed"},
                        "payment_id": {"type": "string"},
                        "order_id": {"type": "string"},
                        "charged_krw": {"type": "integer", "minimum": 0},
                    },
                    "required": [
                        "found",
                        "status",
                        "payment_id",
                        "order_id",
                        "charged_krw",
                    ],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "found": {"const": False},
                        "status": {"const": "absent"},
                    },
                    "required": ["found", "status"],
                    "additionalProperties": False,
                },
            ]
        },
    },
    "calendar.create": {
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start_at": {"type": "string"},
                "end_at": {"type": ["string", "null"]},
                "timezone": {"type": "string"},
                "idempotency_key": {"type": ["string", "null"]},
            },
            "required": ["title", "start_at", "end_at", "timezone", "idempotency_key"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "ok": {"const": True},
                "status": {"const": "created"},
                "event_id": {"type": "string"},
                "idempotent_replay": {"type": "boolean"},
            },
            "required": ["ok", "status", "event_id", "idempotent_replay"],
            "additionalProperties": False,
        },
    },
}


class SandboxContractError(ValueError):
    """A wire payload does not satisfy the reviewed SandboxGym contract."""


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "integer":
        return type(value) is int
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return type(value) is bool
    if expected == "null":
        return value is None
    return False


def _validate_json_schema(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    one_of = schema.get("oneOf")
    if one_of is not None:
        matches = 0
        for option in one_of:
            try:
                _validate_json_schema(value, option, path=path)
            except SandboxContractError:
                continue
            matches += 1
        if matches != 1:
            raise SandboxContractError(f"{path} does not match exactly one declared schema")
        return

    if "const" in schema:
        expected = schema["const"]
        if type(value) is not type(expected) or value != expected:
            raise SandboxContractError(f"{path} does not match its declared constant")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_schema_type_matches(value, item) for item in expected_types):
            raise SandboxContractError(f"{path} has an invalid JSON type")

    if "minimum" in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema["minimum"]:
            raise SandboxContractError(f"{path} is below its declared minimum")

    if expected_type == "object" or (isinstance(expected_type, list) and "object" in expected_type):
        if not isinstance(value, Mapping):
            return
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise SandboxContractError(f"{path} is missing required fields")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unexpected = set(value) - set(properties)
            if unexpected:
                raise SandboxContractError(f"{path} contains undeclared fields")
        for key, child_schema in properties.items():
            if key in value:
                _validate_json_schema(value[key], child_schema, path=f"{path}.{key}")

    if expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                _validate_json_schema(item, item_schema, path=f"{path}[{index}]")


def validate_tool_arguments(tool: str, arguments: Any) -> dict[str, Any]:
    """Validate one host-bound tool call against the canonical input schema."""

    try:
        schema = SANDBOX_GYM_TOOL_SCHEMAS[tool]["input_schema"]
    except KeyError as error:
        raise SandboxContractError(f"tool {tool} is not in the reviewed contract") from error
    _validate_json_schema(arguments, schema, path=f"{tool}.arguments")
    return dict(arguments)


def validate_tool_result(tool: str, result: Any) -> dict[str, Any]:
    """Validate one host-produced tool result before it crosses the wire."""

    try:
        schema = SANDBOX_GYM_TOOL_SCHEMAS[tool]["output_schema"]
    except KeyError as error:
        raise SandboxContractError(f"tool {tool} is not in the reviewed contract") from error
    _validate_json_schema(result, schema, path=f"{tool}.result")
    return dict(result)


def validate_sandbox_manifest(
    tools: Any,
    runtime_contract: Any,
    *,
    permissions: Any,
    python_version: Any,
) -> None:
    """Raise ``ValueError`` unless a manifest is exactly SandboxGym-compatible."""

    if python_version != SANDBOX_GYM_PYTHON_VERSION:
        raise ValueError("SandboxGym manifests must declare the canonical Python version")
    if not isinstance(runtime_contract, Mapping):
        raise ValueError("runtime_contract must be an object")
    expected_runtime = {
        "id": SANDBOX_GYM_CONTRACT_ID,
        "entrypoint_callable": "create_agent",
        "execution": "network_disabled_local_replacement",
        "network_access": "disabled",
        "runtime_dependencies": [],
        "tool_names": list(SANDBOX_GYM_TOOL_NAMES),
    }
    if dict(runtime_contract) != expected_runtime:
        raise ValueError("runtime_contract does not match SandboxGym")
    if not isinstance(permissions, Mapping) or dict(permissions) != SANDBOX_GYM_PERMISSIONS:
        raise ValueError("permissions do not match SandboxGym")
    if not isinstance(tools, list):
        raise ValueError("SandboxGym manifests must declare a tool list")
    if [item.get("name") for item in tools if isinstance(item, Mapping)] != list(
        SANDBOX_GYM_TOOL_NAMES
    ):
        raise ValueError("SandboxGym tool names/order do not match the canonical contract")
    if len(tools) != len(SANDBOX_GYM_TOOL_NAMES):
        raise ValueError("SandboxGym manifests must declare exactly four tools")
    for item, name in zip(tools, SANDBOX_GYM_TOOL_NAMES, strict=True):
        if not isinstance(item, Mapping):
            raise ValueError(f"SandboxGym tool {name} must be an object")
        actual_flags = {
            "side_effect": item.get("side_effect", False),
            "irreversible": item.get("irreversible", False),
            "category_hint": item.get("category_hint"),
        }
        if actual_flags != SANDBOX_GYM_TOOL_FLAGS[name]:
            raise ValueError(f"SandboxGym safety metadata mismatch for {name}")
        actual = {
            "input_schema": item.get("input_schema"),
            "output_schema": item.get("output_schema"),
        }
        if actual != SANDBOX_GYM_TOOL_SCHEMAS[name]:
            raise ValueError(f"SandboxGym schema mismatch for {name}")


__all__ = [
    "SANDBOX_GYM_CONTRACT_ID",
    "SANDBOX_GYM_PERMISSIONS",
    "SANDBOX_GYM_PYTHON_VERSION",
    "SANDBOX_GYM_TOOL_FLAGS",
    "SANDBOX_GYM_TOOL_NAMES",
    "SANDBOX_GYM_TOOL_SCHEMAS",
    "SandboxContractError",
    "validate_tool_arguments",
    "validate_tool_result",
    "validate_sandbox_manifest",
]
