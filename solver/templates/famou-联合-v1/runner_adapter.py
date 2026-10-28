"""OpenEvolve adapter：D+2 货量预测的「参数 + 代码」联合演化。

与 famou-调参-v4 的区别只有一处：候选程序不再只提供一个 PARAMS 字典，
而是同时提供两个可演化函数，adapter 会在生产回测的固定位置调用它们。

    PARAMS                      LightGBM 参数（范围在 adapter 内裁剪）
    augment_features(df)        追加特征列，返回 (df, 新增列名列表)
    postprocess(pred, ctx)      对预测结果做后处理，返回同形状数组

评测口径与生产完全一致：同样的数据准备、样本组合、组合分流、4+1 滚动回测、
同样的指标计算。候选代码只能影响「模型输入」和「预测后处理」两处，
无法接触验证集标签、无法改切分、无法改指标。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/home/user/Documents/work/shsc-work/AI/AI项目/intelligent-scheduling/班次+库存+出库箱数/model")
DEFAULT_VERSION = "v2.0.0"
SUPPORTED_VERSIONS = {"v2.0.0", "v2.1.0", "v2.1.1"}
EXPECTED_FEATURE_COUNT = {"v2.0.0": 25, "v2.1.0": 29, "v2.1.1": 31}
LABEL = "future_1d_box_qty"

# 候选新增特征的护栏：最多 20 列，避免候选把维度炸开拖垮回测
MAX_EXTRA_FEATURES = 20

# 参数裁剪范围：候选写出越界值时不报错、直接夹到区间，保证回测能跑完
PARAM_RANGES = {
    "n_estimators": (150, 1200, int),
    "learning_rate": (0.008, 0.12, float),
    "num_leaves": (4, 63, int),
    "max_depth": (2, 6, int),
    "min_child_samples": (30, 300, int),
    "subsample": (0.6, 1.0, float),
    "colsample_bytree": (0.45, 1.0, float),
    "reg_alpha": (0.0, 20.0, float),
    "reg_lambda": (0.0, 20.0, float),
    "min_split_gain": (0.0, 0.5, float),
}
OBJECTIVE_CHOICES = {"regression", "regression_l1"}

PROFILE_RELATIVE = Path("solver") / "templates" / "famou-联合-v1" / "profile.json"


def _profile_candidates() -> list[Path]:
    explicit = os.environ.get("FAMOU_D2_PROFILE")
    if explicit:
        return [Path(explicit).expanduser()]
    here = Path(__file__).resolve()
    candidates = [here.parent / "profile.json"]
    candidates += [parent / PROFILE_RELATIVE for parent in here.parents[:5]]
    candidates.append(Path.cwd() / PROFILE_RELATIVE)
    return candidates


def load_profile() -> tuple[dict, str]:
    path = next((p for p in _profile_candidates() if p.is_file()), None)
    if path is None:
        return {}, "<none>"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"运行档位读取失败：{path}: {exc}") from exc
    return (data if isinstance(data, dict) else {}), str(path)


PROFILE, PROFILE_SOURCE = load_profile()


def cfg(env_name: str, profile_key: str, default):
    """环境变量 > profile.json > 默认值。"""
    value = os.environ.get(env_name)
    if value not in (None, ""):
        return value
    value = PROFILE.get(profile_key)
    return default if value in (None, "") else value


def resolve_version() -> str:
    version = str(cfg("FAMOU_D2_FEATURE_VERSION", "feature_version", DEFAULT_VERSION)).strip()
    if version not in SUPPORTED_VERSIONS:
        raise RuntimeError(f"不支持的特征版本：{version}，可选 {sorted(SUPPORTED_VERSIONS)}")
    return version


def load_candidate(candidate_path: Path):
    """加载候选程序，返回 (PARAMS, augment_features, postprocess)。

    三个钩子都是可选的：缺省时退化成「不追加特征 / 不改预测」，等价于纯调参。
    """
    spec = importlib.util.spec_from_file_location("_candidate_program", candidate_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载候选文件：{candidate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    params = getattr(module, "PARAMS", None)
    if not isinstance(params, dict):
        raise SystemExit(f"候选文件缺少 PARAMS 字典：{candidate_path}")

    augment = getattr(module, "augment_features", None)
    postprocess = getattr(module, "postprocess", None)
    if augment is not None and not callable(augment):
        raise SystemExit("augment_features 必须是函数")
    if postprocess is not None and not callable(postprocess):
        raise SystemExit("postprocess 必须是函数")
    return params, augment, postprocess


def sanitize_params(params: dict) -> dict:
    """夹取参数到安全区间，剔除不认识的键，避免候选把回测跑挂。"""
    clean: dict = {}
    for name, (low, high, caster) in PARAM_RANGES.items():
        raw = params.get(name)
        try:
            value = caster(raw)
        except (TypeError, ValueError):
            continue
        clean[name] = min(max(value, low), high)
    objective = str(params.get("objective") or "").strip()
    if objective in OBJECTIVE_CHOICES:
        clean["objective"] = objective
    if not clean:
        raise SystemExit("PARAMS 里没有任何可识别的参数键")
    return clean


def _entity_id(df):
    return (df["warehouse_code"].astype(str) + "|" + df["business_object"].astype(str)
            + "|" + df["time_slot"].astype(str) + "|" + df["delivery_temperature"].astype(str))


def _build_config(market: str, use_mock: bool, version: str):
    from app.production_recipe import load_production_recipe_with_trace
    from app.schemas.train_config import TrainConfig

    recipe, _ = load_production_recipe_with_trace(market)
    default_start = "2025-04-01" if market == "DOMESTIC_MARKET" else "2024-07-01"
    return TrainConfig(
        label="future_1d", feature_version=version, business_object=market,
        warehouse_code=str(cfg("FAMOU_D2_WAREHOUSE", "warehouse", "WBJ")).strip().upper(),
        use_mock=use_mock,
        train_start_date=cfg("FAMOU_D2_TRAIN_START", "train_start",
                             recipe.get("train_start_date") or default_start),
        train_end_date=cfg("FAMOU_D2_TRAIN_END", "train_end", "2026-09-15"),
        min_history_days=28, enable_optuna=False, enable_ridge=False,
        enable_combo_scoring=True,
        combo_score_threshold=float(recipe.get("combo_score_threshold", 50.0)),
        min_daily_volume=float(recipe.get("min_daily_volume", 500.0)),
        adaptive_volume_threshold=bool(recipe.get("adaptive_volume_threshold", False)),
        adaptive_volume_floor=float(recipe.get("adaptive_volume_floor", 200.0)),
        adaptive_volume_ratio=float(recipe.get("adaptive_volume_ratio", 0.25)),
        trim_volume_outliers=bool(recipe.get("trim_volume_outliers", False)),
        volume_trim_lower=float(recipe.get("volume_trim_lower", 0.05)),
        volume_trim_upper=float(recipe.get("volume_trim_upper", 0.95)),
        combo_absorb_growth=bool(recipe.get("combo_absorb_growth", True)),
        disable_pipeline_features=True, disable_fwd_pipeline_features=True,
        exclude_dirty_dates=bool(recipe.get("exclude_dirty_dates", False)),
        dirty_date_rules=recipe.get("dirty_date_rules"),
        enable_variance_calibration=False, enable_rolling_backtest=True,
        rolling_backtest_folds=int(recipe.get("rolling_backtest_folds", 7)),
        rolling_train_months=4, rolling_val_months=1,
    )


def _prepare_production_model_data(config):
    from app.features import CORE_TIME_SLOTS
    from app.loaders.dataset_loader import load_training_dataframe
    from app.pipelines.train_pipeline import _compute_combo_scores
    from app.rules.dirty_dates import compute_dirty_date_mask, resolve_rules

    df = load_training_dataframe(config)
    subset = df[(df["business_object"] == config.business_object) & df[LABEL].notna()].copy()
    if subset.empty:
        raise RuntimeError(f"{config.business_object}/{LABEL}无样本")
    subset["entity_id"] = _entity_id(subset)
    if config.exclude_dirty_dates:
        dirty_mask, _ = compute_dirty_date_mask(
            subset, business_object=config.business_object, label_column=LABEL,
            rules=resolve_rules(config.dirty_date_rules),
            lag_spread_days=config.dirty_date_lag_spread_days,
        )
        subset = subset.loc[~dirty_mask].copy()
    history_days = subset.groupby("entity_id")["feature_dt"].nunique()
    valid = history_days[history_days >= config.min_history_days].index
    model_df = subset[subset["entity_id"].isin(valid)].copy()
    model_df = model_df[model_df["time_slot"].astype(str).isin(CORE_TIME_SLOTS)].copy()
    scores = _compute_combo_scores(config)
    if scores is not None and not scores.empty:
        market_scores = scores[scores["business_object"] == config.business_object]
        rule_entities = set(market_scores.loc[market_scores["routed"] == "RULE", "entity_id"])
        model_df = model_df[~model_df["entity_id"].isin(rule_entities)].copy()
    if model_df.empty:
        raise RuntimeError("生产组合分流后无可训练样本")
    return model_df.sort_values(["feature_dt", "entity_id"]).reset_index(drop=True)


def _development_set(model_df):
    import pandas as pd
    from app.pipelines.train_pipeline import _time_series_split

    train_df, val_df, test_df, split_info = _time_series_split(model_df)
    development = pd.concat([train_df, val_df], ignore_index=True).sort_values(
        ["feature_dt", "entity_id"]
    ).reset_index(drop=True)
    print(f"[adapter-joint] sealed_test={split_info['actual_ranges']['test']} rows={len(test_df)}; "
          f"development_rows={len(development)}", file=sys.stderr)
    return development


def _apply_candidate_features(development, augment, production_columns):
    """调用候选的特征钩子，返回 (新 df, 新增列名列表)。

    传进钩子前先抹掉标签列，候选无法用真值构造特征。
    """
    import pandas as pd

    if augment is None:
        return development, []

    blind = development.drop(columns=[LABEL], errors="ignore").copy()
    result = augment(blind)
    if not isinstance(result, tuple) or len(result) != 2:
        raise RuntimeError("augment_features 必须返回 (df, 新增列名列表)")
    augmented, extra_columns = result
    if not isinstance(augmented, pd.DataFrame):
        raise RuntimeError("augment_features 的第一个返回值必须是 DataFrame")
    if not isinstance(extra_columns, (list, tuple)):
        raise RuntimeError("augment_features 的第二个返回值必须是列名列表")

    extra = [str(name) for name in extra_columns]
    if len(extra) > MAX_EXTRA_FEATURES:
        raise RuntimeError(f"新增特征超过 {MAX_EXTRA_FEATURES} 列：{len(extra)}")
    missing = [name for name in extra if name not in augmented.columns]
    if missing:
        raise RuntimeError(f"声明的特征列在返回的 DataFrame 里不存在：{missing}")
    collisions = [name for name in extra if name in production_columns]
    if collisions:
        raise RuntimeError(f"不允许覆盖生产特征列：{collisions}")

    if len(augmented) != len(development):
        raise RuntimeError("augment_features 改变了行数，不允许")
    if not augmented.index.equals(development.index):
        raise RuntimeError("augment_features 改变了索引顺序，请勿排序/过滤")

    # 只把新增列合并回原表：标签列等必须保留，候选无法借此删列
    merged = development.copy()
    for name in extra:
        series = augmented[name]
        if series.isna().any():
            series = series.fillna(0.0)
        if not pd.api.types.is_numeric_dtype(series):
            raise RuntimeError(f"新增特征必须是数值列：{name}")
        merged[name] = series

    return merged, extra


def _evaluate(params: dict, augment=None, postprocess=None) -> dict[str, float]:
    import numpy as np
    import pandas as pd

    root = Path(os.environ.get("FAMOU_PRODUCTION_ROOT", str(DEFAULT_ROOT))).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"生产模型目录不存在：{root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from app.features import (
        CATEGORICAL_COLUMNS, build_category_encoders, get_feature_columns,
        prepare_feature_frame,
    )
    from app.pipelines.train_pipeline import _time_series_split, compute_metrics
    from app.trainers.baseline_trainer import predict_moving_average_7, predict_naive
    from app.trainers.lightgbm_trainer import train_lightgbm

    use_mock = str(cfg("FAMOU_D2_USE_MOCK", "use_mock", "")).strip().lower() in {"1", "true", "yes"}
    market = str(cfg("FAMOU_D2_MARKET", "market", "DOMESTIC_MARKET")).strip().upper()
    if market not in {"DOMESTIC_MARKET", "EXPORT_MARKET"}:
        raise RuntimeError(f"不支持的市场：{market}")
    version = resolve_version()
    config = _build_config(market, use_mock, version)
    max_folds = int(cfg("FAMOU_D2_FOLDS", "folds", config.rolling_backtest_folds))

    development = _development_set(_prepare_production_model_data(config))
    production_columns = get_feature_columns(list(development.columns), LABEL, version)
    expected = EXPECTED_FEATURE_COUNT[version]
    if len(production_columns) != expected:
        raise RuntimeError(f"{version} 应为 {expected} 个特征，实际 {len(production_columns)}")

    # —— 候选代码介入点 1：追加特征 ——
    development, extra_columns = _apply_candidate_features(development, augment, production_columns)
    feature_columns = production_columns + extra_columns

    print(f"[adapter-joint] market={market} version={version} warehouse={config.warehouse_code} "
          f"folds<={max_folds} extra_features={extra_columns} profile={PROFILE_SOURCE}",
          file=sys.stderr)

    categorical = [c for c in ("time_slot", "delivery_temperature") if c in feature_columns]
    encoders = build_category_encoders(development, feature_columns, categorical)
    fixed_params = {**params, "random_state": 42, "verbosity": -1, "subsample_freq": 1, "n_jobs": 1}

    development = development.copy()
    development["feature_dt"] = pd.to_datetime(development["feature_dt"])
    development["ym"] = development["feature_dt"].dt.to_period("M")
    months = sorted(development["ym"].unique())
    train_months, val_months = config.rolling_train_months, config.rolling_val_months
    if len(months) < train_months + val_months:
        raise RuntimeError("可用月份不足，无法回测")

    wmapes: list[float] = []
    biases: list[float] = []
    ma7_wmapes: list[float] = []
    fold_count = 0
    for start in range(0, len(months) - train_months - val_months + 1, train_months):
        if fold_count >= max_folds:
            break
        train_slice = months[start:start + train_months]
        val_slice = months[start + train_months:start + train_months + val_months]
        if not val_slice:
            break
        train_fold = development[development["ym"].isin(train_slice)]
        val_fold = development[development["ym"].isin(val_slice)]
        if development.get("feature_version", pd.Series(dtype=str)).eq("v2.0.0").any() and not val_fold.empty:
            cutoff = pd.to_datetime(val_fold["feature_dt"]).min() - pd.Timedelta(days=3)
            train_fold = train_fold[pd.to_datetime(train_fold["feature_dt"]) <= cutoff]
        if train_fold.empty or val_fold.empty:
            continue

        cat_cols = [c for c in feature_columns if c in CATEGORICAL_COLUMNS]
        X_train = prepare_feature_frame(train_fold, feature_columns, cat_cols, encoders)
        X_val = prepare_feature_frame(val_fold, feature_columns, cat_cols, encoders)
        y_train = pd.to_numeric(train_fold[LABEL], errors="coerce").fillna(0.0)
        y_val = pd.to_numeric(val_fold[LABEL], errors="coerce").fillna(0.0)

        lag_column = "lag_3_box_qty" if version == "v2.0.0" else "lag_1_box_qty"
        ma7_fold = compute_metrics(y_val, predict_moving_average_7(val_fold))
        ma7_wmapes.append(float(ma7_fold["wmape"]))

        model, _ = train_lightgbm(
            X_train, y_train, X_val, y_val,
            categorical_columns=cat_cols, params=fixed_params,
        )
        pred = model.predict(X_val)

        # —— 候选代码介入点 2：预测后处理（拿不到 y_val，无法作弊）——
        if postprocess is not None:
            ctx = {
                "fold": fold_count + 1,
                "val_months": [str(val_slice[0]), str(val_slice[-1])],
                "columns": list(val_fold.columns),
                "feature_columns": list(feature_columns),
                "extra_feature_columns": list(extra_columns),
                "params": dict(fixed_params),
            }
            pred = postprocess(np.asarray(pred, dtype=float), ctx)
            pred = np.asarray(pred, dtype=float).reshape(-1)
            if pred.shape[0] != len(y_val):
                raise RuntimeError(f"postprocess 返回长度 {pred.shape[0]} 与验证集 {len(y_val)} 不一致")
            if not np.isfinite(pred).all():
                raise RuntimeError("postprocess 返回了非有限值")

        metrics = compute_metrics(y_val, pred)
        wmapes.append(float(metrics["wmape"]))
        biases.append(abs(float(metrics["bias_ratio"])))
        fold_count += 1

    if not wmapes:
        raise RuntimeError("没有有效折，回测未产生指标")

    ma7_wmape = float(np.mean(ma7_wmapes)) if ma7_wmapes else 0.0
    result = {
        "wmape": float(np.mean(wmapes)),
        "wmape_std": float(np.std(wmapes)),
        "worst_fold_wmape": float(max(wmapes)),
        "bias_abs": float(np.mean(biases)),
        "baseline_regret": float(max(0.0, float(np.mean(wmapes)) - ma7_wmape)),
        "ma7_wmape": ma7_wmape,
        "fold_count": float(len(wmapes)),
    }
    if not all(math.isfinite(v) for v in result.values()):
        raise RuntimeError(f"评估产生非有限指标：{result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="D+2 参数+代码联合演化 adapter")
    parser.add_argument("candidate", help="候选 init.py 路径")
    args = parser.parse_args()

    raw_params, augment, postprocess = load_candidate(Path(args.candidate).expanduser().resolve())
    params = sanitize_params(raw_params)
    hooks = [name for name, fn in (("augment_features", augment), ("postprocess", postprocess)) if fn]
    print(f"[adapter-joint] params={json.dumps(params, ensure_ascii=False, sort_keys=True)} "
          f"hooks={hooks}", file=sys.stderr)
    print(json.dumps(_evaluate(params, augment, postprocess), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
