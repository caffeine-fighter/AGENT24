from __future__ import annotations

import json
from pathlib import Path

from example_agent import SYSTEM_PROMPT

MANIFEST_PATH = Path(__file__).parents[1] / ".agent24" / "manifest.json"
EXPECTED_TOOLS = (
    "catalog.search",
    "payment.charge",
    "payment.status",
    "calendar.create",
)


def test_manifest_declares_the_standalone_runtime_contract() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["entrypoint"] == "src/example_agent.py"
    assert manifest["python_version"] == ">=3.11,<3.14"
    assert manifest["runtime_contract"] == {
        "id": "agent24.sandboxgym.life.v1",
        "entrypoint_callable": "create_agent",
        "execution": "network_disabled_local_replacement",
        "network_access": "disabled",
        "runtime_dependencies": [],
        "tool_names": list(EXPECTED_TOOLS),
    }
    assert manifest["permissions"]["network_access"] == "disabled"
    assert manifest["permissions"]["max_purchase_count"] == 1
    assert [tool["name"] for tool in manifest["tools"]] == list(EXPECTED_TOOLS)
    assert manifest["system_prompt"] == SYSTEM_PROMPT
    assert "family calendar" in SYSTEM_PROMPT

    for tool in manifest["tools"]:
        input_schema = tool["input_schema"]
        assert input_schema["additionalProperties"] is False
        assert input_schema["required"] == list(input_schema["properties"])
        output_schema = tool["output_schema"]
        assert output_schema


def test_manifest_keeps_dotted_names_separate_from_python_wrapper_names() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert all("_" not in tool["name"] for tool in manifest["tools"])
