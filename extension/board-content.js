// 白板页（本地服务的 /board/<名字>）上的内容脚本：把代码和运行结果拼成快照，给侧边栏和本地服务。
// 和 content.js 对力扣页面做的事一样，只是数据来自白板页自己。
(() => {
  const name = decodeURIComponent(location.pathname.replace(/^\/board\/?/, "").replace(/\/$/, ""));
  if (!name) return; // 选题页，不是白板

  // 页面通过 postMessage 交出当前状态
  const pending = {};
  function getState() {
    return new Promise((resolve) => {
      const reqId = Math.random().toString(36).slice(2);
      pending[reqId] = resolve;
      window.postMessage({ source: "leetphus-board-ext", type: "get", reqId }, "*");
      setTimeout(() => { if (pending[reqId]) { delete pending[reqId]; resolve(null); } }, 1500);
    });
  }
  window.addEventListener("message", (e) => {
    if (e.source !== window || e.data?.source !== "leetphus-board") return;
    if (e.data.type === "state" && pending[e.data.reqId]) {
      pending[e.data.reqId](e.data.state);
      delete pending[e.data.reqId];
    } else if (e.data.type === "run") {
      sync("result");
      chrome.runtime.sendMessage({ type: "pageUpdated", slug: name }).catch(() => {});
    }
  });

  // 和 content.js 一样：超长行只留头尾
  const clipLine = (line) => {
    if (line.length <= 600) return line;
    const items = (line.match(/,/g) || []).length + 1;
    return `${line.slice(0, 300)} …（这一行共 ${line.length} 字${items > 1 ? `、约 ${items} 个元素` : ""}，中间省略）… ${line.slice(-100)}`;
  };
  const fence = (s, lang = "") => "```" + lang + "\n" + String(s).replace(/\n+$/, "").split("\n").map(clipLine).join("\n") + "\n```";

  function runMd(r, codeNow) {
    if (!r) return "_还没运行过。_";
    const when = new Date(r.at).toLocaleString("zh-CN", { hour12: false });
    const out = [`- 类型：**白板运行**（${when}）`,
      `- 结果：**${r.timeout ? "超时（10 秒）" : r.code === 0 ? "正常结束" : "退出码 " + r.code}**，用时 ${r.ms} ms`];
    if (r.source !== codeNow) out.push("- 注意：运行之后代码又改过，下面的输出对应的是改之前的版本");
    out.push("", "### 标准输出（print）", r.stdout ? fence(r.stdout) : "_没有输出。_");
    if (r.stderr) out.push("", "### 报错", fence(r.stderr));
    return out.join("\n");
  }

  async function buildSnapshot() {
    const st = await getState();
    if (!st) return null;
    const title = st.title.replace(/^白板：/, "");
    const md = [
      `# 白板：${title}`,
      "",
      "模式：白板手撕（空白编辑器，类、测试数据、main 都要自己写，点运行直接执行整份代码，没有判题）",
      `语言：python3`,
      "",
      "## 题目描述",
      st.question || "_没有题面（自己起的名字，或者没拿到题面）。_",
      "",
      "## 我的代码",
      fence(st.code, "python3"),
      "",
      "## 最近一次运行 / 提交",
      runMd(st.run, st.code),
      "",
    ].join("\n");
    return { slug: name, title: "白板：" + title, lang: "python3", code: st.code, result: st.run, status: null, markdown: md, at: Date.now() };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "getSnapshot") {
      buildSnapshot().then(sendResponse);
      return true;
    }
  });

  async function sync(reason) {
    try {
      const snap = await buildSnapshot();
      if (snap) await chrome.runtime.sendMessage({ type: "sync", reason, snap });
    } catch (_) {}
  }
})();
