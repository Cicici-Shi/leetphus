// 侧边栏：读取当前标签页的力扣题目快照，调用本地服务 /ask（背后是 claude -p），流式显示回答。
const $ = (s) => document.querySelector(s);
const log = $("#log");

let tabId = null;
let slug = null;
let ctrl = null; // 正在进行的请求

// 对话记录的读写（loadStore / loadConv / saveConv）在 shared.js，按题目存在 chrome.storage.local

// ---------- 滚动：只有停在底部附近时才跟随新内容；往上翻了就不打扰 ----------
let follow = true;
log.addEventListener("scroll", () => {
  follow = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
});
function scrollDown(force) {
  if (force) follow = true;
  if (follow) log.scrollTop = log.scrollHeight;
}

// ---------- 极简 Markdown 渲染（先转义，再处理代码块/行内代码/加粗/标题/列表/表格） ----------
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```[^\n]*\n([\s\S]*?)(```|$)/g, (_, code) => {
    blocks.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  const out = [];
  let list = null;
  const lines = s.split("\n");
  const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // 表格：| a | b | 下一行是 |---|---|
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1] || "")) {
      if (list) { out.push(`</${list}>`); list = null; }
      const head = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
      i--;
      out.push(`<div class="tbl"><table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${
        rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (li) {
      if (!list) { list = /^\s*\d/.test(line) ? "ol" : "ul"; out.push(`<${list}>`); }
      out.push(`<li>${li[1]}</li>`);
      continue;
    }
    if (list) { out.push(`</${list}>`); list = null; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) out.push(`<h${h[1].length}>${h[2]}</h${h[1].length}>`);
    else if (/^\u0000\d+\u0000$/.test(line.trim())) out.push(line.trim());
    else if (line.trim()) out.push(`<p>${line}</p>`);
  }
  if (list) out.push(`</${list}>`);
  return out.join("").replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[+i]);
}

function add(cls, html) {
  log.querySelector(".empty")?.remove();
  const el = document.createElement("div");
  el.className = "m " + cls;
  el.innerHTML = html;
  log.appendChild(el);
  scrollDown();
  return el;
}

const CHIPS = ["帮我看看哪里错了", "给个思路提示，别直接给答案", "分析一下时间和空间复杂度", "有没有更简洁的写法"];

function renderEmpty(title) {
  log.innerHTML = "";
  const box = document.createElement("div");
  box.className = "empty";
  box.innerHTML = `<div>直接问，或者点一个：</div><div class="chips"></div>`;
  for (const c of CHIPS) {
    const b = document.createElement("button");
    b.textContent = c;
    b.onclick = () => { $("#q").value = c; ask(); };
    box.querySelector(".chips").appendChild(b);
  }
  log.appendChild(box);
}

async function renderConv() {
  let conv = { msgs: [] };
  try { conv = await loadConv(slug); } catch (_) {}
  log.innerHTML = "";
  if (!conv.msgs.length) return renderEmpty($("#title").textContent);
  for (const m of conv.msgs) add(m.role === "user" ? "u" : "a", m.role === "user" ? esc(m.text) : md(m.text));
}

// ---------- 当前标签页 ----------
async function snapshot() {
  if (tabId == null) return null;
  try {
    return await chrome.tabs.sendMessage(tabId, { type: "getSnapshot" });
  } catch (_) {
    return null; // 页面还没加载完，或者插件更新后页面没刷新
  }
}

async function refreshTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const m = tab?.url?.match(/^https:\/\/leetcode\.cn\/problems\/([^/?#]+)/);
  const enabled = !!m;
  $("#q").disabled = $("#send").disabled = !enabled;
  if (!enabled) {
    tabId = slug = null;
    $("#title").textContent = "Leetphus";
    $("#status").textContent = "";
    log.innerHTML = `<div class="notlc">打开一道 leetcode.cn 的题目，这里就能用了。</div>`;
    return;
  }
  const changed = tab.id !== tabId || m[1] !== slug;
  if (changed) closeSessions();
  tabId = tab.id;
  slug = m[1];
  chrome.action.setBadgeText({ tabId, text: "" }).catch(() => {});
  const snap = await snapshot();
  $("#title").textContent = snap?.title || tab.title.replace(/\s*-\s*力扣.*$/, "") || slug;
  const st = snap?.status;
  $("#status").textContent = st ? st : snap ? "" : "需刷新页面";
  $("#status").className = "st " + (st ? (st === "Accepted" ? "good" : "bad") : "");
  if (changed && !ctrl) await renderConv();
}

async function checkServer() {
  let ok = false;
  try { ok = (await fetch(SERVER + "/latest", { cache: "no-store" })).ok; } catch (_) {}
  return ok;
}

// ---------- 提问 ----------
function setBusy(b) {
  // 发送 = 上箭头；回答中 = 方块（点了停止）
  $("#send").innerHTML = b
    ? '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="3" y="3" width="10" height="10" rx="2" fill="currentColor"/></svg>'
    : '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  $("#send").title = b ? "停止" : "发送（回车）";
  $("#send").setAttribute("aria-label", b ? "停止" : "发送");
  $("#send").classList.toggle("stop", b);
}

async function ask(opts = {}) {
  if (ctrl) { if (!opts.auto) ctrl.abort(); return; }
  const q = opts.auto ? REVIEW_PROMPT : $("#q").value.trim();
  if (!q || !slug) return;
  if (!opts.auto) $("#q").value = "";
  const shown = opts.auto ? REVIEW_LABEL : q;
  const mySlug = slug;
  scrollDown(true); // 自己发的问题：回到底部
  add("u", esc(shown));
  const a = add("a", '<span class="thinking">思考中…</span>');
  setBusy(true);
  ctrl = new AbortController();

  let conv = newConvObj();
  try { conv = await loadConv(mySlug); } catch (e) { add("err", "读取对话记录失败：" + esc(e.message)); }
  const snap = await snapshot();
  if (!snap) {
    a.remove();
    add("err", "读不到页面内容：请刷新一下力扣页面再问。");
    setBusy(false); ctrl = null;
    return;
  }
  // 追问时不重复发题面，只发代码和运行结果
  const context = conv.session ? snap.markdown.replace(/## 题目描述[\s\S]*?(?=## 我的代码)/, "") : snap.markdown;
  conv.msgs.push({ role: "user", text: shown });

  let text = "";
  const status = Object.assign(document.createElement("div"), { className: "thinking", hidden: true });
  try {
    const res = await fetch(SERVER + "/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, context, session: conv.session, slug: mySlug, title: snap.title, model: $("#model").value || null, auto: !!opts.auto, effort: opts.auto ? "low" : null }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`本地服务返回 ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (!line) continue;
        const m = JSON.parse(line);
        if (m.session) conv.session = m.session;
        if (m.t) {
          status.remove();
          text += m.t;
          a.innerHTML = md(text);
          scrollDown();
        }
        if (m.status) {
          // 正在查资料：没出字时替换「思考中」，出了字就在回答下面小字显示
          const th = a.querySelector(".thinking");
          if (th) th.textContent = m.status;
          else { status.textContent = m.status; status.hidden = false; log.appendChild(status); }
        }
        if (m.error) add("err", esc(m.error));
        if (m.learning) learnNote(m.learning);
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      if (text) text += "\n\n_（已停止）_";
    } else {
      add("err", (await checkServer()) ? esc(e.message) : "连不上本地服务：先在终端运行 python3 ~/leetcode-claude-bridge/server.py");
    }
  } finally {
    status.remove();
    if (text) { a.innerHTML = md(text); conv.msgs.push({ role: "assistant", text }); }
    else a.remove();
    try { await saveConv(mySlug, conv); } catch (_) {}
    ctrl = null;
    setBusy(false);
  }
}

