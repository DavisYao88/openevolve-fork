const form = document.getElementById("run-form");
const startButton = document.getElementById("start-button");
const statusBadge = document.getElementById("status-badge");
const statusMessage = document.getElementById("status-message");
const logElement = document.getElementById("run-log");
const visualizerLink = document.getElementById("open-visualizer");

let pollTimer = null;

function showRun(run) {
  if (!run) return;

  const statusLabels = {
    running: "运行中",
    completed: "已完成",
    failed: "失败",
  };
  statusBadge.textContent = statusLabels[run.status] || run.status;
  statusBadge.className = `badge ${run.status}`;
  statusMessage.textContent = `输出目录：${run.output_dir}`;
  logElement.textContent = run.log || "";
  logElement.scrollTop = logElement.scrollHeight;
  visualizerLink.classList.remove("hidden");
  startButton.disabled = run.status === "running";

  if (run.status === "running" && pollTimer === null) {
    pollTimer = window.setInterval(loadRunStatus, 2000);
  } else if (run.status !== "running" && pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function loadRunStatus() {
  try {
    const response = await fetch("/api/run", { cache: "no-store" });
    const payload = await response.json();
    showRun(payload.run);
  } catch (error) {
    statusMessage.textContent = `无法获取运行状态：${error.message}`;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  startButton.disabled = true;
  statusBadge.textContent = "正在启动";
  statusBadge.className = "badge running";
  statusMessage.textContent = "正在启动进化任务…";
  logElement.textContent = "";

  const payload = {
    initial_program: document.getElementById("initial-program").value,
    evaluator: document.getElementById("evaluator").value,
    config: document.getElementById("config").value,
    iterations: Number(document.getElementById("iterations").value),
    evaluator_args: document.getElementById("evaluator-args").value,
  };

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `请求失败（${response.status}）`);
    showRun(result.run);
  } catch (error) {
    startButton.disabled = false;
    statusBadge.textContent = "启动失败";
    statusBadge.className = "badge failed";
    statusMessage.textContent = error.message;
  }
});

loadRunStatus();
