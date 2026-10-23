"""通用参数调优示例：动量梯度下降求解病态二次函数。

规则（由 template.yml 的 validation 强制校验，违反者直接判 0 分）：
    1. 只允许修改 PARAMS 中现有键对应的值，不允许新增、删除或改名。
    2. 不允许 import、函数定义、类定义或任何 I/O。
    3. 每个参数的合法范围见下方注释，与 template.yml 中的 ranges 对应。

真正被评测的是固定的优化过程（runner_adapter.py），本文件只是一组配置。
"""

# 当前解：默认值 = 手工基线
# EVOLVE-BLOCK-START
PARAMS = {
    "step_size": 0.002,  # [0.0001, 0.02]  步长；问题条件数为 100，过大直接发散
    "momentum": 0.50,    # [0.0, 0.99]     动量系数，会放大有效步长 1/(1-m)
    "restarts": 2,       # [1, 20]         重启次数，每次从新的随机点出发
}
# EVOLVE-BLOCK-END
