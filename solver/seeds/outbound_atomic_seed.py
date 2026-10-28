"""仓店出库量预测器（演化种子）。

任务：给定历史实际出库箱数，预测验证段每一天、每个温层的出库量。
评估会先把预测和实际都按「天」汇总，再算 WMAPE = Σ|实际-预测| / Σ实际。

允许改的范围：`predict()` 内部逻辑（可以加辅助函数、换算法、引入新特征）。
不要改函数名、参数个数和返回值约定。
"""

# EVOLVE-BLOCK-START
import numpy as np
import pandas as pd


def predict(history_df, target_df):
    """基线：每个温层用最近 14 天的日均值。

    参数
    ----
    history_df : DataFrame，列 = delivery_date(datetime), delivery_temperature(str),
                 outbound_total_box_qty(float)
    target_df  : DataFrame，列 = delivery_date(datetime), delivery_temperature(str)

    返回
    ----
    与 target_df 等长的数值数组（负值会被评估截到 0）
    """
    frame = history_df.copy()
    frame["delivery_date"] = pd.to_datetime(frame["delivery_date"])
    window_start = frame["delivery_date"].max() - pd.Timedelta(days=13)
    recent = frame[frame["delivery_date"] >= window_start]

    per_temperature = recent.groupby("delivery_temperature")["outbound_total_box_qty"].mean()
    overall_mean = float(recent["outbound_total_box_qty"].mean() or 0.0)

    temperatures = target_df["delivery_temperature"].astype(str).to_numpy()
    return np.array([float(per_temperature.get(t, overall_mean)) for t in temperatures])
# EVOLVE-BLOCK-END
