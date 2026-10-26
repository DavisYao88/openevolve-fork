"""OpenEvolve adapter aligned with the production D+2 training pipeline."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/home/user/Documents/work/shsc-work/AI/AI项目/intelligent-scheduling/班次+库存+出库箱数/model")
D2_VERSION = "v2.0.0"
LABEL = "future_1d_box_qty"


def load_parameters(candidate_path: Path) -> dict:
    spec = importlib.util.spec_from_file_location("_candidate_params", candidate_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载候选文件：{candidate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    params = getattr(module, "PARAMS", None)
    if not isinstance(params, dict):
        raise SystemExit(f"候选文件没有PARAMS字典：{candidate_path}")
    return params


def _entity_id(df):
    return (df["warehouse_code"].astype(str) + "|" + df["business_object"].astype(str)
            + "|" + df["time_slot"].astype(str) + "|" + df["delivery_temperature"].astype(str))


def _build_config(market: str, use_mock: bool):
    from app.production_recipe import load_production_recipe_with_trace
    from app.schemas.train_config import TrainConfig

    recipe, _ = load_production_recipe_with_trace(market)
    default_start = "2025-04-01" if market == "DOMESTIC_MARKET" else "2024-07-01"
    return TrainConfig(
        label="future_1d", feature_version=D2_VERSION, business_object=market,
        warehouse_code=os.environ.get("FAMOU_D2_WAREHOUSE", "WBJ").strip().upper(),
        use_mock=use_mock,
        train_start_date=os.environ.get("FAMOU_D2_TRAIN_START", recipe.get("train_start_date") or default_start),
        train_end_date=os.environ.get("FAMOU_D2_TRAIN_END", "2026-09-15"),
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
    print(f"[adapter-v3] sealed_test={split_info['actual_ranges']['test']} rows={len(test_df)}; "
          f"development_rows={len(development)}", file=sys.stderr)
    return development


def _evaluate(params: dict) -> dict[str, float]:
    import numpy as np
    root = Path(os.environ.get("FAMOU_PRODUCTION_ROOT", str(DEFAULT_ROOT))).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"生产模型目录不存在：{root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app.features import build_category_encoders, get_feature_columns
    from app.pipelines.train_pipeline import _run_rolling_backtest

    use_mock = os.environ.get("FAMOU_D2_USE_MOCK", "").strip().lower() in {"1", "true", "yes"}
    market = os.environ.get("FAMOU_D2_MARKET", "DOMESTIC_MARKET").strip().upper()
    if market not in {"DOMESTIC_MARKET", "EXPORT_MARKET"}:
        raise RuntimeError(f"不支持市场：{market}")
    config = _build_config(market, use_mock)
    development = _development_set(_prepare_production_model_data(config))
    feature_columns = get_feature_columns(list(development.columns), LABEL, D2_VERSION)
    if len(feature_columns) != 25:
        raise RuntimeError(f"v2.0.0应为25个特征，实际{len(feature_columns)}")
    categorical = [c for c in ("time_slot", "delivery_temperature") if c in feature_columns]
    encoders = build_category_encoders(development, feature_columns, categorical)
    fixed_params = {**params, "random_state": 42, "verbosity": -1, "subsample_freq": 1, "n_jobs": 1}
    summary = _run_rolling_backtest(
        development, LABEL, feature_columns, encoders,
        lightgbm_params=fixed_params, ridge_params={}, enable_ridge=False,
        max_folds=int(os.environ.get("FAMOU_D2_FOLDS", str(config.rolling_backtest_folds))),
        train_months=config.rolling_train_months, val_months=config.rolling_val_months,
    )
    if summary.get("status") != "completed":
        raise RuntimeError(f"生产滚动回测未完成：{summary}")
    folds = summary["candidate_folds"]
    model_metrics = [f["metrics_by_model"]["LightGBM"] for f in folds]
    ma7_metrics = [f["metrics_by_model"]["MovingAverage7"] for f in folds]
    wmapes = [float(m["wmape"]) for m in model_metrics]
    biases = [abs(float(m["bias_ratio"])) for m in model_metrics]
    ma7_wmape = float(np.mean([m["wmape"] for m in ma7_metrics]))
    result = {
        "wmape": float(np.mean(wmapes)), "wmape_std": float(np.std(wmapes)),
        "worst_fold_wmape": float(max(wmapes)), "bias_abs": float(np.mean(biases)),
        "baseline_regret": float(max(0.0, np.mean(wmapes) - ma7_wmape)),
        "ma7_wmape": ma7_wmape, "fold_count": float(len(wmapes)),
    }
    if not all(math.isfinite(v) for v in result.values()):
        raise RuntimeError(f"评估产生非有限指标：{result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="D+2 production-aligned read-only adapter v3")
    parser.add_argument("candidate", help="候选init.py路径")
    args = parser.parse_args()
    params = load_parameters(Path(args.candidate).expanduser().resolve())
    print(f"[adapter-v3] params={json.dumps(params, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
    print(json.dumps(_evaluate(params), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
