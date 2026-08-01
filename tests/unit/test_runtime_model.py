from __future__ import annotations

from agent24.api import OpenAIWhiteBoxAdapter, RuntimeSettings


def test_default_controller_model_is_gpt_5_6_luna(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    runtime = OpenAIWhiteBoxAdapter(
        settings=RuntimeSettings(openai_api_key=None, _env_file=None),
    )

    assert runtime.model_name == "gpt-5.6-luna"
