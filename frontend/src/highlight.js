// 轻量行级语法高亮：够用的 token 着色，不引入 highlight.js 这类依赖
const TOKEN = /(#[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|\b(def|class|return|if|elif|else|for|while|import|from|as|in|not|and|or|None|True|False|try|except|finally|with|lambda|pass|raise|yield|global|self|print)\b|(\b\d+(?:\.\d+)?\b)/g;

export function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function highlightLine(line) {
  const escaped = escapeHtml(line);
  return escaped.replace(TOKEN, (match, comment, text, keyword, number) => {
    if (comment) return `<span class="tok-comment">${comment}</span>`;
    if (text) return `<span class="tok-string">${text}</span>`;
    if (keyword) return `<span class="tok-keyword">${keyword}</span>`;
    if (number) return `<span class="tok-number">${number}</span>`;
    return match;
  });
}

export function isCodeFile(path) {
  return /\.(py|js|ts|json|ya?ml|md|txt|log|csv|sh|toml|ini|cfg)$/i.test(String(path || ""));
}

// JSON：键、字符串、数字、字面量分别着色
const JSON_TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;

export function highlightJsonLine(line) {
  return escapeHtml(line).replace(JSON_TOKEN, (match, text, colon, literal, number) => {
    if (text && colon) return `<span class="tok-key">${text}</span>${colon}`;
    if (text) return `<span class="tok-str">${text}</span>`;
    if (literal) return `<span class="tok-lit">${literal}</span>`;
    if (number) return `<span class="tok-num">${number}</span>`;
    return match;
  });
}
