// 侧边栏和后台共用的常量。
const SERVER = "http://127.0.0.1:8765";
const REVIEW_LABEL = "提交通过 · 自动检查代码";
const REVIEW_PROMPT = `我刚提交通过了这道题。帮我检查一下现在这份代码：
1. 有没有可以写得更简洁的地方（更短的写法、合并的分支、Python 内置函数等）；
2. 有没有多写了的东西（没用到的变量、多余的判断或边界处理、可以省掉的 import 或数组）。
逐条列出具体位置和改法，每条附上改后的那一两行即可，不要重写整份代码。如果已经很简洁了，就直接说一句不用改。`;

// ---------- 会话存储：不跟题目绑定，全部放在一个列表 { active, list: [{ id, created, updated, session, msgs, slug, title }] } ----------
// slug/title 是这段对话最近一次提问时所在的题目
const STORE_KEY = "convs";
const newConvObj = () => ({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), created: Date.now(), updated: Date.now(), session: null, msgs: [], slug: null, title: null });

async function loadStore() {
  const all = await chrome.storage.local.get(null);
  let store = all[STORE_KEY];
  if (!all.imported) {
    // 第一次运行（比如插件换了位置重新加载）：从本地服务的问答日志把以前的对话恢复回来
    try {
      const r = await fetch(SERVER + "/conversations", { cache: "no-store" });
      if (r.ok) {
        const { list } = await r.json();
        store = store || { active: null, list: [] };
        for (const c of list) if (!store.list.some((x) => x.id === c.id)) store.list.push(c);
        store.list.sort((a, b) => a.updated - b.updated);
        await chrome.storage.local.set({ [STORE_KEY]: store, imported: Date.now() });
      }
    } catch (_) {} // 服务没开就下次再试
  }
  const oldKeys = Object.keys(all).filter((k) => k.startsWith("convs:") || k.startsWith("conv:"));
  if (!store || oldKeys.length) {
    // 旧版按题目分开存（convs:slug / 更早的 conv:slug），合并进来
    store = store || { active: null, list: [] };
    for (const k of oldKeys) {
      const slug = k.slice(k.indexOf(":") + 1);
      const v = all[k];
      const convs = k.startsWith("convs:") ? v.list || [] : v?.msgs?.length ? [{ ...newConvObj(), created: 0, updated: 0, session: v.session, msgs: v.msgs }] : [];
      for (const c of convs) if (c.msgs.length && !store.list.some((x) => x.id === c.id)) store.list.push({ ...c, slug: c.slug || slug, title: c.title || slug });
    }
    store.list.sort((a, b) => a.updated - b.updated);
    await chrome.storage.local.set({ [STORE_KEY]: store });
    if (oldKeys.length) await chrome.storage.local.remove(oldKeys);
  }
  if (!store.list.find((c) => c.id === store.active)) {
    if (!store.list.length) store.list.push(newConvObj());
    store.active = store.list[store.list.length - 1].id;
  }
  return store;
}
async function saveStore(store) {
  await chrome.storage.local.set({ [STORE_KEY]: store });
}
// 当前会话（没有就建一个）
async function loadConv() {
  const store = await loadStore();
  return store.list.find((c) => c.id === store.active);
}
// 按 id 写回；这段会话可能已经不是当前的了（比如回答过程中切走了），照样存到它自己那里
async function saveConv(conv) {
  const store = await loadStore();
  conv.updated = Date.now();
  const i = store.list.findIndex((c) => c.id === conv.id);
  if (i >= 0) store.list[i] = conv; else store.list.push(conv);
  await saveStore(store);
}
// 同一段对话里第一次问这道题时带上题面，之后只发代码和运行结果
const contextFor = (conv, slug, markdown) =>
  conv.session && conv.slug === slug ? markdown.replace(/## 题目描述[\s\S]*?(?=## 我的代码)/, "") : markdown;
const convTitle = (c) => (c.msgs.find((m) => m.role === "user")?.text || "（空对话）").replace(/\s+/g, " ").slice(0, 40);
