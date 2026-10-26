"""GUI 设置接口（主题 + 单模型 LLM 配置）。"""

from __future__ import annotations

import logging

from flask import Blueprint, abort, jsonify, request

from ..secrets import (
    _load_gui_settings,
    _load_secrets,
    _normalize_provider,
    _public_settings,
    _save_gui_settings,
    _save_secrets,
)
from .common import _is_local_request

logger = logging.getLogger(__name__)

settings_api_bp = Blueprint("settings_api", __name__)


@settings_api_bp.route("/api/settings", methods=["GET", "POST"])
def gui_settings_api():
    """Read or update GUI preferences: theme + the single LLM model used by new runs."""
    if not _is_local_request():
        abort(403)
    if request.method == "GET":
        return jsonify(_public_settings())

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "请求内容必须是 JSON 对象。"}), 400

    current = _load_gui_settings()
    llm = dict(current.get("llm") or {})
    llm.pop("api_key", None)
    # 单模型模式：历史多模型列表不再保留
    llm.pop("models", None)
    incoming = payload.get("llm")
    if isinstance(incoming, dict):
        for key in ("provider", "api_base", "primary_model"):
            if isinstance(incoming.get(key), str):
                llm[key] = incoming[key].strip()
        # Key 只写独立 secrets 文件，并按供应商分桶：新增供应商即新增一个 Key
        provider_name = _normalize_provider(llm.get("provider"))
        api_key = incoming.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            secrets = _load_secrets()
            secrets.pop("api_key", None)  # 旧版单 Key 已迁移进分桶
            secrets.pop("models", None)
            keys = secrets.get("provider_keys")
            if not isinstance(keys, dict):
                keys = {}
            keys[provider_name] = api_key.strip()
            secrets["provider_keys"] = keys
            _save_secrets(secrets)
        if incoming.get("clear_api_key"):
            secrets = _load_secrets()
            secrets.pop("api_key", None)
            keys = secrets.get("provider_keys")
            if isinstance(keys, dict):
                keys.pop(provider_name, None)
                if keys:
                    secrets["provider_keys"] = keys
                else:
                    secrets.pop("provider_keys", None)
            _save_secrets(secrets)
        for key, cast in (("temperature", float), ("max_tokens", int)):
            if key in incoming:
                value = incoming.get(key)
                if value in ("", None):
                    llm.pop(key, None)
                    continue
                try:
                    llm[key] = cast(value)
                except (TypeError, ValueError):
                    return jsonify({"error": f"{key} 不是合法数值。"}), 400

    theme = payload.get("theme")
    if theme in ("light", "dark"):
        current["theme"] = theme
    current["llm"] = llm
    try:
        _save_gui_settings(current)
    except OSError as error:
        logger.exception("Could not save GUI settings")
        return jsonify({"error": f"保存失败：{error}"}), 500
    return jsonify(_public_settings())
