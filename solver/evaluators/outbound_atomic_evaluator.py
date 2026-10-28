"""仓店出库量预测评估器：候选按「交货日期 × 温层」预测，按天汇总算 WMAPE。

契约
----
候选程序需提供：

    def predict(history_df, target_df) -> 预测数组

- `history_df`：验证段之前的全部实际值，列为
  `delivery_date`(datetime)、`delivery_temperature`(str)、`outbound_total_box_qty`(float)
- `target_df`：待预测的 `delivery_date`、`delivery_temperature`，**不含任何实际值**
- 返回值：与 `target_df` 等长的数值数组（允许负值，评估时会被截到 0）

评测：
- 验证段 = 最后 `OUTBOUND_VAL_DAYS`（默认 90）天，其余为历史
- 按天汇总预测与实际，WMAPE = Σ|实际-预测| / Σ实际
- stdout 只输出一个 JSON 对象
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import traceback

import numpy as np
import pandas as pd

DEFAULT_CSV = "/home/user/Documents/study/program-codes/开源项目/openevolve/solver/datasets/outbound_atomic.csv"
CSV_PATH = os.environ.get("OUTBOUND_ATOMIC_CSV", DEFAULT_CSV)
VAL_DAYS = int(os.environ.get("OUTBOUND_VAL_DAYS", "90"))
MIN_TRAIN_DAYS = int(os.environ.get("OUTBOUND_MIN_TRAIN_DAYS", "120"))

# 候选不得绕过评估：禁止读文件/网络/起子进程
FORBIDDEN = re.compile(
    r"read_csv|read_excel|read_parquet|open\s*\(|urllib|requests|httpx|socket|subprocess|os\.system",
    re.IGNORECASE,
)

FAIL_SCORE = {"wmape": 9.99, "score": -999.0}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    sys.exit(0)


def _fail(message: str) -> None:
    payload = dict(FAIL_SCORE)
    payload["error"] = message[:500]
    _emit(payload)


def load_frame() -> pd.DataFrame:
    if not os.path.isfile(CSV_PATH):
        _fail(f"数据文件不存在：{CSV_PATH}")
    frame = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    frame["delivery_date"] = pd.to_datetime(frame["delivery_date"])
    frame["outbound_total_box_qty"] = pd.to_numeric(
        frame["outbound_total_box_qty"], errors="coerce"
    ).fillna(0.0)
    frame["delivery_temperature"] = frame["delivery_temperature"].astype(str)
    grouped = (
        frame.groupby(["delivery_date", "delivery_temperature"], as_index=False)["outbound_total_box_qty"]
        .sum()
        .sort_values(["delivery_date", "delivery_temperature"])
        .reset_index(drop=True)
    )
    if grouped.empty:
        _fail("数据为空")
    return grouped


def split_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = np.sort(frame["delivery_date"].unique())
    if len(dates) < MIN_TRAIN_DAYS + VAL_DAYS:
        _fail(f"可用天数不足：{len(dates)} < {MIN_TRAIN_DAYS + VAL_DAYS}")
    split_at = dates[-VAL_DAYS]
    history = frame[frame["delivery_date"] < split_at].copy()
    target = frame[frame["delivery_date"] >= split_at].copy()
    if history["delivery_date"].nunique() < MIN_TRAIN_DAYS:
        _fail(f"训练段天数不足：{history['delivery_date'].nunique()}")
    return history.reset_index(drop=True), target.reset_index(drop=True)


def score(val: pd.DataFrame, pred) -> dict:
    actual = val["outbound_total_box_qty"].to_numpy(dtype=float)
    try:
        arr = np.asarray(pred, dtype=float).reshape(-1)
    except (TypeError, ValueError) as error:
        _fail(f"预测结果无法转成数值数组：{error}")
    if arr.shape[0] != actual.shape[0]:
        _fail(f"预测长度 {arr.shape[0]} 与实际 {actual.shape[0]} 不一致")
    if not np.isfinite(arr).all():
        _fail("预测结果含非有限值")

    arr = np.clip(arr, 0.0, None)
    joined = val.assign(pred=arr)
    daily = joined.groupby("delivery_date")[["outbound_total_box_qty", "pred"]].sum()
    total_actual = float(daily["outbound_total_box_qty"].sum())
    if total_actual <= 0:
        _fail("实际总量为 0，无法计算 WMAPE")

    abs_err = (daily["outbound_total_box_qty"] - daily["pred"]).abs()
    wmape_total = float(abs_err.sum() / total_actual)
    daily_wmape = (abs_err / daily["outbound_total_box_qty"].replace(0, np.nan)).dropna()

    return {
        "wmape": wmape_total,
        "wmape_daily_mean": float(daily_wmape.mean()) if len(daily_wmape) else wmape_total,
        "wmape_daily_std": float(daily_wmape.std()) if len(daily_wmape) > 1 else 0.0,
        "bias_ratio": float((daily["pred"].sum() - total_actual) / total_actual),
        "val_days": float(len(daily)),
    }


def main() -> None:
    if len(sys.argv) < 2:
        _fail("用法：evaluator.py <候选程序路径>")
    program_path = os.path.abspath(sys.argv[1])

    try:
        source = open(program_path, encoding="utf-8").read()
    except OSError as error:
        _fail(f"无法读取候选程序：{error}")
    if FORBIDDEN.search(source):
        _fail("候选程序出现文件/网络/子进程访问，评估只允许纯计算")

    try:
        spec = importlib.util.spec_from_file_location("candidate_program", program_path)
        if spec is None or spec.loader is None:
            _fail("无法加载候选程序")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        predict = getattr(module, "predict", None)
        if not callable(predict):
            _fail("候选程序缺少 predict(history_df, target_df) 函数")

        frame = load_frame()
        history, target = split_frame(frame)
        target_input = target[["delivery_date", "delivery_temperature"]].copy()

        raw = predict(history.copy(), target_input.copy())
        metrics = score(target, raw)
        metrics["history_days"] = float(history["delivery_date"].nunique())
        metrics["target_rows"] = float(len(target_input))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        _fail("候选程序执行异常：" + traceback.format_exc(limit=3).replace("\n", " | "))

    metrics["score"] = -metrics["wmape"]
    _emit(metrics)


if __name__ == "__main__":
    main()
