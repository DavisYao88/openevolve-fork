"""通用程序演化示例：把要优化的搜索算法写在 solve() 里。

评分契约：solve() 返回一个指标字典，例如 {"score": -0.42}。指标名要和
template.yml 的 objective.metrics 对齐；本模板默认 `score` 越大越好。
"""

import random

# 固定的评测目标：最小化这个病态二次函数。
TARGET = [1.0, -2.0, 3.0]
WEIGHTS = [1.0, 10.0, 100.0]


def objective(x):
    return sum(w * (xi - t) ** 2 for xi, t, w in zip(x, TARGET, WEIGHTS))


# EVOLVE-BLOCK-START
def solve() -> dict:
    """返回本次求解的指标。当前实现是朴素随机搜索。"""
    rng = random.Random(20261023)
    best = float("inf")
    for _ in range(300):
        x = [rng.uniform(-5.0, 5.0) for _ in TARGET]
        best = min(best, objective(x))
    return {"score": -best}
# EVOLVE-BLOCK-END
