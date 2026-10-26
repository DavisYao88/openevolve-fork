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


DEFAULT_PROVIDER = "deepseek"


def _normalize_provider(provider: Any) -> str:
    """供应商名统一成小写；空值落到默认供应商，与设置页默认选项一致。"""
    text = str(provider or "").strip().lower()
    return text or DEFAULT_PROVIDER


def _provider_keys(secrets: dict[str, Any]) -> dict[str, str]:
    """读取 {供应商: key} 分桶，过滤掉空值与非字符串。"""
    raw = secrets.get("provider_keys")
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): value.strip()
        for name, value in raw.items()
        if isinstance(value, str) and value.strip()
    }


def _provider_api_bases(llm: dict[str, Any]) -> dict[str, str]:
    """读取 {供应商: api_base} 分桶，切换供应商时端点跟着走，不会串到上一家。"""
    raw = llm.get("provider_api_bases")
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): value.strip()
        for name, value in raw.items()
        if isinstance(value, str) and value.strip()
    }


def _current_api_key(provider: Any = None) -> str:
    """优先级：环境变量 OPENEVOLVE_API_KEY > 该供应商自己的 Key。

    Key 按供应商分桶存放，切换供应商不会覆盖别人的 Key。
    """
    env_key = (os.environ.get("OPENEVOLVE_API_KEY") or "").strip()
    if env_key:
        return env_key
    return _provider_keys(_load_secrets()).get(_normalize_provider(provider), "")


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
        # 旧版单个 api_base -> 当前供应商的端点表（切换供应商时端点跟着走）
        single_base = llm.pop("api_base", None)
        if isinstance(single_base, str) and single_base.strip():
            bases = _provider_api_bases(llm)
            bases.setdefault(_normalize_provider(llm.get("provider")), single_base.strip())
            llm["provider_api_bases"] = bases
            changed = True
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

    # 旧版「单个 api_key」-> 归属到当前供应商的桶，之后每个供应商各存一把
    legacy_single = secrets.pop("api_key", None)
    if legacy_single is not None:
        changed = True
        if isinstance(legacy_single, str) and legacy_single.strip():
            llm_settings = settings.get("llm")
            provider = _normalize_provider(
                llm_settings.get("provider") if isinstance(llm_settings, dict) else None
            )
            keys = _provider_keys(secrets)
            keys.setdefault(provider, legacy_single.strip())
            secrets["provider_keys"] = keys

    if changed:
        _save_secrets(secrets)


def _mask_secret(value: str) -> str:
    value = str(value or "")
    return f"{value[:4]}…{value[-4:]}" if len(value) > 8 else "*" * len(value)


def _public_settings() -> dict[str, Any]:
    """单模型设置 + 按供应商分桶的 Key 状态（Key 只回掩码）。

    env（.env.local / export）对所有供应商都生效，且优先级高于本地 secrets。
    """
    data = _load_gui_settings()
    llm = dict(data.get("llm") or {})
    llm.pop("api_key", None)
    llm.pop("models", None)

    provider = _normalize_provider(llm.get("provider"))
    bases = _provider_api_bases(llm)
    # 各供应商的端点回给前端，切换时直接带出（不含任何密钥）
    llm["provider_api_bases"] = bases
    if bases.get(provider):
        llm["api_base"] = bases[provider]

    env_base = (os.environ.get("OPENEVOLVE_API_BASE") or "").strip()
    env_model = (os.environ.get("OPENEVOLVE_PRIMARY_MODEL") or "").strip()
    if env_base:
        llm["api_base"] = env_base
    if env_model:
        llm["primary_model"] = env_model

    env_key = (os.environ.get("OPENEVOLVE_API_KEY") or "").strip()
    provider_keys = _provider_keys(_load_secrets())
    key = env_key or provider_keys.get(provider, "")

    llm["provider"] = provider
    llm["has_api_key"] = bool(key)
    llm["api_key_masked"] = _mask_secret(key)
    llm["api_key_source"] = "env" if env_key else ("secrets" if key else "")
    llm["env_api_key_masked"] = _mask_secret(env_key) if env_key else ""
    # 各供应商已保存的 Key（掩码），设置页切供应商时直接复用，无需重新请求
    llm["provider_keys"] = {name: _mask_secret(value) for name, value in provider_keys.items()}
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

    # 供应商：设置页显式配置过才覆盖，否则沿用 global_config 里的值
    raw_provider = settings.get("provider")
    if isinstance(raw_provider, str) and raw_provider.strip():
        provider = _normalize_provider(raw_provider)
        llm["provider"] = provider
    else:
        provider = _normalize_provider(llm.get("provider"))

    # 端点按供应商取，取不到就清掉，绝不沿用上一家的 api_base
    base = _provider_api_bases(settings).get(provider) or settings.get("api_base")
    if isinstance(base, str) and base.strip():
        llm["api_base"] = base.strip()
    else:
        llm.pop("api_base", None)

    model = settings.get("primary_model")
    if isinstance(model, str) and model.strip():
        llm["primary_model"] = model.strip()

    # 环境变量优先级最高：设置页展示的就是它，这里必须同样生效
    env_base = (os.environ.get("OPENEVOLVE_API_BASE") or "").strip()
    env_model = (os.environ.get("OPENEVOLVE_PRIMARY_MODEL") or "").strip()
    if env_base:
        llm["api_base"] = env_base
    if env_model:
        llm["primary_model"] = env_model

    # 只取当前供应商自己的 Key；GUI 接管供应商配置后，绝不沿用 global 里别家的 Key
    api_key = _current_api_key(provider)
    if api_key:
        llm["api_key"] = api_key
    elif isinstance(raw_provider, str) and raw_provider.strip():
        llm.pop("api_key", None)

    for key in ("temperature", "max_tokens"):
        value = settings.get(key)
        if value not in ("", None):
            llm[key] = value
