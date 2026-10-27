"""演化结束后的运行报告生成器。

在 ``output/report/`` 下产出三份文件：

- ``report.json``   结构化摘要，GUI / 其他工具读取
- ``report.md``     Markdown 报告，便于贴进文档或 PR
- ``report.html``   自包含静态页面（无外部依赖），便于分享与离线查看

设计约束：这里只读数据库与配置、只写报告目录，任何异常都不应影响演化主流程
（调用方另有一层 try/except，本模块内部也尽量降级而不是抛错）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from html import escape
from typing import Any, Optional

# 主指标：优先取这几个约定名，取不到再退化为第一个数值指标
_PRIMARY_METRIC_CANDIDATES = ("combined_score", "score", "fitness", "objective", "value")


def _numeric_metrics(metrics: Any) -> dict[str, float]:
    if not isinstance(metrics, dict):
        return {}
    result: dict[str, float] = {}
    for name, value in metrics.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            result[str(name)] = float(value)
    return result


def _pick_primary_metric(metrics: dict[str, float]) -> str:
    for name in _PRIMARY_METRIC_CANDIDATES:
        if name in metrics:
            return name
    return next(iter(metrics), "")


def _sort_value(metrics: Any) -> float:
    numeric = _numeric_metrics(metrics)
    primary = _pick_primary_metric(numeric)
    return numeric.get(primary, 0.0)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.6g}"
    return str(value)


def collect_summary(
    output_dir: str,
    database: Any,
    config: Any = None,
    best_program: Any = None,
    started_at: Optional[float] = None,
) -> dict[str, Any]:
    """把一次演化运行的结论整理成可序列化的摘要。"""
    programs = list(getattr(database, "programs", {}).values())
    islands = [set(item) for item in getattr(database, "islands", []) or []]

    best_metrics = _numeric_metrics(getattr(best_program, "metrics", None))
    primary = _pick_primary_metric(best_metrics)
    best_score = best_metrics.get(primary) if primary else None

    # 按发现顺序还原 best-so-far 曲线
    ordered = sorted(
        programs,
        key=lambda p: (getattr(p, "iteration_found", 0) or 0, getattr(p, "generation", 0) or 0),
    )
    curve: list[dict[str, Any]] = []
    running_best: Optional[float] = None
    for program in ordered:
        metrics = _numeric_metrics(getattr(program, "metrics", None))
        value = metrics.get(primary) if primary else None
        if value is None:
            continue
        if running_best is None or value > running_best:
            running_best = value
        curve.append(
            {
                "iteration": getattr(program, "iteration_found", 0) or 0,
                "generation": getattr(program, "generation", 0) or 0,
                "score": value,
                "best_so_far": running_best,
            }
        )

    # 初始基线：最早一个有效程序，用来算提升幅度
    baseline_score = None
    for program in ordered:
        metrics = _numeric_metrics(getattr(program, "metrics", None))
        if primary and metrics.get(primary) is not None:
            baseline_score = metrics[primary]
            break
    improvement = None
    if baseline_score not in (None, 0) and best_score is not None:
        improvement = (best_score - baseline_score) / abs(baseline_score) * 100.0

    # 岛屿分布
    program_index = {getattr(p, "id", ""): p for p in programs}
    island_rows = []
    for index, members in enumerate(islands):
        scores = []
        for program_id in members:
            program = program_index.get(program_id)
            if program is None:
                continue
            metrics = _numeric_metrics(getattr(program, "metrics", None))
            if primary and metrics.get(primary) is not None:
                scores.append(metrics[primary])
        island_rows.append(
            {
                "index": index,
                "programs": len(members),
                "best_score": max(scores) if scores else None,
            }
        )

    elapsed = None
    if started_at:
        elapsed = max(0.0, datetime.now().timestamp() - started_at)

    # 任务名取上级目录名（evolve_tasks/<run_id>/output -> <run_id>），
    # 直接用 output_dir 的 basename 会得到无意义的 "output"
    normalized = os.path.normpath(output_dir)
    parent_name = os.path.basename(os.path.dirname(normalized))
    run_name = parent_name or os.path.basename(normalized) or "run"

    return {
        "run_name": run_name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "primary_metric": primary,
        "best_program": {
            "id": getattr(best_program, "id", None),
            "generation": getattr(best_program, "generation", None),
            "iteration": getattr(best_program, "iteration_found", None),
            "metrics": best_metrics,
        }
        if best_program is not None
        else None,
        "best_score": best_score,
        "baseline_score": baseline_score,
        "improvement_percent": improvement,
        "total_programs": len(programs),
        "islands": island_rows,
        "curve": curve,
        "elapsed_seconds": elapsed,
        "config": {
            "max_iterations": getattr(config, "max_iterations", None),
            "population_size": getattr(config, "population_size", None),
            "num_islands": getattr(config, "num_islands", None),
        },
    }


def render_markdown(summary: dict[str, Any], best_code: str = "") -> str:
    primary = summary.get("primary_metric") or "score"
    best = summary.get("best_program") or {}
    lines = [
        f"# 演化报告 · {summary['run_name']}",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 主指标：`{primary}`",
        f"- 程序总数：{summary['total_programs']}",
        "",
        "## 一、结论",
        "",
    ]
    if best:
        lines.append(f"- 最佳分数：**{_fmt(summary.get('best_score'))}**")
        lines.append(f"- 最佳程序：`{best.get('id')}`（第 {best.get('generation')} 代 / 第 {best.get('iteration')} 轮）")
    else:
        lines.append("- 本次运行没有产出有效程序（所有迭代均失败或被过滤）。")
    if summary.get("baseline_score") is not None:
        lines.append(f"- 初始基线：{_fmt(summary['baseline_score'])}")
    if summary.get("improvement_percent") is not None:
        lines.append(f"- 相对提升：{summary['improvement_percent']:+.2f}%")

    if best.get("metrics"):
        lines += ["", "## 二、最佳程序指标", "", "| 指标 | 值 |", "| --- | --- |"]
        for name, value in best["metrics"].items():
            lines.append(f"| `{name}` | {_fmt(value)} |")

    curve = summary.get("curve") or []
    if curve:
        lines += ["", "## 三、收敛过程", "", "| 轮次 | 代 | 本轮分数 | 历史最佳 |", "| --- | --- | --- | --- |"]
        step = max(1, len(curve) // 20)
        for point in curve[::step]:
            lines.append(
                f"| {point['iteration']} | {point['generation']} | "
                f"{_fmt(point['score'])} | {_fmt(point['best_so_far'])} |"
            )
        if len(curve) > 20:
            lines.append(f"| … | | | |")
            lines.append(
                f"| {curve[-1]['iteration']} | {curve[-1]['generation']} | "
                f"{_fmt(curve[-1]['score'])} | {_fmt(curve[-1]['best_so_far'])} |"
            )

    islands = summary.get("islands") or []
    if islands:
        lines += ["", "## 四、岛屿分布", "", "| 岛屿 | 程序数 | 最佳分 |", "| --- | --- | --- |"]
        for row in islands:
            lines.append(
                f"| #{row['index']} | {row['programs']} | {_fmt(row['best_score'])} |"
            )

    if best_code:
        lines += ["", "## 五、最佳程序代码", "", "```python", best_code.rstrip(), "```", ""]

    return "\n".join(lines) + "\n"


def _svg_curve(curve: list[dict[str, Any]], width: int = 760, height: int = 240) -> str:
    """把 best-so-far 曲线画成内联 SVG（无 JS，静态可读）。"""
    points = [row for row in curve if row.get("best_so_far") is not None]
    if len(points) < 2:
        return '<p class="muted">迭代次数不足，暂无收敛曲线。</p>'

    pad_l, pad_r, pad_t, pad_b = 52, 16, 16, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    xs = [row["iteration"] for row in points]
    ys = [row["best_so_far"] for row in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max == x_min:
        x_max = x_min + 1
    if y_max == y_min:
        y_max = y_min + 1

    def px(x: float) -> float:
        return pad_l + (x - x_min) / (x_max - x_min) * plot_w

    def py(y: float) -> float:
        return pad_t + (1 - (y - y_min) / (y_max - y_min)) * plot_h

    poly = " ".join(f"{px(row['iteration']):.1f},{py(row['best_so_far']):.1f}" for row in points)
    area = f"{pad_l},{pad_t + plot_h} " + poly + f" {pad_l + plot_w},{pad_t + plot_h}"
    ticks = "".join(
        f'<text x="{pad_l - 8}" y="{py(v) + 4:.1f}" text-anchor="end">{_fmt(v)}</text>'
        for v in (y_min, (y_min + y_max) / 2, y_max)
    )
    return f"""<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="收敛曲线">
  <polygon points="{area}" class="chart-area"/>
  <polyline points="{poly}" class="chart-line"/>
  {ticks}
  <text x="{pad_l}" y="{height - 8}" class="axis">迭代 0</text>
  <text x="{pad_l + plot_w}" y="{height - 8}" class="axis" text-anchor="end">迭代 {x_max}</text>