$("#send").addEventListener("click", () => ask());
$("#q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(); }
});
// ---------- 偏好 ----------
function learnNote(reason) {
  const el = document.createElement("div");
  el.className = "learn";
  el.textContent = reason === "correction" ? "已记下这次纠正，正在更新偏好…" : "正在根据最近的问题整理偏好…";
  log.appendChild(el);
  scrollDown();
  // 整理在后台跑，结束后把提示改成完成（最多等 3 分钟）
  const t0 = Date.now();
  const poll = setInterval(async () => {
    try {
      const p = await (await fetch(SERVER + "/profile", { cache: "no-store" })).json();
      if (!p.reflecting) { clearInterval(poll); el.textContent = "偏好已更新"; }
    } catch (_) {}
    if (Date.now() - t0 > 180000) { clearInterval(poll); el.remove(); }
  }, 2000);
}

let prefPoll = null;
async function loadPrefs() {
  try {
    const p = await (await fetch(SERVER + "/profile", { cache: "no-store" })).json();
    if (document.activeElement !== $("#prefText") && !$("#prefText").dataset.dirty) $("#prefText").value = p.profile;
    $("#prefMeta").textContent =
      (p.reflecting ? "正在整理偏好… · " : "") +
      `共记录 ${p.total_questions} 个问题 · ` +
      (p.last_reflect ? `上次整理 ${p.last_reflect}` : "还没整理过") +
      ` · 再问 ${Math.max(0, p.every - p.since_reflect)} 个会自动整理；纠正它时会立刻整理`;
  } catch (_) {
    $("#prefMeta").textContent = "连不上本地服务：先运行 python3 ~/leetcode-claude-bridge/server.py";
  }
}
function showPrefs(on) {
  $("#prefs").hidden = !on;
  $("#log").hidden = $("#foot").hidden = $("#chatBar").hidden = on;
  $("#prefBar").hidden = !on;
  closeSessions();
  clearInterval(prefPoll);
  if (on) { delete $("#prefText").dataset.dirty; loadPrefs(); prefPoll = setInterval(loadPrefs, 3000); }
}
$("#prefBtn").addEventListener("click", () => showPrefs(true));
$("#prefBack").addEventListener("click", () => showPrefs(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#prefs").hidden) showPrefs(false); });
$("#prefText").addEventListener("input", () => ($("#prefText").dataset.dirty = "1"));
$("#prefSave").addEventListener("click", async () => {
  try {
    await fetch(SERVER + "/profile", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: $("#prefText").value }),
    });
    delete $("#prefText").dataset.dirty;
    $("#prefMsg").textContent = "已保存";
  } catch (_) { $("#prefMsg").textContent = "保存失败"; }
  setTimeout(() => ($("#prefMsg").textContent = ""), 1500);
});
$("#prefReflect").addEventListener("click", async () => {
  if ($("#prefText").dataset.dirty && !confirmInline()) return;
  delete $("#prefText").dataset.dirty;
  try { await fetch(SERVER + "/reflect", { method: "POST", body: "{}" }); } catch (_) {}
  $("#prefMsg").textContent = "已开始整理，稍等十几秒";
  setTimeout(() => ($("#prefMsg").textContent = ""), 4000);
  loadPrefs();
});
// 有未保存的修改时，第一次点「立即整理」只提示，第二次才真的执行
let confirmArmed = false;
function confirmInline() {
  if (confirmArmed) { confirmArmed = false; return true; }
  confirmArmed = true;
  $("#prefMsg").textContent = "你有未保存的修改，整理会覆盖它们。再点一次确认";
  setTimeout(() => { confirmArmed = false; }, 4000);
  return false;
}

