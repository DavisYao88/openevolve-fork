"""openevolve-run-fork 的 fork_build.py 单测。

全部用 mock 顶掉 urllib，不依赖后端进程、不写真实 payload。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "skills"
    / "openevolve-run-fork"
    / "scripts"
    / "fork_build.py"
)

RUN_ID = "evolve_task_20260101120000"

TASK_MANIFEST = {
    "name": "原始任务",
    "template": "generic-python",
    "template_name": "通用程序演化",
    "objective": {"metrics": [{"name": "wmape", "direction": "minimize", "scale": 100}]},
    "validation": {"mode": "none"},
    "resumed_from": None,
    "created_at": "2026-01-01T12:00:00",
}

INPUT_FILES = {
    "task.json": json.dumps(TASK_MANIFEST, ensure_ascii=False),
    "prompt.md": "PROMPT TEXT",
    "init.py": "SEED CODE",
    "runner_adapter.py": "EVALUATOR CODE",
    "evaluator_callback.py": "BACKEND CALLBACK CODE",
    "config.yaml": "max_iterations: 30",
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture()
def fork_build(monkeypatch):
    """加载脚本模块，并把所有 HTTP 请求指向内存假后端。"""
    spec = importlib.util.spec_from_file_location("fork_build_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    calls: list[str] = []

    def fake_urlopen(url, timeout=None):  # noqa: ARG001
        calls.append(url)
        path = url.split("://", 1)[-1]
        path = path[path.index("/") :]
        if path == f"/api/runs/{RUN_ID}":
            return _FakeResponse(
                {"run": {"id": RUN_ID, "status": "interrupted", "planned_iterations": 30}}
            )
        prefix = f"/api/runs/{RUN_ID}/input/"
        if path.startswith(prefix):
            name = path[len(prefix) :]
            assert name in INPUT_FILES, f"未预期的输入文件：{name}"
            return _FakeResponse({"name": name, "content": INPUT_FILES[name]})
        if path == f"/api/runs/{RUN_ID}/output/best_program.py":
            return _FakeResponse({"name": "best_program.py", "content": "BEST CODE"})
        raise AssertionError(f"未预期的请求：{url}")

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    module.requested_urls = calls
    return module


def _invoke(module, monkeypatch, capsys, argv: list[str]) -> dict:
    """跑一次 main()，返回它打印的摘要。"""
    monkeypatch.setattr(sys, "argv", ["fork_build.py", *argv])
    module.main()
    return json.loads(capsys.readouterr().out)


def _payload(module, monkeypatch, capsys, tmp_path, argv: list[str]) -> tuple[dict, dict]:
    """跑一次 main()，返回写出的 payload 与摘要。"""
    out = tmp_path / "payload.json"
    summary = _invoke(module, monkeypatch, capsys, [*argv, "--out", str(out)])
    return json.loads(out.read_text(encoding="utf-8")), summary


# ---------------------------------------------------------------- run_id 规范化


@pytest.mark.parametrize(
    "raw",
    [
        RUN_ID,
        f"{RUN_ID}/",
        f"http://127.0.0.1:4800/runs/{RUN_ID}",
        f"http://127.0.0.1:4800/runs/{RUN_ID}/",
    ],
)
def test_run_id_accepts_bare_id_and_url(fork_build, monkeypatch, capsys, raw):
    summary = _invoke(fork_build, monkeypatch, capsys, [raw, "--dry-run"])
    assert summary["source_run"] == RUN_ID


def test_run_id_rejects_garbage(fork_build, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["fork_build.py", "not-a-run-id", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        fork_build.main()
    assert "看起来不是 run_id" in str(exc.value)


# ---------------------------------------------------------------- 字段来源


def test_payload_copies_source_task(fork_build, monkeypatch, capsys, tmp_path):
    payload, _ = _payload(fork_build, monkeypatch, capsys, tmp_path, [RUN_ID])

    assert payload["template"] == "generic-python"
    assert payload["task_name"] == "原始任务"
    assert payload["prompt"] == "PROMPT TEXT"
    assert payload["initial_program"] == "SEED CODE"
    assert payload["iterations"] == 30
    assert payload["objective"] == TASK_MANIFEST["objective"]
    assert payload["validation"] == {"mode": "none"}
    assert "resume_from" not in payload


def test_evaluator_comes_from_runner_adapter(fork_build, monkeypatch, capsys, tmp_path):
    """评测脚本必须取 runner_adapter.py，取 evaluator_callback.py 会导致重复包装。"""
    payload, _ = _payload(fork_build, monkeypatch, capsys, tmp_path, [RUN_ID])

    assert payload["evaluator"] == "EVALUATOR CODE"
    assert "BACKEND CALLBACK" not in payload["evaluator"]
    assert any("runner_adapter.py" in url for url in fork_build.requested_urls)
    assert not any("evaluator_callback.py" in url for url in fork_build.requested_urls)


def test_objective_parsed_from_task_json(fork_build, monkeypatch, capsys, tmp_path):
    payload, _ = _payload(fork_build, monkeypatch, capsys, tmp_path, [RUN_ID])
    assert [m["name"] for m in payload["objective"]["metrics"]] == ["wmape"]


# ---------------------------------------------------------------- 覆盖项


def test_iterations_and_name_override(fork_build, monkeypatch, capsys, tmp_path):
    payload, _ = _payload(
        fork_build, monkeypatch, capsys, tmp_path, [RUN_ID, "--iterations", "50", "--name", "新名字"]
    )
    assert payload["iterations"] == 50
    assert payload["task_name"] == "新名字"


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_iterations_must_be_positive(fork_build, monkeypatch, bad):
    monkeypatch.setattr(
        sys, "argv", ["fork_build.py", RUN_ID, "--iterations", bad, "--dry-run"]
    )
    with pytest.raises(SystemExit) as exc:
        fork_build.main()
    assert "iterations 必须是正整数" in str(exc.value)


def test_use_best_replaces_initial_program(fork_build, monkeypatch, capsys, tmp_path):
    payload, _ = _payload(fork_build, monkeypatch, capsys, tmp_path, [RUN_ID, "--use-best"])
    assert payload["initial_program"] == "BEST CODE"


def test_prompt_evaluator_init_override_from_files(fork_build, monkeypatch, capsys, tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("NEW PROMPT", encoding="utf-8")
    evaluator = tmp_path / "e.py"
    evaluator.write_text("NEW EVAL", encoding="utf-8")
    seed = tmp_path / "i.py"
    seed.write_text("NEW SEED", encoding="utf-8")
    objective = tmp_path / "o.json"
    objective.write_text(
        json.dumps({"metrics": [{"name": "rmse", "direction": "minimize"}]}), encoding="utf-8"
    )

    payload, _ = _payload(
        fork_build,
        monkeypatch,
        capsys,
        tmp_path,
        [
            RUN_ID,
            "--prompt", str(prompt),
            "--evaluator", str(evaluator),
            "--init", str(seed),
            "--objective", str(objective),
        ],
    )

    assert payload["prompt"] == "NEW PROMPT"
    assert payload["evaluator"] == "NEW EVAL"
    assert payload["initial_program"] == "NEW SEED"
    assert payload["objective"]["metrics"][0]["name"] == "rmse"


# ---------------------------------------------------------------- 续跑


def test_resume_sets_resume_from(fork_build, monkeypatch, capsys, tmp_path):
    payload, _ = _payload(fork_build, monkeypatch, capsys, tmp_path, [RUN_ID, "--resume"])
    assert payload["resume_from"] == RUN_ID


def test_checkpoint_sets_resume_from_with_name(fork_build, monkeypatch, capsys, tmp_path):
    payload, _ = _payload(
        fork_build, monkeypatch, capsys, tmp_path, [RUN_ID, "--checkpoint", "checkpoint_10"]
    )
    assert payload["resume_from"] == f"{RUN_ID}/checkpoint_10"


def test_checkpoint_wins_over_resume(fork_build, monkeypatch, capsys, tmp_path):
    payload, _ = _payload(
        fork_build,
        monkeypatch,
        capsys,
        tmp_path,
        [RUN_ID, "--resume", "--checkpoint", "checkpoint_3"],
    )
    assert payload["resume_from"] == f"{RUN_ID}/checkpoint_3"


# ---------------------------------------------------------------- 写盘与异常


def test_dry_run_writes_nothing(fork_build, monkeypatch, capsys, tmp_path):
    out = tmp_path / "payload.json"
    summary = _invoke(
        fork_build, monkeypatch, capsys, [RUN_ID, "--dry-run", "--out", str(out)]
    )

    assert not out.exists()
    assert summary["source_run"] == RUN_ID
    assert "payload" not in summary


def test_real_run_writes_payload(fork_build, monkeypatch, capsys, tmp_path):
    out = tmp_path / "payload.json"
    summary = _invoke(fork_build, monkeypatch, capsys, [RUN_ID, "--out", str(out)])

    assert summary["payload"] == str(out)
    assert out.is_file()


def test_unreachable_backend_raises_system_exit(fork_build, monkeypatch):
    def boom(url, timeout=None):  # noqa: ARG001
        raise fork_build.urllib.error.URLError("connection refused")

    monkeypatch.setattr(fork_build.urllib.request, "urlopen", boom)
    monkeypatch.setattr(sys, "argv", ["fork_build.py", RUN_ID, "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        fork_build.main()
    assert "连不上后端" in str(exc.value)
