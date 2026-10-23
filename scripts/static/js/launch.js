const form = document.getElementById("run-form");
const startButton = document.getElementById("start-button");
const statusBadge = document.getElementById("status-badge");
const statusMessage = document.getElementById("status-message");
const logElement = document.getElementById("run-log");
const visualizerLink = document.getElementById("open-visualizer");
const importButton = document.getElementById("import-button");
const importMessage = document.getElementById("import-message");

let pollTimer = null;

importButton.addEventListener("click", async () => {
  const experimentFile = document.getElementById("experiment-file").files[0];
  const programFile = document.getElementById("program-file").files[0];
  if (!experimentFile || !programFile) {
    importMessage.textContent = "请同时选择实验 YAML 文件和初始程序 .py 文件。";
    importMessage.className = "import-message error";
    return;
  }

  const formData = new FormData();
  formData.append("experiment", experimentFile);
  formData.append("program", programFile);
  importButton.disabled = true;
  importMessage.textContent = "正在读取并转换实验文件…";
  importMessage.className = "import-message";

  try {
    const response = await fetch("/api/import-experiment", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: formData,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `导入失败（${response.status}）`);

    document.getElementById("initial-program").value = result.initial_program;
    document.getElementById("config").value = result.config;
    document.getElementById("iterations").value = result.iterations;
    document.getElementById("evaluator").value = "";

    const name = result.experiment_name ? `“${result.experiment_name}”已导入。` : "实验配置已导入。";
    const evaluatorNote = result.evaluator_reference
      ? ` YAML 中的 evaluator 是“${result.evaluator_reference}”路径，不是评测器源码。`
      : " 实验文件没有提供 evaluator。";
    importMessage.textContent =
      `${name} 已填入初始代码、任务描述和 ${result.iterations} 轮。${evaluatorNote} 请粘贴实际评测器代码后再启动。`;
    importMessage.className = "import-message warning";
  } catch (error) {
    importMessage.textContent = error.message;
    importMessage.className = "import-message error";
  } finally {
    importButton.disabled = false;
  }
});

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
