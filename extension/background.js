// 与本地服务通信放在扩展环境里做，避开 leetcode.cn 页面的 CSP。
importScripts("shared.js");

// 点工具栏图标 / 快捷键 → 打开侧边栏
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // 提交通过 → 自动检查代码
  if (msg?.type === "accepted" && sender.tab) {
    autoReview(msg, sender.tab.id);
    return;
  }

  // 调试日志 → 本地服务
  if (msg?.type === "debug") {
    fetch(`${SERVER}/debug`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg) }).catch(() => {});
    return;
  }

  // 快照同步到本地文件（顺带把力扣登录状态交给本地服务，供 Claude 查提交记录/题解）
  if (msg?.type === "sync") {
    sendCookies();
    fetch(`${SERVER}/snapshot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: msg.reason, ...msg.snap }),
    })
      .then((r) => sendResponse({ ok: r.ok }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
});

// ---------- 提交通过后的自动检查 ----------
// 侧边栏开着且正在看这道题 → 交给侧边栏（能看到逐字输出）；否则在后台做完存进对话，并在图标上标个 1。
async function autoReview({ id, slug, snap }, tabId) {
  const key = "reviewed:" + id;
  if ((await chrome.storage.local.get(key))[key]) return; // 同一次提交只检查一次
  await chrome.storage.local.set({ [key]: Date.now() });

  const r = await chrome.runtime.sendMessage({ type: "reviewInPanel", tabId, slug }).catch(() => null);
  if (r?.taken) return;

  const conv = await loadConv(); // 放进当前会话
  const context = contextFor(conv, slug, snap.markdown);
  const { model } = await chrome.storage.local.get("model");
  let text = "", err = "";
  try {
    const res = await fetch(`${SERVER}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: REVIEW_PROMPT, context, session: conv.session, conv: conv.id, slug, title: snap.title, model: model || null, auto: true, effort: "low" }),
    });
    for (const line of (await res.text()).split("\n")) {
      if (!line.trim()) continue;
      const m = JSON.parse(line);
      if (m.session) conv.session = m.session;
      if (m.t) text += m.t;
      if (m.error) err = m.error;
    }
  } catch (e) {
    err = "连不上本地服务";
  }
  if (!text) return; // 失败就算了，不打扰
  conv.msgs.push({ role: "user", text: REVIEW_LABEL }, { role: "assistant", text });
  Object.assign(conv, { slug, title: snap.title });
  await saveConv(conv);
  chrome.action.setBadgeText({ tabId, text: "1" }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color: "#c96442" }).catch(() => {});
}

// ---------- 登录状态 → 本地服务（只发往 127.0.0.1，服务只放内存） ----------
let lastSession = null;
async function sendCookies() {
  try {
    const [s, c] = await Promise.all([
      chrome.cookies.get({ url: "https://leetcode.cn", name: "LEETCODE_SESSION" }),
      chrome.cookies.get({ url: "https://leetcode.cn", name: "csrftoken" }),
    ]);
    if (!s?.value) return;
    const key = s.value + "|" + (c?.value || "");
    if (key === lastSession) return;
    const r = await fetch(`${SERVER}/cookies`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session: s.value, csrf: c?.value || "" }),
    });
    if (r.ok) lastSession = key;
  } catch (_) {}
}