</svg>"""


def render_html(summary: dict[str, Any], best_code: str = "") -> str:
    primary = escape(str(summary.get("primary_metric") or "score"))
    best = summary.get("best_program") or {}
    improvement = summary.get("improvement_percent")
    improvement_text = f"{improvement:+.2f}%" if improvement is not None else "—"
    island_rows = "".join(
        f"<tr><td>#{row['index']}</td><td>{row['programs']}</td>"
        f"<td>{escape(_fmt(row['best_score']))}</td></tr>"
        for row in summary.get("islands") or []
    )
    metric_rows = "".join(
        f"<tr><td><code>{escape(str(name))}</code></td><td>{escape(_fmt(value))}</td></tr>"
        for name, value in (best.get("metrics") or {}).items()
    )
    title = escape(f"演化报告 · {summary['run_name']}")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:32px 20px 64px; background:#070b14; color:#e6ecf7;
         font:14px/1.65 -apple-system, "PingFang SC", "Noto Sans SC", sans-serif; }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  h1 {{ margin:0 0 6px; font-size:24px; letter-spacing:-.01em; }}
  .sub {{ color:#7f8ea8; font-size:12px; margin-bottom:24px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:22px; }}
  .card {{ padding:14px 16px; border:1px solid #19233a; border-radius:12px; background:#0c1322; }}
  .card .k {{ color:#7f8ea8; font-size:11px; letter-spacing:.06em; text-transform:uppercase; }}
  .card .v {{ margin-top:6px; font-size:20px; font-weight:650; color:#00c8ff; font-variant-numeric:tabular-nums; }}
  section {{ margin-bottom:22px; padding:18px 20px; border:1px solid #19233a; border-radius:14px; background:#0c1322; }}
  h2 {{ margin:0 0 12px; font-size:14px; color:#9fb0c9; font-weight:600; letter-spacing:.04em; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ padding:7px 10px; text-align:left; border-bottom:1px solid #161f33; }}
  th {{ color:#7f8ea8; font-weight:500; font-size:11px; letter-spacing:.05em; }}
  td:last-child, th:last-child {{ text-align:right; font-variant-numeric:tabular-nums; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; color:#8fd6ff; }}
  pre {{ margin:0; padding:14px 16px; overflow:auto; border-radius:10px; background:#080e1a;
         border:1px solid #161f33; font-family: ui-monospace, Menlo, monospace; font-size:12px; line-height:1.6; }}
  .chart {{ width:100%; height:auto; }}
  .chart-line {{ fill:none; stroke:#00c8ff; stroke-width:2; }}
  .chart-area {{ fill:#00c8ff14; stroke:none; }}
  text {{ fill:#7f8ea8; font-size:10px; }}
  .muted {{ color:#7f8ea8; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; background:#00ff881a;
            color:#00ff88; font-size:11px; font-weight:600; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{title}</h1>
  <p class="sub">生成于 {escape(summary['generated_at'])} · 主指标 <code>{primary}</code>
     · 程序总数 {summary['total_programs']}</p>

  <div class="cards">
    <div class="card"><div class="k">最佳分数</div><div class="v">{escape(_fmt(summary.get('best_score')))}</div></div>
    <div class="card"><div class="k">相对提升</div><div class="v">{improvement_text}</div></div>
    <div class="card"><div class="k">最佳程序代次</div><div class="v">{escape(str(best.get('generation', '—')))}</div></div>
    <div class="card"><div class="k">岛屿数</div><div class="v">{len(summary.get('islands') or [])}</div></div>
  </div>

  <section>
    <h2>收敛曲线（best-so-far）</h2>
    {_svg_curve(summary.get('curve') or [])}
  </section>

  <section>
    <h2>最佳程序指标</h2>
    {f'<table><thead><tr><th>指标</th><th>值</th></tr></thead><tbody>{metric_rows}</tbody></table>' if metric_rows else '<p class="muted">没有可用指标。</p>'}
  </section>

  <section>
    <h2>岛屿分布</h2>
    {f'<table><thead><tr><th>岛屿</th><th>程序数</th><th>最佳分</th></tr></thead><tbody>{island_rows}</tbody></table>' if island_rows else '<p class="muted">没有岛屿数据。</p>'}
  </section>

  <section>
    <h2>最佳程序代码 <span class="badge">best_program</span></h2>
    <pre>{escape(best_code.rstrip()) if best_code else '<span class="muted">无</span>'}</pre>
  </section>
</div>
</body>
</html>
"""


def generate_run_report(
    output_dir: str,
    database: Any,
    config: Any = None,
    best_program: Any = None,
    started_at: Optional[float] = None,
) -> dict[str, str]:
    """在 ``<output_dir>/report/`` 下生成 json / md / html 三份报告。"""
    report_dir = os.path.join(output_dir, "report")
    os.makedirs(report_dir, exist_ok=True)

    summary = collect_summary(output_dir, database, config, best_program, started_at)
    best_code = getattr(best_program, "code", "") or ""

    json_path = os.path.join(report_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    md_path = os.path.join(report_dir, "report.md")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(summary, best_code))

    html_path = os.path.join(report_dir, "report.html")
    with open(html_path, "w", encoding="utf-8") as handle:
        handle.write(render_html(summary, best_code))

    return {"json": json_path, "markdown": md_path, "html": html_path}
