// 运行在扩展隔离环境：拼快照 Markdown、同步到本地服务、响应侧边栏的取数请求。
(() => {
  const TAG = "lc-claude-bridge";
  let lastResult = null;

  // ---------- 和 inject.js 通信 ----------
  const pending = {};
  function getCode() {
    return new Promise((resolve) => {
      const reqId = Math.random().toString(36).slice(2);
      pending[reqId] = resolve;
      window.postMessage({ source: TAG, type: "getCode", reqId }, "*");
      setTimeout(() => {
        if (pending[reqId]) { delete pending[reqId]; resolve({ code: null, lang: null }); }
      }, 1500);
    });
  }

  window.addEventListener("message", (e) => {
    if (e.source !== window || e.data?.source !== TAG) return;
    const { type, payload } = e.data;
    if (type === "code" && pending[payload.reqId]) {
      pending[payload.reqId](payload);
      delete pending[payload.reqId];
    } else if (type === "result") {
      lastResult = payload;
      sync("result");
      notifyPanel();
      if (payload.kind === "提交" && payload.raw?.status_msg === "Accepted") {
        buildSnapshot().then((snap) =>
          chrome.runtime.sendMessage({ type: "accepted", id: payload.id, slug: slug(), snap }).catch(() => {})
        );
      }
    } else if (type === "debug") {
      chrome.runtime.sendMessage({ type: "debug", ...payload }).catch(() => {});
    } else if (type === "codeChanged") {
      sync("code");
    }
  });

  // ---------- 页面信息 ----------
  const slug = () => location.pathname.split("/")[2] || "";
  const title = () => document.title.replace(/\s*-\s*力扣.*$/, "").trim();
  function description() {
    const el = document.querySelector('[data-track-load="description_content"]');
    return el ? el.innerText.trim() : "";
  }

  // ---------- 判题结果 → Markdown ----------
  // 超长用例（比如 10^5 个元素的数组）原样附上会让每次提问都几十万 token，只留头尾
  const clipLine = (line) => {
    if (line.length <= 600) return line;
    const items = (line.match(/,/g) || []).length + 1;
    return `${line.slice(0, 300)} …（这一行共 ${line.length} 字${items > 1 ? `、约 ${items} 个元素` : ""}，中间省略）… ${line.slice(-100)}`;
  };
  const fence = (s, lang = "") => "```" + lang + "\n" + String(s).replace(/\n+$/, "").split("\n").map(clipLine).join("\n") + "\n```";
  const arr = (v) => (Array.isArray(v) ? v.join("\n") : v ?? "");

  function resultMd(r) {
    if (!r) return "_还没有运行或提交过（插件加载后）。_";
    const j = r.raw;
    const out = [];
    const when = new Date(r.at).toLocaleString("zh-CN", { hour12: false });
    out.push(`- 类型：**${r.kind}**（${when}）`);
    out.push(`- 结果：**${j.status_msg || "未知"}**`);
    if (j.total_testcases != null) out.push(`- 通过用例：${j.total_correct ?? "?"} / ${j.total_testcases}`);
    if (j.status_runtime && j.status_runtime !== "N/A") out.push(`- 用时：${j.status_runtime}，内存：${j.status_memory || "?"}`);

    const err = j.full_compile_error || j.compile_error || j.full_runtime_error || j.runtime_error;
    if (err) out.push("", "### 报错", fence(err));

    if (r.kind === "运行") {
      if (r.input) out.push("", "### 测试输入", fence(r.input));
      const ans = arr(j.code_answer), exp = arr(j.expected_code_answer);
      if (ans) out.push("", "### 我的输出", fence(ans));
      if (exp) out.push("", "### 预期输出", fence(exp));
      if (Array.isArray(j.compare_result) || typeof j.compare_result === "string") {
        out.push("", `逐用例对比（1=通过，0=失败）：\`${arr(j.compare_result)}\``);
      }
    } else {
      if (j.last_testcase) out.push("", "### 失败用例输入", fence(j.last_testcase));
      if (j.code_output) out.push("", "### 我的输出", fence(arr(j.code_output)));
      if (j.expected_output) out.push("", "### 预期输出", fence(j.expected_output));
    }
    const stdout = arr(j.std_output_list || j.std_output || "");
    if (stdout.trim()) out.push("", "### 标准输出（print）", fence(stdout));
    return out.join("\n");
  }

  async function buildSnapshot() {
    const { code, lang } = await getCode();
    const md = [
      `# ${title()}`,
      "",
      `链接：${location.origin}/problems/${slug()}/`,
      `语言：${lang || "未知"}`,
      "",
      "## 题目描述",
      description() || "_没读到题面（可能不在「题目描述」标签页）。_",
      "",
      "## 我的代码",
      code != null ? fence(code, lang || "") : "_没读到编辑器内容。_",
      "",
      "## 最近一次运行 / 提交",
      resultMd(lastResult),
      "",
    ].join("\n");
    return {
      slug: slug(), title: title(), lang, code, result: lastResult,
      status: lastResult?.raw?.status_msg || null, markdown: md, at: Date.now(),
    };
  }

  // ---------- 侧边栏取数 ----------
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "getSnapshot") {
      buildSnapshot().then(sendResponse);
      return true;
    }
    if (msg?.type === "getQuestion") { // 侧边栏的「白板」按钮要题面 HTML
      const el = document.querySelector('[data-track-load="description_content"]');
      const [, id = "", t = title()] = title().match(/^(\S+?)\.\s*(.*)$/) || [];
      sendResponse(el ? { slug: slug(), id, title: t, difficulty: "", content: el.innerHTML } : null);
    }
  });
  // 有新的运行结果 / 换题时通知侧边栏刷新
  function notifyPanel() {
    chrome.runtime.sendMessage({ type: "pageUpdated", slug: slug() }).catch(() => {});
  }

  // ---------- 同步到本地服务 ----------
  async function sync(reason) {
    try {
      const snap = await buildSnapshot();
      await chrome.runtime.sendMessage({ type: "sync", reason, snap });
    } catch (_) {}
  }


  // 打开页面 / 切换题目时同步一次（leetcode.cn 是单页应用）
  let lastPath = location.pathname.split("/").slice(0, 3).join("/");
  setTimeout(() => sync("open"), 3000);
  setInterval(() => {
    const p = location.pathname.split("/").slice(0, 3).join("/");
    if (p !== lastPath) {
      lastPath = p;
      lastResult = null;
      setTimeout(() => { sync("open"); notifyPanel(); }, 2500);
    }
  }, 1000);
})();
