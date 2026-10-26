# 公共演化求解器

`solver/` 把 OpenEvolve 从"某一条业务调参流水线"变成**通用演化求解器**：
核心评估器不认识任何业务指标，任务差异全部由配置描述。新增一个优化任务
不需要写评估器，只需要提供一个"候选程序 → 评测命令 → 指标 JSON"的闭环。

```
候选程序 (由 LLM 演化) ──► runner_command（你的评测脚本）──► stdout: {"metric": 值}
                                                                    │
                                    objective 配置 ──► combined_score（越大越好）
```

## 目录

| 路径 | 作用 |
|------|------|
| `evaluator.py` | 公共评估器。启动时被复制为本轮 `input/evaluator_callback.py`。 |
| `templates/<模板>/template.yml` | 模板元数据：`objective`（怎么算分）+ `validation`（允许改什么）+ 输入文件名。 |
| `templates/<模板>/init.py` 等 | 种子程序、提示词、评测脚本模板。 |

## 求解契约

1. OpenEvolve 把候选程序写成文件，把绝对路径交给评估器。
2. 评估器按 `runner_command`（argv 列表，**不使用 shell**）调用评测脚本，
   其中 `{program_path}` 替换成候选程序路径。
3. 评测脚本**只向 stdout 打印一个 JSON 对象**，键是指标名：
   `{"wmape": 0.12, "bias_ratio": -0.02}`；日志一律写 stderr。
4. 评估器按 `objective` 把指标折算成 `combined_score`（越大越好），所有原始
   指标同时透传给 OpenEvolve，可直接作为 MAP-Elites 的特征维度。

## objective：怎么算分

```yaml
objective:
  metrics:
    - name: wmape            # 指标名，取自评测脚本输出的 JSON
      direction: minimize    # minimize（默认）| maximize
      transform: log         # none（默认）| log | abs
      threshold: null        # 设置后先取 max(0, |v| - threshold)
      scale: 100             # 乘数，默认 1
      weight: 1.0            # 权重，默认 1
```

单个指标贡献 `sign × weight × scale × f(v)`，`direction: minimize` 时 `sign = -1`。
多个指标按权重加权求和，所以惩罚项只要写成一条 `direction: minimize` 的指标即可，
例如 famou 模板的两条等价于 `-log(wmape) × 100 - max(0, |bias_ratio| - 0.05) × 100`。

如果评测脚本自己已经算了总分，直接返回 `combined_score`（或 `score`）并且不配置
`objective` 即可；两者都没给会直接报错，而不是静默按 0 分演化。

## validation：允许改什么

```yaml
validation:
  mode: params_dict     # none（默认）| params_dict
  variable: PARAMS      # params_dict 模式下校验的顶层字典变量
  ranges:
    step_size: {min: 0.001, max: 1.0, type: float}
    restarts: [1, 20]   # 简写等价于 {min: 1, max: 20}
  booleans: [enable_cache]
  enums: {objective: [regression, huber]}
  constraints: {lt: [[clamp_min, clamp_max]]}
```

`params_dict` 模式要求候选程序与初始程序结构完全一致（AST 去掉字面量值后逐字节
相同）、键集合与键顺序一致，只允许改值，并逐项检查范围/布尔/枚举/交叉约束。
`none` 模式不做结构校验，候选程序可以自由改写算法。

校验失败、评测命令非零退出、输出不是 JSON、指标缺失或超时，都记 0 分并把原因
作为 artifact 回传给 LLM，下一轮提示词里会出现这些错误。

## 内置模板

| 模板 | 用途 |
|------|------|
| `famou-调参-v1` | 真实业务参数调优：只改 `PARAMS`，按 WMAPE/bias 评分，评测脚本需接生产训练链路。 |
| `famou-调参-v2` | D+2 出库货量参数调优：只读评估 v2.0.0 D+2 的 LightGBM 参数。 |
| `param-tuning` | 通用参数调优骨架：自带可跑通的示例评测脚本（动量梯度下降），换成自己的链路即可。 |
| `generic-python` | 通用程序演化：自由改写 `solve()`，评测脚本只负责跑它并打印指标。 |

复制任意模板目录、改 `template.yml` 和三份输入文件就是新任务；可视化服务的
"新建进化"页面会自动发现 `solver/templates/` 下的所有模板。

## 运行

- **页面**：`python scripts/visualizer.py`，浏览器打开 `/launch`（或 Vue 前端
  `frontend/`），选模板 → 填提示词/初始程序/评测脚本/轮数 → 开始进化。
- **命令行**：见各模板 README 的 `--evaluator-args` 示例，或复制页面生成的
  `evolve_tasks/evolve_task_<时间戳>/input/config.yaml`。