// ---------- 会话：新建 / 切换 / 删除 ----------
$("#new").addEventListener("click", async () => {
  if (!slug) return;
  if (ctrl) ctrl.abort();
  const store = await loadStore(slug);
  const cur = store.list.find((c) => c.id === store.active);
  if (cur?.msgs.length) { // 当前已经是空对话就不再多建一个
    const c = newConvObj();
    store.list.push(c);
    store.active = c.id;
    await saveStore(slug, store);
  }
  closeSessions();
  renderEmpty($("#title").textContent);
});

const menu = $("#sessions");
function closeSessions() { menu.hidden = true; }
function when(ts) {
  const d = new Date(ts), now = new Date();
  const hm = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return d.toDateString() === now.toDateString() ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}
async function renderSessions() {
  const store = await loadStore(slug);
  const list = [...store.list].sort((a, b) => b.updated - a.updated);
  menu.innerHTML = "";
  for (const c of list) {
    const row = document.createElement("div");
    row.className = "sess" + (c.id === store.active ? " on" : "");
    row.innerHTML = `<button class="pick"><span class="st">${esc(convTitle(c))}</span><span class="sm">${when(c.updated)} · ${Math.ceil(c.msgs.length / 2)} 轮</span></button><button class="del" title="删除这段对话">×</button>`;
    row.querySelector(".pick").onclick = async () => {
      if (ctrl) ctrl.abort();
      const s = await loadStore(slug);
      s.active = c.id;
      await saveStore(slug, s);
      closeSessions();
      await renderConv();
      scrollDown(true);
    };
    const del = row.querySelector(".del");
    del.onclick = async () => {
      if (!del.classList.contains("sure")) { // 点第一次变成「删除?」，再点才删
        del.classList.add("sure");
        del.textContent = "删除?";
        setTimeout(() => { del.classList.remove("sure"); del.textContent = "×"; }, 3000);
        return;
      }
      if (ctrl && c.id === store.active) ctrl.abort();
      const s = await loadStore(slug);
      s.list = s.list.filter((x) => x.id !== c.id);
      await saveStore(slug, s); // loadStore 会在下次读取时补一个当前会话
      await renderConv();
      renderSessions();
    };
    menu.appendChild(row);
  }
}
$("#hist").addEventListener("click", () => {
  if (!slug) return;
  if (menu.hidden) { renderSessions(); menu.hidden = false; } else closeSessions();
});
document.addEventListener("click", (e) => {
  if (!menu.hidden && !menu.contains(e.target) && e.target.closest("#hist") == null) closeSessions();
});

// 切标签页 / 页面跳转 / 有新运行结果时刷新顶部信息
chrome.tabs.onActivated.addListener(refreshTab);
chrome.tabs.onUpdated.addListener((id, info) => { if (id === tabId || info.url) refreshTab(); });
chrome.windows.onFocusChanged.addListener(refreshTab);
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === "pageUpdated" && sender.tab?.id === tabId) refreshTab();
  // 后台问：这次提交通过的自动检查，侧边栏能不能接？（正在看这道题且空闲才接）
  if (msg?.type === "reviewInPanel") {
    const take = msg.tabId === tabId && msg.slug === slug && !ctrl && !$("#log").hidden;
    sendResponse({ taken: take });
    if (take) ask({ auto: true });
  }
});

// 后台替你做完的检查写进了存储：当前题目的对话有变化时重新渲染
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && slug && changes["convs:" + slug] && !ctrl) {
    renderConv();
    if (!menu.hidden) renderSessions();
  }
});

// 模型选择：记住上次选的
chrome.storage.local.get("model").then((r) => { if (r.model != null) $("#model").value = r.model; }).catch(() => {});
$("#model").addEventListener("change", () => chrome.storage.local.set({ model: $("#model").value }).catch(() => {}));

refreshTab();
$("#q").focus();
