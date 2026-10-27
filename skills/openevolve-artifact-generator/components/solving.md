# Component — Deep Solving

**Input**: `evaluator.py`, `init.py`, `prompt.md`
**Output**: Best `init.py` + score

---

## OpenEvolve Deep Solving

Use the `openevolve-experiment-manager` skill and follow its complete workflow.

Do not manually generate `config.yaml` in this step. Experiment submission and task management must be handled through the `openevolve-experiment-manager` skill.

The three core inputs for the OpenEvolve task:

```
experiment/
├── evaluator.py     ← fixed scoring interface (READONLY)
├── init.py          ← this is what OpenEvolve evolves
└── prompt.md        ← guides LLM mutation
```

Prepare these artifacts, then invoke the `openevolve-experiment-manager` skill to submit the experiment. Let that skill decide how the experiment should be packaged and submitted.

After submission, inform the user:

*"Experiment submitted — OpenEvolve jobs can take a while. I'll check status periodically and run a read-only diagnosis every 20 evolution rounds by default; feel free to do other things. You can also ask me to diagnose the experiment at any time."*

If the user requests diagnosis, invoke the `openevolve-experiment-diagnosis` skill with the current `run_id` and run directory. Diagnosis is read-only and must not interrupt or modify the running experiment.

**Status polling** — use the `openevolve-experiment-manager` skill to query status every 20 seconds until the job completes. Report progress at each check:

```
⏳ [HH:MM] Experiment <id> — status: running | iterations: 42/200 | best_score: 0.1234
```

**Periodic diagnosis** — during status polling, invoke the `openevolve-experiment-diagnosis` skill once every 20 completed evolution rounds by default, using the current `run_id` and run directory. Continue status polling after reporting the diagnosis.

Once complete, use the `openevolve-experiment-manager` skill to retrieve results and proceed to the Result Report.

---

## Result Report

演化结束（成功或无有效程序）时，后端会自动在 `output/report/` 下生成
`report.json` / `report.md` / `report.html`。取数优先读 `report.json`
（`best_score`、`baseline_score`、`improvement_percent`、`curve`、`islands`），不要自己重算。
GUI 的任务详情页有「运行报告」卡片，可直接打开 `report.html` 给用户看。

Upon completion, display:

```
📊 Results
─────────────────────────────────────
Engine:         OpenEvolve
Baseline score: {init_score:.6f}
Best score:     {best_score:.6f}
Improvement:    {improvement:+.2f}%
─────────────────────────────────────
Best solution:  init.py
```
