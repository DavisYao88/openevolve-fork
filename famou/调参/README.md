# famou 参数调优（OpenEvolve）

## 输入文件

- `init.py`：种子程序。OpenEvolve 只允许演化 `PARAMS` 中的值；评估器会检查键集合、字面量结构、范围和交叉约束。
- `prompt.md`：独立的调参任务说明。`config.yml` 中的 `prompt.template_dir: .` 和 `system_message: prompt` 会让 OpenEvolve 从该文件加载提示词。
- `config.yml`：OpenEvolve 原生 YAML 配置。
- `evaluator.py`：OpenEvolve 评估回调，负责校验候选参数并调用训练适配器。
- `runner_adapter.py`：页面评估器代码的命令行模板。页面提交的评测器代码会保存为本轮 `input/runner_adapter.py`，运行配置会自动将其设为 `runner_command`。

在仓库可视化服务的“新建进化”页面中，默认读取 `init.py`、`runner_adapter.py` 和 `prompt.md` 作为表单默认值；运行时用户只需填写或修改这三项以及进化轮数。页面不提供文件导入，也不显示 YAML 配置。页面使用的隐藏全局运行配置位于 `evolve_tasks/global_config.yml`（模型、数据库、评测超时等）；服务读取它后，为每轮生成独立配置副本，自动将本轮 `runner_adapter.py` 的路径和候选 `{program_path}` 写入 `evaluator.evaluator_args.runner_command`，并把提示词模板动态设置为 `template_dir: .`、`system_message: prompt`。页面访问和任务启动都不会写回本目录；输入副本、生成配置和结果写到 `evolve_tasks/.../input/` 与 `output/`。命令行直接运行示例时仍可使用本目录中的 `config.yml`。

安装 OpenEvolve 后，在仓库根目录运行：

```bash
export OPENAI_API_KEY="你的 OpenAI-compatible API key"
python openevolve-run.py \
  "famou/调参/init.py" \
  "famou/调参/evaluator.py" \
  --config "famou/调参/config.yml" \
  --iterations 50 \
  --output "famou/调参/openevolve_output"
```

`config.yml` 当前使用 Gemini 的 OpenAI-compatible endpoint。使用其他模型时，请按 OpenEvolve 的配置方式调整 `llm` 设置。

## 评测器代码接口

页面中的评测器代码由 OpenEvolve 作为独立命令运行，并接收候选 `init.py` 路径作为唯一位置参数。它必须调用真实固定训练链路并在标准输出中仅打印一个 JSON 对象，例如：

```python
import json
import sys

candidate_init = sys.argv[1]
# 使用 candidate_init 中的 PARAMS 调用真实训练与评分流程。
print(json.dumps({"wmape": wmape, "bias_ratio": bias_ratio}))
```

`wmape` 必须是正的有限数值；`bias_ratio` 用比例而不是百分数表示，例如 5% 写成 `0.05`。日志写入标准错误。仓库没有 `app.pipelines.train_pipeline.run_train` 或生产数仓代码，因此 `runner_adapter.py` 只是明确报错的接口模板，不会伪造训练结果；请在页面评测器编辑框里填入真实训练适配器代码。

评估分数为 `-log(wmape) * 100 - max(0, abs(bias_ratio) - 0.05) * 100`。参数校验失败、训练命令失败、输出无效或超时都会返回 0 分并附带错误产物。

真实适配器未实现时，评估会失败，不要把失败结果用于调参结论。
