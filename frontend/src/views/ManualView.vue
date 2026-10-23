<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { api } from "../api";

const tasks = ref([]);
const currentTask = ref(null);
const answer = ref("");
const busy = ref(false);
const loading = ref(true);
const error = ref("");
const copied = ref(false);
let timer;

async function loadTasks() {
  try {
    error.value = "";
    const response = await api.manualTasks();
    tasks.value = response.tasks || [];
  } catch (cause) {
    error.value = cause.message;
  } finally {
    loading.value = false;
  }
}

async function openTask(task) {
  try {
    currentTask.value = await api.manualTask(task.id);
    answer.value = "";
  } catch (cause) {
    error.value = cause.message;
  }
}

async function submitAnswer() {
  if (!currentTask.value || !answer.value.trim()) return;
  busy.value = true;
  error.value = "";
  try {
    await api.answerManualTask(currentTask.value.id, answer.value.trim());
    currentTask.value = null;
    answer.value = "";
    await loadTasks();
  } catch (cause) {
    error.value = cause.message;
  } finally {
    busy.value = false;
  }
}

async function copyPrompt() {
  try {
    await navigator.clipboard.writeText(currentTask.value?.display_prompt || "");
    copied.value = true;
    window.setTimeout(() => { copied.value = false; }, 1200);
  } catch (cause) {
    error.value = `复制失败：${cause.message}`;
  }
}

onMounted(() => {
  loadTasks();
  timer = window.setInterval(loadTasks, 10000);
});
onUnmounted(() => window.clearInterval(timer));
</script>

<template>
  <section>
    <div class="page-heading">
      <div><p class="eyebrow">Human in the Loop</p><h1>人工反馈</h1><p>查看等待人工判断的模型提示词，并提交反馈。</p></div>
      <button class="button" @click="loadTasks">↻ 刷新任务</button>
    </div>
    <p v-if="error" class="message error">{{ error }}</p>
    <div v-if="loading" class="card empty-state">正在读取反馈队列…</div>
    <div v-else-if="!tasks.length" class="card empty-state">
      <div class="empty-icon">✓</div><strong>暂无待处理反馈</strong><p>新的人工反馈任务出现后会显示在这里。</p>
    </div>
    <div v-else class="task-list">
      <article v-for="task in tasks" :key="task.id" class="card task-card">
        <div class="task-icon">✳</div>
        <div class="task-main">
          <div class="task-title-row"><strong>{{ task.model || "未知模型" }}</strong><span class="pending-badge">待反馈</span></div>
          <p class="task-id mono">{{ task.id }}</p>
          <small>{{ task.created_at || "创建时间未知" }}</small>
        </div>
        <button class="button primary" @click="openTask(task)">填写反馈 <span>→</span></button>
      </article>
    </div>

    <div v-if="currentTask" class="modal-backdrop" @click.self="currentTask = null">
      <section class="feedback-modal" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
        <div class="modal-title">
          <div><p class="eyebrow">Feedback Task</p><h2 id="feedback-title">查看提示词并提交回答</h2><small class="mono">{{ currentTask.id }}</small></div>
          <button class="icon-close" aria-label="关闭" @click="currentTask = null">×</button>
        </div>
        <div class="feedback-columns">
          <section class="feedback-pane">
            <div class="pane-title"><strong>模型提示词</strong><button class="button" @click="copyPrompt">{{ copied ? "已复制" : "复制" }}</button></div>
            <pre class="prompt-view">{{ currentTask.display_prompt || "（没有提示词内容）" }}</pre>
          </section>
          <section class="feedback-pane">
            <label class="pane-title" for="answer-text"><strong>你的回答</strong><span>反馈将提交到任务队列</span></label>
            <textarea id="answer-text" v-model="answer" class="answer-input" placeholder="输入你的分析、建议或改进方向…" autofocus></textarea>
            <button class="button primary submit-feedback" :disabled="busy || !answer.trim()" @click="submitAnswer">{{ busy ? "正在提交…" : "提交回答" }}</button>
          </section>
        </div>
        <p v-if="error" class="message error">{{ error }}</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.task-list { display:grid; gap:10px; }
.task-card { display:flex; align-items:center; gap:15px; padding:17px 20px; }
.task-icon { display:grid; width:40px; height:40px; flex:none; place-items:center; border-radius:12px; background:#edf2ff; color:var(--accent); font-size:19px; }
.task-main { min-width:0; flex:1; }
.task-title-row { display:flex; align-items:center; gap:9px; font-size:13px; }
.pending-badge { padding:4px 7px; border-radius:100px; background:#fff5dd; color:#946407; font-size:9px; font-weight:700; }
.task-id { overflow:hidden; margin:4px 0; color:#6b7890; font-size:10px; text-overflow:ellipsis; }
.task-main small { color:#98a2b3; font-size:10px; }
.empty-icon { display:grid; width:42px; height:42px; margin:0 auto 10px; place-items:center; border-radius:50%; background:#e6f6ee; color:var(--success); font-size:18px; }
.empty-state strong { color:#36445c; }
.empty-state p { margin:7px 0 0; font-size:12px; }
.modal-backdrop { position:fixed; z-index:60; inset:0; display:grid; place-items:center; padding:24px; background:#10182788; }
.feedback-modal { display:flex; width:min(1100px,96vw); max-height:90vh; flex-direction:column; overflow:hidden; border:1px solid var(--line); border-radius:14px; background:#fff; box-shadow:0 24px 80px #10182744; }
.modal-title { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:17px 22px; border-bottom:1px solid var(--line); }
.modal-title .eyebrow { margin-bottom:4px; }.modal-title h2 { margin:0 0 5px; font-size:17px; }.modal-title small { color:var(--muted); font-size:10px; }
.icon-close { width:32px; height:32px; border:0; border-radius:8px; background:#f1f3f7; color:#647086; font-size:20px; }
.feedback-columns { display:grid; min-height:0; grid-template-columns:1fr 1fr; gap:1px; overflow:auto; background:var(--line); }
.feedback-pane { display:flex; min-height:380px; flex-direction:column; padding:17px; background:#fff; }
.pane-title { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; font-size:12px; }
.pane-title span { color:var(--muted); font-size:10px; font-weight:400; }
.pane-title .button { min-height:30px; padding:5px 9px; font-size:10px; }
.prompt-view { flex:1; min-height:280px; overflow:auto; margin:0; padding:14px; border:1px solid var(--line); border-radius:9px; background:#f7f8fb; color:#344054; font:11px/1.65 "IBM Plex Mono",monospace; white-space:pre-wrap; }
.answer-input { flex:1; min-height:280px; resize:vertical; padding:13px; border:1px solid #d6deea; border-radius:9px; font:12px/1.6 "IBM Plex Mono",monospace; }
.submit-feedback { align-self:flex-end; margin-top:10px; }
.feedback-modal > .message { margin:12px 16px 16px; }
@media(max-width:760px) { .task-card { align-items:flex-start; flex-wrap:wrap; }.task-card .button { margin-left:55px; }.feedback-columns { grid-template-columns:1fr; }.feedback-pane { min-height:280px; }.modal-backdrop { padding:8px; } }
</style>
