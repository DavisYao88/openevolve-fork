async function request(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  if (!response.ok) {
    const body = await response.text();
    let message = body;
    try {
      message = JSON.parse(body).error || body;
    } catch {
      // Keep non-JSON server errors readable.
    }
    throw new Error(message || `请求失败（${response.status}）`);
  }
  return response.json();
}

export const api = {
  evolution(runId = "") {
    const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
    return request(`/api/data${query}`);
  },
  runs() {
    return request("/api/runs");
  },
  runDetail(runId) {
    return request(`/api/runs/${encodeURIComponent(runId)}`);
  },
  runProgress(runId) {
    return request(`/api/runs/${encodeURIComponent(runId)}/progress`);
  },
  runLog(runId, offset = 0) {
    return request(`/api/runs/${encodeURIComponent(runId)}/log?offset=${offset}`);
  },
  launchDefaults() {
    return request("/api/launch");
  },
  currentRun() {
    return request("/api/run");
  },
  startRun(payload) {
    return request("/api/run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    });
  },
  manualTasks() {
    return request("/manual/api/tasks");
  },
  manualTask(taskId) {
    return request(`/manual/api/tasks/${encodeURIComponent(taskId)}`);
  },
  answerManualTask(taskId, answer) {
    const body = new FormData();
    body.append("answer", answer);
    return request(`/manual/api/tasks/${encodeURIComponent(taskId)}/answer`, {
      method: "POST",
      body,
    });
  },
};
