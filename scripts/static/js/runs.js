const RUN_STORAGE_KEY = "openevolve.selectedRunId";

const STATUS_LABELS = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  interrupted: "已中断",
  unknown: "未知",
};

const refreshButton = document.getElementById("runs-refresh");
const summaryElement = document.getElementById("runs-summary");
const errorElement = document.getElementById("runs-error");
const emptyElement = document.getElementById("runs-empty");
const tableWrap = document.getElementById("runs-table-wrap");
const bodyElement = document.getElementById("runs-body");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatMetrics(metrics) {
  if (!metrics || typeof metrics !== "object") return '<span class="muted">—</span>';
  const parts = Object.entries(metrics)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .map(([key, value]) => `${escapeHtml(key)}=${value.toFixed(4)}`);
  return parts.length ? parts.join("<br/>") : '<span class="muted">—</span>';
}

function renderRun(run) {
  const status = STATUS_LABELS[run.status] || run.status;
  const visualizerHref = `/?run_id=${encodeURIComponent(run.id)}`;
  const actions = run.has_checkpoint
    ? `<a href="${visualizerHref}">查看进化</a>`
    : '<span class="muted">暂无 checkpoint</span>';
  const rounds = run.iterations ? `${run.programs}/${run.iterations}` : String(run.programs || 0);

  return `
    <tr>
      <td class="run-id">${escapeHtml(run.id)}</td>
      <td><span class="badge ${escapeHtml(run.status)}">${escapeHtml(status)}</span></td>
      <td>${run.model ? escapeHtml(run.model) : '<span class="muted">—</span>'}</td>
      <td>${escapeHtml(rounds)}</td>
      <td>${escapeHtml(String(run.programs || 0))}</td>
      <td class="run-metrics">${formatMetrics(run.best_metrics)}</td>
      <td>${run.updated_at ? escapeHtml(run.updated_at) : '<span class="muted">—</span>'}</td>
      <td class="run-actions">${actions}</td>
    </tr>
  `;
}

async function loadRuns() {
  refreshButton.disabled = true;
  errorElement.classList.add("hidden");
  try {
    const response = await fetch("/api/runs", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `读取任务列表失败（${response.status}）`);

    const runs = Array.isArray(payload.runs) ? payload.runs : [];
    bodyElement.innerHTML = runs.map(renderRun).join("");
    summaryElement.textContent = `共 ${runs.length} 个任务`;
    tableWrap.classList.toggle("hidden", runs.length === 0);
    emptyElement.classList.toggle("hidden", runs.length !== 0);
  } catch (error) {
    tableWrap.classList.add("hidden");
    emptyElement.classList.add("hidden");
    errorElement.textContent = error.message;
    errorElement.classList.remove("hidden");
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", loadRuns);
loadRuns();

export { RUN_STORAGE_KEY };
