# famou 参数调优任务（OpenEvolve 公共演化求解器模板）

## 输入文件

- `init.py`：种子程序。求解器只允许演化 `PARAMS` 中的值；`template.yml` 的
  `validation` 段会检查键集合、字面量结构、取值范围和交叉约束。
- `prompt.md`：独立的调参任务说明，启动时复制为本轮 `input/prompt.md`，
  运行配置自动设为 `template_dir: .`、`system_message: prompt`。
- `runner_adapter.py`：评测命令（页面"评测脚本"编辑框）的模板。它接收候选
  `init.py` 路径作为唯一位置参数，调用真实训练链路，并向标准输出打印一个
  JSON 对象，例如 `{"wmape": 0.12, "bias_ratio": -0.02}`。
- `template.yml`：模板元数据。`objective` 决定怎么算分，`validation` 决定
  候选程序允许怎么改，两者都会写入本轮 `config.yaml` 的
  `evaluator.evaluator_args`。

评估器本身不在这里：`solver/evaluator.py` 是公共评估器，启动时复制为本轮
`input/evaluator_callback.py`。

在仓库可视化服务（或 Vue 前端）的"新建进化"页面中选择本模板即可运行，页面会
自动把 `template.yml` 的 `objective` / `validation` 注入本轮配置。命令行运行
时这两项要通过 `--evaluator-args` 传入：

```bash
export OPENAI_API_KEY="你的 OpenAI-compatible API key"
python openevolve-run.py \
  "solver/templates/famou-调参/init.py" \
  "solver/evaluator.py" \
  --config "evolve_tasks/global_config.yml" \
  --evaluator-args '{"runner_command": ["python", "solver/templates/famou-调参/runner_adapter.py", "{program_path}"], "objective": {"metrics": [{"name": "wmape", "direction": "minimize", "transform": "log", "scale": 100}, {"name": "bias_ratio", "direction": "minimize", "threshold": 0.05, "scale": 100}]}, "validation": {"mode": "params_dict", "variable": "PARAMS"}}' \
  --iterations 50 \
  --output "solver/templates/famou-调参/openevolve_output"
```

## 评分

`objective` 配置等价于 `-log(wmape) * 100 - max(0, |bias_ratio| - 0.05) * 100`；
`combined_score` 越大越好。`bias_ratio` 用比例而不是百分数（5% 写成 `0.05`）。
参数校验失败、评测命令失败、输出无效或超时都会返回 0 分并附带错误产物。

## 评测脚本接口

页面里的评测脚本由求解器作为独立命令运行，不使用 shell：

```python
import json
import sys

candidate_init = sys.argv[1]
# 用 candidate_init 中的 PARAMS 跑真实训练与评分流程。
print(json.dumps({"wmape": wmape, "bias_ratio": bias_ratio}))
```

`wmape` 必须是正的有限数值。日志写标准错误，标准输出只放最后的 JSON。
仓库没有 `app.pipelines.train_pipeline.run_train` 或生产数仓代码，因此
`runner_adapter.py` 只是明确报错的接口模板，不会伪造训练结果；请在页面评测
脚本编辑框里填入真实训练适配器代码。真实适配器未实现时评估会失败，不要把
失败结果用于调参结论。
