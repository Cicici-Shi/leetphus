// 运行在页面自身的 JS 环境（MAIN world）：
// 1. 拦截 fetch / XHR，抓取「运行」「提交」的请求参数和判题结果
// 2. 读取 Monaco 编辑器里的代码
// 通过 window.postMessage 与 content.js 通信。
(() => {
  const TAG = "lc-claude-bridge";
  const runs = {}; // 判题 id -> { kind, input, lang }
  let lastKind = null;

  const send = (type, payload) => window.postMessage({ source: TAG, type, payload }, "*");

  function onRequest(url, body) {
    try {
      if (/\/interpret_solution\/?$/.test(url)) {
        const b = JSON.parse(body || "{}");
        lastKind = { kind: "运行", input: b.data_input ?? "", lang: b.lang };
      } else if (/\/submit\/?$/.test(url)) {
        const b = JSON.parse(body || "{}");
        lastKind = { kind: "提交", input: "", lang: b.lang };
      }
    } catch (_) {}
  }

  function onResponse(url, text) {
    try {
      if (/\/interpret_solution\/?$/.test(url) || /\/submit\/?$/.test(url)) {
        const j = JSON.parse(text);
        const id = j.interpret_id || j.submission_id;
        if (id && lastKind) runs[id] = lastKind;
        return;
      }
      const m = url.match(/\/submissions\/detail\/([^/]+)\/(?:v\d+\/)?check\/?/); // 提交用的是 /v2/check/
      if (!m) return;
      const j = JSON.parse(text);
      if (j.state ? j.state !== "SUCCESS" : !j.status_msg) return; // PENDING / STARTED 还在判题
      const meta = { ...(runs[m[1]] || lastKind || { input: "" }) };
      meta.kind = String(m[1]).startsWith("runcode") ? "运行" : "提交";
      send("result", { id: m[1], ...meta, raw: j, at: Date.now() });
    } catch (_) {}
  }

  // --- fetch ---
  const origFetch = window.fetch;
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : input?.url || "";
    const body = init?.body;
    if (typeof body === "string") onRequest(url, body);
    const res = await origFetch.apply(this, arguments);
    if (/interpret_solution|\/submit\/?$|\/check\/?/.test(url)) {
      res.clone().text().then((t) => onResponse(url, t)).catch(() => {});
    }
    return res;
  };

  // --- XHR ---
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__lcUrl = String(url);
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const url = this.__lcUrl || "";
    if (typeof body === "string") onRequest(url, body);
    if (/interpret_solution|\/submit\/?$|\/check\/?/.test(url)) {
      this.addEventListener("load", () => {
        try { onResponse(url, this.responseText); } catch (_) {}
      });
    }
    return origSend.apply(this, arguments);
  };

  // --- 编辑器 ---
  function codeModel() {
    const models = window.monaco?.editor?.getModels?.() || [];
    // 代码 model 的语言不是 plaintext；取内容最长的一个
    const cands = models.filter((m) => m.getLanguageId?.() !== "plaintext");
    return (cands.length ? cands : models).sort((a, b) => b.getValueLength() - a.getValueLength())[0];
  }

  let watched = null;
  setInterval(() => {
    const m = codeModel();
    if (m && m !== watched) {
      watched = m;
      let t;
      m.onDidChangeContent(() => {
        clearTimeout(t);
        t = setTimeout(() => send("codeChanged", {}), 2000);
      });
    }
  }, 1500);

  window.addEventListener("message", (e) => {
    if (e.source !== window || e.data?.source !== TAG || e.data.type !== "getCode") return;
    const m = codeModel();
    send("code", {
      reqId: e.data.reqId,
      code: m ? m.getValue() : null,
      lang: m ? m.getLanguageId?.() : null,
    });
  });
})();
