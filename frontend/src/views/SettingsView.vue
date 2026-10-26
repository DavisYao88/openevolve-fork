<script setup>
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { api } from "../api";

const PROVIDERS = [
  { value: "deepseek", label: "DeepSeek", apiBase: "https://api.deepseek.com", model: "deepseek-chat" },
  { value: "openai", label: "OpenAI", apiBase: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  { value: "azure", label: "Azure OpenAI", apiBase: "https://<resource>.openai.azure.com", model: "gpt-4o" },
  { value: "custom", label: "自定义（OpenAI 兼容）", apiBase: "", model: "" },
];

const theme = ref(localStorage.getItem("openevolve.theme") === "dark" ? "dark" : "light");
const provider = ref("deepseek");
const apiBase = ref("");
const apiKey = ref("");
const model = ref("");
const temperature = ref("0.6");
const maxTokens = ref("16000");
const hasApiKey = ref(false);
const apiKeyMasked = ref("");
const loading = ref(true);
const saving = ref(false);
const message = ref("");
const error = ref("");

const activeProvider = computed(() => PROVIDERS.find((item) => item.value === provider.value));

function applyTheme(value) {
  theme.value = value;
  document.documentElement.dataset.theme = value;
  localStorage.setItem("openevolve.theme", value);
  // 通知顶部导航同步主题按钮状态
  window.dispatchEvent(new Event("openevolve-theme"));
}

function pickProvider(value) {
  provider.value = value;
  const preset = PROVIDERS.find((item) => item.value === value);
  if (preset && preset.apiBase) apiBase.value = preset.apiBase;
  if (preset && preset.model) model.value = preset.model;
}

async function loadSettings() {
  loading.value = true;
  try {
    const data = await api.settings();
    const llm = data.llm || {};
    provider.value = llm.provider || "deepseek";
    apiBase.value = llm.api_base || "";
    model.value = llm.primary_model || "";
    temperature.value = llm.temperature ?? "0.6";
    maxTokens.value = llm.max_tokens ?? "16000";
    hasApiKey.value = !!llm.has_api_key;
    apiKeyMasked.value = llm.api_key_masked || "";
    if (data.theme === "dark" || data.theme === "light") applyTheme(data.theme);
  } catch (cause) {
    error.value = `读取设置失败：${cause.message}`;
  } finally {
    loading.value = false;
  }
}

async function save() {
  saving.value = true;
  message.value = "";
  error.value = "";
  try {
    const payload = {
      theme: theme.value,
      llm: {
        provider: provider.value,
        api_base: apiBase.value,
        primary_model: model.value,
        temperature: temperature.value,
        max_tokens: maxTokens.value,
      },
    };
    if (apiKey.value.trim()) payload.llm.api_key = apiKey.value.trim();
    const data = await api.saveSettings(payload);
    const llm = data.llm || {};
    hasApiKey.value = !!llm.has_api_key;
    apiKeyMasked.value = llm.api_key_masked || "";
    apiKey.value = "";
    message.value = "已保存，下一次启动演化任务时生效。";
  } catch (cause) {
    error.value = `保存失败：${cause.message}`;
  } finally {
    saving.value = false;
  }
}

async function clearApiKey() {
  saving.value = true;
  error.value = "";
  try {
    const data = await api.saveSettings({ llm: { clear_api_key: true } });
    hasApiKey.value = !!data.llm?.has_api_key;
    apiKeyMasked.value = data.llm?.api_key_masked || "";
    message.value = "已清除已保存的 API Key。";
  } catch (cause) {
    error.value = `清除失败：${cause.message}`;
  } finally {
    saving.value = false;
  }
}

onMounted(loadSettings);
</script>

<template>
  <section>
    <div class="page-heading">
      <div>
        <p class="eyebrow">Settings</p>
        <h1>设置</h1>
        <p>外观与模型供应商。模型配置会写入下一次启动的演化任务配置。</p>
      </div>
      <div class="heading-actions">
        <button class="button primary" :disabled="saving || loading" @click="save">{{ saving ? "保存中…" : "保存设置" }}</button>
        <RouterLink class="button" to="/runs">返回任务列表</RouterLink>
      </div>
    </div>

    <p v-if="error" class="message error">{{ error }}</p>
    <p v-else-if="message" class="message">{{ message }}</p>

    <div class="settings-grid">
      <section class="card">
        <h2>外观</h2>
        <p class="card-hint">深色/浅色主题，保存后立即生效。</p>
        <div class="theme-switch">
          <button class="theme-option" :class="{ selected: theme === 'light' }" @click="applyTheme('light')">
            <span class="theme-swatch light"></span>
            浅色
          </button>
          <button class="theme-option" :class="{ selected: theme === 'dark' }" @click="applyTheme('dark')">
            <span class="theme-swatch dark"></span>
            深色
          </button>
        </div>
      </section>

      <section class="card">
        <h2>模型供应商</h2>
        <p class="card-hint">用于演化过程中调用大模型；留空的字段沿用全局配置。</p>

        <label class="field">
          <span>供应商</span>
          <select v-model="provider" class="select-input" @change="pickProvider(provider)">
            <option v-for="item in PROVIDERS" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </label>

        <label class="field">
          <span>API Base</span>
          <input v-model="apiBase" class="text-input mono" :placeholder="activeProvider?.apiBase || 'https://…'" />
        </label>

        <label class="field">
          <span>模型名</span>
          <input v-model="model" class="text-input mono" :placeholder="activeProvider?.model || 'deepseek-chat'" />
        </label>

        <label class="field">
          <span>API Key</span>
          <input
            v-model="apiKey"
            class="text-input mono"
            type="password"
            :placeholder="hasApiKey ? `已保存：${apiKeyMasked}（留空则不修改）` : '粘贴 API Key'"
            autocomplete="off"
          />
        </label>

        <div class="field-row">
          <label class="field">
            <span>Temperature</span>
            <input v-model="temperature" class="text-input" inputmode="decimal" />
          </label>
          <label class="field">
            <span>Max Tokens</span>
            <input v-model="maxTokens" class="text-input" inputmode="numeric" />
          </label>
        </div>

        <div class="field-actions">
          <button class="button" :disabled="saving || !hasApiKey" @click="clearApiKey">清除已保存的 Key</button>
          <span class="field-hint">Key 只保存在本机 <code>evolve_tasks/gui_settings.json</code>，接口不会回传明文。</span>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.heading-actions { display:flex; gap:8px; }
.settings-grid { display:grid; grid-template-columns:minmax(260px, 1fr) minmax(0, 1.6fr); gap:16px; align-items:start; }
.settings-grid h2 { margin:0; font-size:14px; }
.card-hint { margin:6px 0 14px; color:var(--muted); font-size:11px; }
.theme-switch { display:flex; gap:10px; }
.theme-option { display:flex; flex:1; align-items:center; justify-content:center; gap:8px; padding:12px; border:1px solid var(--line); border-radius:10px; background:var(--surface); color:inherit; font-size:12px; cursor:pointer; }
.theme-option.selected { border-color:var(--accent); background:#eef3ff; color:var(--accent); font-weight:700; }
.theme-swatch { width:16px; height:16px; border-radius:50%; border:1px solid var(--line); }
.theme-swatch.light { background:#fff; }
.theme-swatch.dark { background:#1b2438; }
.field { display:flex; flex-direction:column; gap:6px; margin-bottom:12px; }
.field > span { color:var(--muted); font-size:11px; }
.field-row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.field-actions { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin-top:4px; }
.field-hint { color:var(--muted); font-size:10px; }
.field-hint code { color:#3a4658; }
@media (max-width: 900px) {
  .settings-grid { grid-template-columns:1fr; }
}
</style>
