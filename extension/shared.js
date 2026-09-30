// 侧边栏和后台共用的常量。
const SERVER = "http://127.0.0.1:8765";
const REVIEW_LABEL = "提交通过 · 自动检查代码";
const REVIEW_PROMPT = `我刚提交通过了这道题。帮我检查一下现在这份代码：
1. 有没有可以写得更简洁的地方（更短的写法、合并的分支、Python 内置函数等）；
2. 有没有多写了的东西（没用到的变量、多余的判断或边界处理、可以省掉的 import 或数组）。
逐条列出具体位置和改法，每条附上改后的那一两行即可，不要重写整份代码。如果已经很简洁了，就直接说一句不用改。`;

// ---------- 会话存储：每道题一个列表 { active, list: [{ id, created, updated, session, msgs }] } ----------
const newConvObj = () => ({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), created: Date.now(), updated: Date.now(), session: null, msgs: [] });

async function loadStore(slug) {
  const k = "convs:" + slug;
  const got = await chrome.storage.local.get([k, "conv:" + slug]);
  let store = got[k];
  if (!store) {
    // 旧版只有一段对话（conv:slug），迁移过来
    const old = got["conv:" + slug];
    const c = newConvObj();
    if (old?.msgs?.length) Object.assign(c, { session: old.session, msgs: old.msgs });
    store = { active: c.id, list: [c] };
    await saveStore(slug, store); // 立刻存下，保证编号稳定，不会重复迁移
  }
  if (!store.list.find((c) => c.id === store.active)) {
    if (!store.list.length) store.list.push(newConvObj());
    store.active = store.list[store.list.length - 1].id;
  }
  return store;
}
async function saveStore(slug, store) {
  await chrome.storage.local.set({ ["convs:" + slug]: store });
  await chrome.storage.local.remove("conv:" + slug);
}
// 当前会话（没有就建一个）
async function loadConv(slug) {
  const store = await loadStore(slug);
  return store.list.find((c) => c.id === store.active);
}
// 按 id 写回；这段会话可能已经不是当前的了（比如回答过程中切走了），照样存到它自己那里
async function saveConv(slug, conv) {
  const store = await loadStore(slug);
  conv.updated = Date.now();
  const i = store.list.findIndex((c) => c.id === conv.id);
  if (i >= 0) store.list[i] = conv; else store.list.push(conv);
  await saveStore(slug, store);
}
const convTitle = (c) => (c.msgs.find((m) => m.role === "user")?.text || "（空对话）").replace(/\s+/g, " ").slice(0, 40);
