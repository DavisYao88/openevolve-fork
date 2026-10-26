"""任务模板加载与编辑器默认内容（纯模块，不 import visualizer / backend.core / backend.app）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import paths


def _template_dirs() -> dict[str, Path]:
    """Map template id -> template directory (any folder holding template.yml)."""
    if not paths.TEMPLATE_DIR.is_dir():
        return {}
    return {
        path.name: path
        for path in sorted(paths.TEMPLATE_DIR.iterdir())
        if (path / "template.yml").is_file()
    }


def _template_summaries() -> list[dict[str, Any]]:
    """Id/name/description for every template, for the template picker."""
    import yaml

    summaries = []
    for template_id, template_dir in _template_dirs().items():
        name = template_id
        description = ""
        try:
            meta = yaml.safe_load((template_dir / "template.yml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            meta = None
        if isinstance(meta, dict):
            name = meta.get("name") or template_id
            description = meta.get("description") or ""
        summaries.append({"id": template_id, "name": name, "description": description})
    return summaries


def _load_solver_evaluator():
    """Import solver/evaluator.py by path, so runs share one evaluator."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("solver_evaluator", paths.SOLVER_EVALUATOR_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载通用评估器：{paths.SOLVER_EVALUATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_template(template_id: Optional[str] = None) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read one task template: metadata, objective/validation and editor contents."""
    import yaml

    template_dirs = _template_dirs()
    if not template_dirs:
        return None, f"没有找到任何任务模板：{paths.TEMPLATE_DIR}"

    selected = template_id if template_id in template_dirs else None
    if selected is None:
        selected = (
            paths.DEFAULT_TEMPLATE_ID if paths.DEFAULT_TEMPLATE_ID in template_dirs else sorted(template_dirs)[0]
        )
    template_dir = template_dirs[selected]

    try:
        global_config = yaml.safe_load(paths.GLOBAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        meta = yaml.safe_load((template_dir / "template.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        return None, f"无法读取任务模板 {selected}：{error}"
    if not isinstance(meta, dict):
        return None, f"任务模板 {selected} 的 template.yml 必须是键值映射"

    files = meta.get("files") or {}
    names = {
        "initial_program": files.get("initial_program") or "init.py",
        "prompt": files.get("prompt") or "prompt.md",
        "evaluator": files.get("adapter") or "runner_adapter.py",
    }
    contents: dict[str, Any] = {}
    for key, name in names.items():
        try:
            contents[key] = (template_dir / name).read_text(encoding="utf-8")
        except OSError as error:
            return None, f"无法读取任务模板 {selected} 的 {name}：{error}"

    defaults = meta.get("defaults") or {}
    if not isinstance(defaults, dict):
        return None, f"任务模板 {selected} 的 defaults 必须是键值映射"
    iterations = defaults.get("iterations")
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 1:
        iterations = global_config.get("max_iterations") if isinstance(global_config, dict) else 10

    return {
        "id": selected,
        "name": meta.get("name") or selected,
        "description": meta.get("description") or "",
        "objective": meta.get("objective"),
        "validation": meta.get("validation"),
        "iterations": iterations,
        **contents,
    }, None


def _launch_defaults(template_id: Optional[str] = None) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    import yaml

    template, error = _load_template(template_id)
    if error:
        return None, error

    if (
        not isinstance(template["iterations"], int)
        or isinstance(template["iterations"], bool)
        or template["iterations"] < 1
    ):
        return None, "全局配置中的 max_iterations 必须是正整数。"

    # Flat keys keep the legacy launch page and older clients working.
    payload = {
        "templates": _template_summaries(),
        "default_template": template["id"],
        "template": template,
    }
    payload.update(
        {
            key: template[key]
            for key in ("initial_program", "evaluator", "prompt", "iterations", "objective", "validation")
        }
    )
    # 可编辑的 YAML 文本：objective 决定怎么算分，validation 决定候选允许怎么改。
    for key in ("objective", "validation"):
        value = template[key]
        payload[f"{key}_yaml"] = (
            yaml.safe_dump(value, allow_unicode=True, sort_keys=False) if value else ""
        )
    return payload, None


def _parse_mapping_field(value: Any, field: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Accept a YAML/JSON string or a mapping for objective/validation overrides."""
    if value is None:
        return None, None
    if isinstance(value, str):
        import yaml

        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError as error:
            return None, f"{field} 不是合法的 YAML/JSON：{error}"
    if value == "" or value is None:
        return None, None
    if not isinstance(value, dict):
        return None, f"{field} 必须是键值映射（或 YAML/JSON 文本）。"
    return value, None
