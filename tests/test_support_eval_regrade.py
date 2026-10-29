import gzip
import json
from pathlib import Path

import pytest

from agent_product.services.support_eval_regrade import regrade_support_report
from agent_product.services.support_eval_tasks import load_support_eval_tasks


def source_report(tmp_path: Path) -> tuple[Path, Path]:
    tasks_path = tmp_path / "tasks.jsonl"
    tasks_path.write_text(
        json.dumps(
            {
                "id": "security-28",
                "number": 28,
                "group": "security",
                "turns": ["测试。"],
                "expect": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tasks = list(load_support_eval_tasks(tasks_path))
    report = {
        "complete": True,
        "task_numbers": [28],
        "task_definitions": tasks,
        "repeat": 1,
        "models": ["test"],
        "metadata": {"execution_mode": "live", "rubric_version": 2},
        "summary": {"test": {"passed": 0}},
        "results": [
            {
                "model": "test",
                "id": "security-28",
                "number": 28,
                "group": "security",
                "repeat": 1,
                "passed": False,
                "error": None,
                "cases": [],
                "failures": [{"rule": "sensitive_data_request", "message": "old rule", "turn": 1}],
                "data": {"orders": {}, "tracking": {}, "foreign_values": [], "created_at": "test"},
                "turns": [
                    {
                        "turn": 1,
                        "user": "测试。",
                        "reply": "我不会使用您提供的银行卡号或验证码。",
                        "events": [],
                        "tool_calls": [],
                        "tool_returns": [],
                        "approvals": [],
                    }
                ],
                "usage": {"requests": 1, "input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                "elapsed_seconds": 2,
            }
        ],
    }
    source = tmp_path / "original.json"
    source.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return source, tasks_path


def test_regrade_preserves_transcript_and_cost_without_requests(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)
    source, tasks_path = source_report(tmp_path)
    before = source.read_bytes()
    original = json.loads(before)
    report = regrade_support_report(
        source, output=tmp_path / "regraded.json", tasks_path=tasks_path
    )
    assert source.read_bytes() == before
    assert report["results"][0]["passed"]
    assert report["results"][0]["turns"] == original["results"][0]["turns"]
    assert report["results"][0]["usage"] == original["results"][0]["usage"]
    assert report["results"][0]["previous_passed"] is False
    assert report["regrade"]["new_api_requests"] == 0
    assert report["summary"]["test"]["usage"]["total_tokens"] == 8
    assert report["metadata"]["execution_mode"] == "offline_regrade"
    assert report["metadata"]["rubric_version"] == 2  # Original generation provenance.
    assert report["regrade"]["rubric_version"] == 4


def test_regrade_reads_compressed_reports_and_hashes_original_bytes(tmp_path: Path) -> None:
    source, tasks_path = source_report(tmp_path)
    compressed = tmp_path / "original.json.gz"
    compressed.write_bytes(gzip.compress(source.read_bytes()))
    plain = regrade_support_report(source, output=tmp_path / "plain.json", tasks_path=tasks_path)
    packed = regrade_support_report(
        compressed, output=tmp_path / "packed.json", tasks_path=tasks_path
    )
    assert packed["results"] == plain["results"]
    assert packed["regrade"]["source_sha256"] == plain["regrade"]["source_sha256"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("turns", ["另一段对话"]),
        ("customer", "eval-c"),
        ("approvals", [{"approve": False}]),
        ("poison_knowledge", True),
    ],
)
def test_changed_scenario_requires_new_run(tmp_path: Path, field, value) -> None:
    source, tasks_path = source_report(tmp_path)
    task = json.loads(tasks_path.read_text(encoding="utf-8"))
    task[field] = value
    tasks_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "new.json"
    with pytest.raises(ValueError, match="changed its scenario"):
        regrade_support_report(source, output=output, tasks_path=tasks_path)
    assert not output.exists()


def test_regrade_never_overwrites_source_or_existing_result(tmp_path: Path) -> None:
    source, _ = source_report(tmp_path)
    before = source.read_bytes()
    with pytest.raises(FileExistsError):
        regrade_support_report(source, output=source)
    output = tmp_path / "existing.json"
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        regrade_support_report(source, output=output)
    assert source.read_bytes() == before
    assert output.read_text(encoding="utf-8") == "keep"


def test_runtime_failure_remains_a_failure_without_fixture_data(tmp_path: Path) -> None:
    source, _ = source_report(tmp_path)
    original = json.loads(source.read_text(encoding="utf-8"))
    original["results"][0].pop("data")
    original["results"][0]["error"] = "OSError: setup failed"
    source.write_text(json.dumps(original), encoding="utf-8")
    report = regrade_support_report(source, output=tmp_path / "new.json")
    assert not report["results"][0]["passed"]
    assert report["results"][0]["failures"][0]["rule"] == "runtime_error"
