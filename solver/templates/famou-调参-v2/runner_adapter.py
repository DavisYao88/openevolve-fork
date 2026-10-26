"""OpenEvolve adapter for read-only D+2 rolling validation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from pathlib import Path


DEFAULT_ROOT = Path(
    "/home/user/Documents/work/shsc-work/AI/AI项目/intelligent-scheduling/"
    "班次+库存+出库箱数/model"
)


def load_parameters(candidate_path: Path) -> dict:
    spec = importlib.util.spec_from_file_location("_candidate_params", candidate_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载候选文件：{candidate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    params = getattr(module, "PARAMS", None)
    if not isinstance(params, dict):
        raise SystemExit(f"候选文件没有 PARAMS 字典：{candidate_path}")
    return params


def _entity_id(df):
    return (
        df["warehouse_code"].astype(str)
        + "|" + df["business_object"].astype(str)
        + "|" + df["time_slot"].astype(str)
        + "|" + df["delivery_temperature"].astype(str)
    )


def _prepare_model_data(config, label):
    from app.features import CORE_TIME_SLOTS
    from app.loaders.dataset_loader import load_training_dataframe
    from app.rules.dirty_dates import compute_dirty_date_mask, resolve_rules

    df = load_training_dataframe(config)
    subset = df[(df["business_object"] == config.business_object) & df[label].notna()].copy()
    if subset.empty:
        raise RuntimeError(f"{config.business_object}/{label} 无样本")
    subset["entity_id"] = _entity_id(subset)

    dirty_mask, _ = compute_dirty_date_mask(
        subset,
        business_object=config.business_object,
        label_column=label,
        rules=resolve_rules(None),
        lag_spread_days=0,
    )
    subset = subset.loc[~dirty_mask].copy()

    history_days = subset.groupby("entity_id")["feature_dt"].nunique()
    valid = history_days[history_days >= config.min_history_days].index
    subset = subset[subset["entity_id"].isin(valid)].copy()
    subset = subset[subset["time_slot"].astype(str).isin(CORE_TIME_SLOTS)].copy()
    if subset.empty:
        raise RuntimeError("核心组合过滤后无样本")
    return subset.sort_values(["feature_dt", "entity_id"]).reset_index(drop=True)


def _development_set(model_df):
    from app.pipelines.train_pipeline import _time_series_split

    train_df, val_df, test_df, split_info = _time_series_split(model_df)
    development = (
        __import__("pandas").concat([train_df, val_df], ignore_index=True)
        .sort_values(["feature_dt", "entity_id"])
        .reset_index(drop=True)
    )
    print(
        "[adapter] sealed_test="
        f"{split_info['actual_ranges']['test']} rows={len(test_df)}; "
        f"development_rows={len(development)}",
        file=sys.stderr,
    )
    return development


def _fold_months(development, requested_folds, use_mock):
    import pandas as pd

    dates = pd.to_datetime(development["feature_dt"])
    months = sorted(dates.dt.to_period("M").unique())
    minimum_train_months = 2 if use_mock else 4
    eligible = months[minimum_train_months:]
    if not eligible:
        raise RuntimeError(f"可用月份不足：months={len(months)}")
    return eligible[-requested_folds:]


def _evaluate(params: dict) -> dict[str, float]:
    import numpy as np
    import pandas as pd

    root = Path(os.environ.get("FAMOU_PRODUCTION_ROOT", str(DEFAULT_ROOT))).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"生产模型目录不存在：{root}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from app.features import (
        CATEGORICAL_COLUMNS,
        build_category_encoders,
        get_feature_columns,
        prepare_feature_frame,
    )
    from app.metrics import compute_metrics
    from app.schemas.train_config import TrainConfig
    from app.trainers.lightgbm_trainer import train_lightgbm

    use_mock = os.environ.get("FAMOU_D2_USE_MOCK", "").strip().lower() in {"1", "true", "yes"}
    market = os.environ.get("FAMOU_D2_MARKET", "DOMESTIC_MARKET").strip().upper()
    config = TrainConfig(
        label="future_1d",
        feature_version="v2.0.0",
        business_object=market,
        warehouse_code=os.environ.get("FAMOU_D2_WAREHOUSE", "WBJ").strip().upper(),
        use_mock=use_mock,
        train_start_date=os.environ.get("FAMOU_D2_TRAIN_START", "2025-04-01"),
        train_end_date=os.environ.get("FAMOU_D2_TRAIN_END", "2026-09-15"),
        min_history_days=28,
        enable_optuna=False,
        enable_ridge=False,
        disable_pipeline_features=True,
        disable_fwd_pipeline_features=True,
        exclude_dirty_dates=True,
        enable_variance_calibration=False,
    )
    label = "future_1d_box_qty"
    model_df = _prepare_model_data(config, label)
    development = _development_set(model_df)
    requested_folds = max(1, int(os.environ.get("FAMOU_D2_FOLDS", "5")))
    val_months = _fold_months(development, requested_folds, use_mock)

    feature_columns = get_feature_columns(list(development.columns), label, "v2.0.0")
    if len(feature_columns) != 25:
        raise RuntimeError(f"v2.0.0 应为 25 个特征，实际 {len(feature_columns)}")
    categorical = [c for c in CATEGORICAL_COLUMNS if c in feature_columns]

    all_actual = []
    all_pred = []
    all_baseline = []
    fold_wmapes = []
    best_iterations = []
    dev_dates = pd.to_datetime(development["feature_dt"])

    fixed_params = {
        **params,
        "random_state": 42,
        "verbosity": -1,
        "subsample_freq": 1,
        "n_jobs": 1,
    }
    for val_month in val_months:
        val_start = val_month.start_time
        val_end = val_month.end_time.normalize()
        train_end = val_start - pd.Timedelta(days=3)
        train_fold = development[dev_dates <= train_end].copy()
        val_fold = development[(dev_dates >= val_start) & (dev_dates <= val_end)].copy()
        if train_fold.empty or val_fold.empty:
            continue

        encoders = build_category_encoders(train_fold, feature_columns, categorical)
        x_train = prepare_feature_frame(train_fold, feature_columns, categorical, encoders)
        x_val = prepare_feature_frame(val_fold, feature_columns, categorical, encoders)
        y_train = pd.to_numeric(train_fold[label], errors="coerce").fillna(0.0)
        y_val = pd.to_numeric(val_fold[label], errors="coerce").fillna(0.0)
        model, _ = train_lightgbm(
            x_train, y_train, x_val, y_val,
            categorical_columns=categorical,
            params=fixed_params,
        )
        pred = np.maximum(model.predict(x_val), 0.0)
        baseline = pd.to_numeric(
            val_fold["rolling_7_box_qty_mean"], errors="coerce"
        ).fillna(0.0).to_numpy()
        fold_wmapes.append(compute_metrics(y_val, pred)["wmape"])
        best_iterations.append(int(getattr(model, "best_iteration_", 0) or 0))
        all_actual.extend(y_val.to_numpy().tolist())
        all_pred.extend(pred.tolist())
        all_baseline.extend(baseline.tolist())

    if not fold_wmapes:
        raise RuntimeError("没有可评估的滚动折")
    global_metrics = compute_metrics(all_actual, all_pred)
    baseline_metrics = compute_metrics(all_actual, all_baseline)
    result = {
        "wmape": float(global_metrics["wmape"]),
        "wmape_std": float(np.std(fold_wmapes)),
        "bias_ratio": float(global_metrics["bias_ratio"]),
        "baseline_regret": float(max(0.0, global_metrics["wmape"] - baseline_metrics["wmape"])),
        "ma7_wmape": float(baseline_metrics["wmape"]),
        "fold_count": float(len(fold_wmapes)),
        "best_iteration_mean": float(np.mean(best_iterations)),
    }
    if not all(math.isfinite(v) for v in result.values()):
        raise RuntimeError(f"评估产生非有限指标：{result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="D+2 read-only evolution adapter")
    parser.add_argument("candidate", help="候选 init.py 路径")
    args = parser.parse_args()
    candidate = Path(args.candidate).expanduser().resolve()
    params = load_parameters(candidate)
    print(f"[adapter] params={json.dumps(params, ensure_ascii=False, sort_keys=True)}", file=sys.stderr)
    metrics = _evaluate(params)
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
