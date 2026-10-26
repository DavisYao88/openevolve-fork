"""密钥与 GUI 设置（纯模块，不 import visualizer / backend.core / backend.app）。"""

from __future__ import annotations

import json
import os
from typing import Any

from . import paths


def _load_secrets() -> dict[str, Any]:
    if not paths.GUI_SECRETS_PATH.is_file():
        return {}
    try:
        data = json.loads(paths.GUI_SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_secrets(data: dict[str, Any]) -> None:
    paths.GUI_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    paths.GUI_SECRETS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(paths.GUI_SECRETS_PATH, 0o600)
    except OSError:
        pass


def _current_api_key() -> str:
    """优先级：环境变量 OPENEVOLVE_API_KEY > 本地 secrets 文件。"""
    env_key = (os.environ.get("OPENEVOLVE_API_KEY") or "").strip()
    if env_key:
        return env_key
    return str(_load_secrets().get("api_key") or "")


def _load_gui_settings() -> dict[str, Any]:
    if not paths.GUI_SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(paths.GUI_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_gui_settings(data: dict[str, Any]) -> None:
    paths.GUI_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    paths.GUI_SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _migrate_legacy_secrets() -> None:
    """把旧版明文 api_key / 多模型配置收敛成「单模型 + 独立 secrets 文件」。"""
    secrets = _load_secrets()
    changed = False

    # 旧版多模型密钥 {模型名: key} -> 单个 api_key
    legacy_keys = secrets.pop("models", None)
    if isinstance(legacy_keys, dict) and legacy_keys:
        first_key = next((str(v) for v in legacy_keys.values() if isinstance(v, str) and v), "")
        if first_key:
            secrets["api_key"] = first_key
        changed = True

    settings = _load_gui_settings()
    llm = settings.get("llm")
    if isinstance(llm, dict):
        if llm.get("api_key"):
            secrets.setdefault("api_key", llm["api_key"])
            llm.pop("api_key", None)
            changed = True
        # 旧版多模型列表 -> 单模型字段（取第一个可用模型）
        raw_models = llm.pop("models", None)
        if isinstance(raw_models, list) and raw_models:
            changed = True
            first = next(
                (
                    item
                    for item in raw_models
                    if isinstance(item, dict) and (item.get("name") or item.get("api_base"))
                ),
                None,
            )
            if isinstance(first, dict):
                for source, target in (
                    ("provider", "provider"),
                    ("api_base", "api_base"),
                    ("name", "primary_model"),
                ):
                    value = str(first.get(source) or "").strip()
                    if value and not llm.get(target):
                        llm[target] = value
        if changed:
            try:
                _save_gui_settings(settings)
            except OSError:
                pass

    try:
        import yaml

        config = yaml.safe_load(paths.GLOBAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 迁移失败不应阻止启动
        config = None
    if isinstance(config, dict):
        llm_config = config.get("llm")
        if isinstance(llm_config, dict) and llm_config.get("api_key"):
            secrets.setdefault("api_key", llm_config["api_key"])
            llm_config["api_key"] = ""
            try:
                import yaml

                paths.GLOBAL_CONFIG_PATH.write_text(
                    yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
                )
                changed = True
            except OSError:
                pass

    if changed and secrets.get("api_key"):
        _save_secrets(secrets)


def _mask_secret(value: str) -> str:
    value = str(value or "")
    return f"{value[:4]}…{value[-4:]}" if len(value) > 8 else "*" * len(value)


def _public_settings() -> dict[str, Any]:
    """单模型设置。key 只回掩码；env（.env.local / export）优先于本地 secrets。"""
    data = _load_gui_settings()
    llm = dict(data.get("llm") or {})
    llm.pop("api_key", None)
    llm.pop("models", None)

    env_base = (os.environ.get("OPENEVOLVE_API_BASE") or "").strip()
    env_model = (os.environ.get("OPENEVOLVE_PRIMARY_MODEL") or "").strip()
    if env_base:
        llm["api_base"] = env_base
    if env_model:
        llm["primary_model"] = env_model

    key = _current_api_key()
    llm["has_api_key"] = bool(key)
    llm["api_key_masked"] = _mask_secret(key)
    llm["api_key_source"] = (
        "env" if (os.environ.get("OPENEVOLVE_API_KEY") or "").strip()
        else ("secrets" if key else "")
    )
    llm["env_file"] = (paths.PROJECT_ROOT / ".env.local").is_file()
    return {"theme": data.get("theme", "light"), "llm": llm}


def _apply_gui_llm_settings(config: dict[str, Any]) -> None:
    """把设置页里的单模型配置写进本次任务的 llm 段。

    真实 key 不回写到 config 文件：写盘前会被换成 ${OPENEVOLVE_API_KEY} 占位符，
    由子进程环境变量注入。
    """
    llm = config.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        config["llm"] = llm

    # 单模型模式：清掉历史多模型写法，否则 llm.models 会盖过 primary_model
    llm.pop("models", None)
    llm.pop("evaluator_models", None)
    llm.pop("secondary_model", None)

    settings = _load_gui_settings().get("llm") or {}
    for key in ("provider", "api_base", "primary_model"):
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            llm[key] = value.strip()

    api_key = _current_api_key()
    if api_key:
        llm["api_key"] = api_key

    for key in ("temperature", "max_tokens"):
        value = settings.get(key)
        if value not in ("", None):
            llm[key] = value
