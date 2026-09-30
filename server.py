#!/usr/bin/env python3
"""leetcode.cn 插件的本地服务。

    python3 ~/leetcode-claude-bridge/server.py

1. /snapshot：接收插件发来的快照，写到本地文件，供 Claude Code 读取
    latest.md / latest.json / history/
2. /ask：侧边栏聊天，调用本机的 `claude -p`（用 Claude Code 的登录状态），流式返回回答。
   每次都会把 profile.md（用户偏好档案）放进系统提示。
3. 偏好学习：每次问答记到 qa.jsonl；遇到纠正/「记住」类的话，或每累计 N 个问题，
   在后台让 Claude 根据最近问答重写 profile.md。旧版本备份在 profile_history/。
   /profile 可读可写（侧边栏的「偏好」页），/reflect 立即整理一次。
"""
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST, PORT = "127.0.0.1", 8765
BASE = Path(__file__).resolve().parent
HIST = BASE / "history"
PROFILE = BASE / "profile.md"
PROFILE_HIST = BASE / "profile_history"
QA_LOG = BASE / "qa.jsonl"
STATE = BASE / "learn_state.json"
for d in (HIST, PROFILE_HIST):
    d.mkdir(exist_ok=True)
CLAUDE = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
CLAUDE_FLAGS = ["--tools", "", "--strict-mcp-config", "--setting-sources", ""]
LC_TOOL = f"python3 {BASE}/lc.py"
# 侧边栏提问时开放的工具：只允许运行 lc.py（查力扣），以及搜索/读网页
ASK_FLAGS = [
    "--tools", "Bash,WebSearch,WebFetch",
    "--allowedTools", f"Bash({LC_TOOL}:*)", "WebSearch", "WebFetch",
    "--strict-mcp-config", "--setting-sources", "",
]
COOKIES = {}  # leetcode.cn 登录 cookie，由插件发来，只放内存

REFLECT_EVERY = 5          # 每累计多少个问题整理一次
REFLECT_MODEL = "sonnet"   # 整理偏好用的模型
# 像是在纠正或提要求的话 → 马上整理
CORRECTION_RE = re.compile(
    r"记住|以后|下次|别再|不要(?:给|告诉|直接|提醒|讲|说|写|用)|不用(?:给|告诉|直接|提醒|讲|说|写)|别给|别直接|太长|太短|太啰嗦|啰嗦|简单点|详细点|说错|不对|错了吧|我是说|我想要|我要的是|偏好|习惯|换个方式|用.{0,6}语言|用 ?(python|java|c\+\+|go|js)",
    re.I,
)

SYSTEM_PROMPT_T = """你是用户刷 leetcode.cn 时的算法辅导助手，界面是浏览器侧边栏。
- 始终用中文回答；代码、术语保留原文。
- 每条用户消息后面会附上用户当前的代码和最近一次运行/提交结果，以它为准，不要凭空假设用户写了什么。
- 用户问报错或错误答案时：先指出具体哪一行、为什么错，再给最小改动；除非用户要求，不要直接给完整题解。
- 【防剧透】看附带的「最近一次运行 / 提交」：只要这道题还没有提交 Accepted，就只回答用户问的那一个问题。
  即使你看出了代码里别的错误、漏掉的初始化、还没改的地方，也不要提、不要「顺便提醒」、不要在结尾暗示；
  讲思路或另一种解法时，也不要拿来对照指出用户当前代码哪里不对。让用户自己发现。用户明确问「还有哪里错」时才说。
  提交 Accepted 之后没有这个限制。
- 回答简洁，侧边栏较窄，少用大表格。
- 你可以自己去查力扣数据，用 Bash 运行下面的命令（必须原样以 `{lc}` 开头，不能加 cd、管道或其他命令）：
  - `{lc} submissions [题目slug]`：用户在这道题的提交记录（不填就是当前题）
  - `{lc} submission <提交id>`：某次提交的代码、结果、通过用例数
  - `{lc} solutions [题目slug] [--n 5]`：社区题解列表（按热度）
  - `{lc} solution <题解slug>`：某篇题解全文
  - `{lc} question <题目slug>`：其他题目的题面
  - `{lc} history [题目slug]` / `{lc} history-show <文件名>`：本地记录的每次运行/提交快照，含失败用例、输出和报错
  用户问到提交记录、以前的写法、别人的解法、其他题、之前错在哪时，先去查，不要说看不到。引用题解时用自己的话讲思路和关键几行，不要整篇照搬。
- 也可以用 WebSearch / WebFetch 查语法、标准库文档。
- 下面的「用户偏好档案」是从用户过去的提问和纠正中总结的，与上面的默认规则冲突时，以档案为准。"""

SYSTEM_PROMPT = SYSTEM_PROMPT_T.replace("{lc}", LC_TOOL)

REFLECT_PROMPT = """你在维护一份「用户偏好档案」，给一个 leetcode 刷题辅导助手用。助手每次回答前都会读这份档案。

下面是现有档案，以及用户最近的问答记录（按时间顺序，最后的最新）。请输出更新后的完整档案。

规则：
- 只写有证据的内容：用户明确说过的要求、纠正过助手的地方、反复出现的提问类型。不要猜测性格，不要写空话。
- 用户明确的纠正和「记住/以后…」类要求优先级最高，写进「明确要求」，措辞尽量贴近用户原话。
- 新证据和旧条目矛盾时，以新的为准，删掉旧的。
- 条目要可执行，例如「解释错误时先给出错的行号」，而不是「用户喜欢清晰的解释」。
- 总共不超过 25 条，每条一行。没有内容的小节写「（暂无）」。
- 用中文。只输出档案本身，放在 <profile> 和 </profile> 之间，不要其他内容。

格式：
<profile>
## 明确要求
- ...
## 回答风格偏好
- ...
## 常问的问题类型
- ...
## 背景
- （用的编程语言、在跟的学习计划、薄弱点等）
</profile>

===== 现有档案 =====
{profile}

===== 最近的问答 =====
{qa}
"""

_reflect_lock = threading.Lock()


def read_profile():
    return PROFILE.read_text(encoding="utf-8") if PROFILE.exists() else ""


def write_profile(text, reason):
    old = read_profile()
    if old.strip():
        ts = time.strftime("%Y%m%d-%H%M%S")
        (PROFILE_HIST / f"{ts}-{reason}.md").write_text(old, encoding="utf-8")
    PROFILE.write_text(text.strip() + "\n", encoding="utf-8")


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {"since_reflect": 0, "last_reflect": None, "reflecting": False}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False))


def recent_qa(n=20):
    if not QA_LOG.exists():
        return []
    lines = QA_LOG.read_text(encoding="utf-8").strip().splitlines()[-n:]
    return [json.loads(l) for l in lines if l.strip()]


def reflect(reason="auto"):
    """让 Claude 根据最近问答重写偏好档案。在后台线程里跑。"""
    if not _reflect_lock.acquire(blocking=False):
        return  # 已经在整理了
    st = load_state()
    st["reflecting"] = True
    save_state(st)
    try:
        qa = recent_qa()
        if not qa:
            return
        qa_text = "\n\n".join(
            f"[{x['time']}] 题目：{x.get('title', '')}\n用户：{x['question']}\n助手：{x['answer'][:600]}"
            for x in qa
        )
        prompt = REFLECT_PROMPT.format(profile=read_profile() or "（空）", qa=qa_text)
        r = subprocess.run(
            [CLAUDE, "-p", prompt, "--model", REFLECT_MODEL, "--output-format", "text", *CLAUDE_FLAGS],
            cwd=BASE, capture_output=True, text=True, timeout=180,
        )
        m = re.search(r"<profile>([\s\S]*?)</profile>", r.stdout)
        if m and m.group(1).strip():
            write_profile(m.group(1), reason)
            log(f"偏好档案已更新（{reason}）")
        else:
            log(f"整理偏好失败：{(r.stderr or r.stdout)[-300:]}")
    except Exception as e:  # noqa: BLE001
        log(f"整理偏好出错：{e}")
    finally:
        st = load_state()
        st.update(reflecting=False, since_reflect=0, last_reflect=time.strftime("%Y-%m-%d %H:%M:%S"))
        save_state(st)
        _reflect_lock.release()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def tool_status(c):
    """把 Claude 的工具调用翻译成一句给用户看的状态。"""
    name, inp = c.get("name"), c.get("input", {})
    if name == "WebSearch":
        return f"正在搜索：{inp.get('query', '')}"
    if name == "WebFetch":
        return "正在读网页…"
    cmd = inp.get("command", "")
    for k, v in (("history", "正在翻本地记录…"), ("submission", "正在看你的提交记录…"),
                 ("solution", "正在看社区题解…"), ("question", "正在查题目…")):
        if f"lc.py {k}" in cmd:
            return v
    return "正在查资料…"


# ---------- 力扣查询（lc.py 的后端） ----------
def lc_gql(query, variables):
    if not COOKIES.get("session"):
        raise RuntimeError("还没拿到力扣登录状态：请在浏览器里打开或刷新任意一道力扣题目")
    req = urllib.request.Request(
        "https://leetcode.cn/graphql/",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Content-Type": "application/json",
            "Cookie": f"LEETCODE_SESSION={COOKIES['session']}; csrftoken={COOKIES.get('csrf', '')}",
            "x-csrftoken": COOKIES.get("csrf", ""),
            "Referer": "https://leetcode.cn/",
            "Origin": "https://leetcode.cn",
            "User-Agent": "Mozilla/5.0 (Macintosh) leetcode-claude-bridge",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    if data.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "") for e in data["errors"]))
    return data["data"]


def current_slug():
    try:
        return json.loads((BASE / "latest.json").read_text())["slug"]
    except Exception:  # noqa: BLE001
        return ""


def lc_command(cmd, args, opts):
    slug = args[0] if args else current_slug()
    n = int(opts.get("n", 5))
    if cmd == "submissions":
        d = lc_gql("""query($offset:Int!,$limit:Int!,$questionSlug:String!){submissionList(offset:$offset,limit:$limit,questionSlug:$questionSlug){hasNext submissions{id statusDisplay lang runtime memory timestamp}}}""",
                   {"offset": int(opts.get("skip", 0)), "limit": int(opts.get("n", 20)), "questionSlug": slug})["submissionList"]
        rows = [f"{s['id']}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(int(s['timestamp'])))}  {s['statusDisplay']:<22} {s['lang']:<10} {s['runtime']}  {s['memory']}"
                for s in d["submissions"]]
        return f"题目 {slug} 的提交记录（新→旧）：\n" + ("\n".join(rows) or "（没有提交）") + ("\n…还有更多，用 --skip 翻页" if d["hasNext"] else "") + "\n"
    if cmd == "submission":
        s = lc_gql("""query($submissionId:ID!){submissionDetail(submissionId:$submissionId){code timestamp statusDisplay runtime memory lang passedTestCaseCnt totalTestCaseCnt question{titleSlug}}}""",
                   {"submissionId": args[0]})["submissionDetail"]
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(s["timestamp"])))
        return (f"题目：{s['question']['titleSlug']}  时间：{when}\n结果：{s['statusDisplay']}  通过用例 {s['passedTestCaseCnt']}/{s['totalTestCaseCnt']}  用时 {s['runtime']}  内存 {s['memory']}\n"
                f"语言：{s['lang']}\n```\n{s['code']}\n```\n（失败用例的详细输入输出看 lc.py history）\n")
    if cmd == "solutions":
        d = lc_gql("""query($questionSlug:String!,$first:Int,$skip:Int){questionSolutionArticles(questionSlug:$questionSlug,first:$first,skip:$skip,orderBy:DEFAULT){totalNum edges{node{title slug upvoteCount author{username profile{realName}}}}}}""",
                   {"questionSlug": slug, "first": n, "skip": int(opts.get("skip", 0))})["questionSolutionArticles"]
        rows = [f"- {e['node']['title']}  （作者 {(e['node']['author']['profile'] or {}).get('realName') or e['node']['author']['username']}，{e['node']['upvoteCount']} 赞）  slug: {e['node']['slug']}"
                for e in d["edges"]]
        return f"题目 {slug} 共 {d['totalNum']} 篇题解，热门前 {len(rows)} 篇：\n" + "\n".join(rows) + "\n"
    if cmd == "solution":
        a = lc_gql("""query($slug:String!){solutionArticle(slug:$slug,orderBy:DEFAULT){title content upvoteCount author{username}}}""",
                   {"slug": args[0]})["solutionArticle"]
        content = a["content"]
        if len(content) > 12000:
            content = content[:12000] + "\n…（太长，已截断）"
        return f"# {a['title']}（{a['author']['username']}，{a['upvoteCount']} 赞）\n\n{content}\n"
    if cmd == "question":
        q = lc_gql("""query($titleSlug:String!){question(titleSlug:$titleSlug){questionFrontendId translatedTitle difficulty translatedContent topicTags{translatedName}}}""",
                   {"titleSlug": slug})["question"]
        body = re.sub(r"<[^>]+>", "", q["translatedContent"] or "")
        body = body.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        tags = "、".join(t["translatedName"] for t in q["topicTags"])
        return f"{q['questionFrontendId']}. {q['translatedTitle']}（{q['difficulty']}）标签：{tags}\n\n{body.strip()}\n"
    if cmd == "history":
        files = sorted(HIST.glob(f"*-{slug}.md" if args else "*.md"), reverse=True)[:30]
        rows = []
        for f in files:
            txt = f.read_text(encoding="utf-8")
            kind = re.search(r"类型：\*\*(.+?)\*\*", txt)
            res = re.search(r"结果：\*\*(.+?)\*\*", txt)
            rows.append(f"{f.name}  {kind.group(1) if kind else '?'}  {res.group(1) if res else '?'}")
        return "本地快照（新→旧，用 history-show <文件名> 查看）：\n" + ("\n".join(rows) or "（没有记录）") + "\n"
    if cmd == "history-show":
        f = HIST / Path(args[0]).name
        if not f.exists():
            raise RuntimeError("没有这个文件")
        txt = f.read_text(encoding="utf-8")
        return re.sub(r"## 题目描述[\s\S]*?(?=## 我的代码)", "", txt)
    raise RuntimeError(f"不认识的命令 {cmd}，用 lc.py --help 查看用法")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _reply(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._reply(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/latest"):
            f = BASE / "latest.md"
            self._reply(200, f.read_bytes() if f.exists() else "还没有数据".encode(), "text/markdown; charset=utf-8")
        elif self.path == "/profile":
            st = load_state()
            self._json({
                "profile": read_profile(),
                "reflecting": st.get("reflecting", False),
                "last_reflect": st.get("last_reflect"),
                "since_reflect": st.get("since_reflect", 0),
                "every": REFLECT_EVERY,
                "total_questions": len(QA_LOG.read_text(encoding="utf-8").splitlines()) if QA_LOG.exists() else 0,
            })
        else:
            self._reply(404)

    def do_POST(self):
        try:
            body = self._json_body()
        except Exception as e:  # noqa: BLE001
            return self._reply(400, str(e).encode())
        if self.path == "/snapshot":
            return self.snapshot(body)
        if self.path == "/ask":
            return self.ask(body)
        if self.path == "/profile":
            write_profile(body.get("profile", ""), "manual")
            log("偏好档案已手动修改")
            return self._json({"ok": True})
        if self.path == "/cookies":
            if body.get("session"):
                COOKIES.update(session=body["session"], csrf=body.get("csrf", ""))
            return self._reply(200, b"ok")
        if self.path == "/lc":
            try:
                out = lc_command(body.get("cmd", ""), body.get("args", []), body.get("opts", {}))
                return self._reply(200, out.encode())
            except Exception as e:  # noqa: BLE001
                return self._reply(400, f"出错：{e}\n".encode())
        if self.path == "/reflect":
            threading.Thread(target=reflect, args=("manual-reflect",), daemon=True).start()
            return self._json({"ok": True})
        self._reply(404)

    # ---------- 快照 ----------
    def snapshot(self, snap):
        md = snap.get("markdown", "")
        (BASE / "latest.md").write_text(md, encoding="utf-8")
        (BASE / "latest.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        if snap.get("reason") == "result":
            ts = time.strftime("%Y%m%d-%H%M%S")
            (HIST / f"{ts}-{snap.get('slug', 'unknown')}.md").write_text(md, encoding="utf-8")
        status = ((snap.get("result") or {}).get("raw") or {}).get("status_msg", "")
        log(f"{snap.get('reason')}: {snap.get('title')} {status}")
        self._reply(200, b"ok")

    # ---------- 提问 ----------
    def ask(self, body):
        question = (body.get("question") or "").strip()
        context = body.get("context") or ""
        session = body.get("session")
        prompt = f"{question}\n\n---\n以下是我当前的页面状态（自动附带）：\n\n{context}"
        profile = read_profile().strip()
        system = SYSTEM_PROMPT + ("\n\n# 用户偏好档案\n" + profile if profile else "")

        cmd = [
            CLAUDE, "-p", prompt,
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            *ASK_FLAGS, "--append-system-prompt", system,
        ]
        if body.get("model"):
            cmd += ["--model", body["model"]]
        if body.get("effort") in ("low", "medium", "high"):
            cmd += ["--effort", body["effort"]]
        if session:
            cmd += ["--resume", session]

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj):
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()

        log(f"ask{'（自动检查）' if body.get('auto') else ''}: {question[:30]}")
        t0 = time.time()
        proc = subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        answer = ""
        used_tool = False
        try:
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    emit({"session": ev.get("session_id")})
                elif ev.get("type") == "stream_event":
                    d = ev.get("event", {}).get("delta", {})
                    if d.get("type") == "text_delta":
                        chunk = d.get("text", "")
                        if used_tool and answer and not answer.endswith("\n\n"):
                            chunk = "\n\n" + chunk  # 查完资料后接着写，和前面的话分开
                        used_tool = False
                        answer += chunk
                        emit({"t": chunk})
                elif ev.get("type") == "assistant":
                    for c in ev.get("message", {}).get("content", []):
                        if c.get("type") == "tool_use":
                            used_tool = True
                            emit({"status": tool_status(c)})
                elif ev.get("type") == "result" or "duration_api_ms" in ev:
                    if ev.get("is_error") and not answer:
                        emit({"error": str(ev.get("result") or "claude 返回错误")})
            proc.wait()
            if proc.returncode and not answer:
                err = proc.stderr.read().strip()[-800:]
                emit({"error": err or f"claude 退出码 {proc.returncode}"})
            # 自动检查不是用户自己问的，不记入偏好学习
            log(f"回答完成，用时 {time.time() - t0:.0f} 秒，{len(answer)} 字")
            learning = self.record(body, question, answer) if answer and not body.get("auto") else None
            emit({"done": True, "learning": learning})
        except (BrokenPipeError, ConnectionResetError):
            # 用户点了「停止」或关了侧边栏；已经生成的部分仍然记下来
            if answer and not body.get("auto"):
                self.record(body, question, answer)
        finally:
            if proc.poll() is None:
                proc.kill()

    def record(self, body, question, answer):
        """记日志；需要时在后台整理偏好。返回触发原因（没触发则 None）。"""
        with QA_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "slug": body.get("slug"), "title": body.get("title"),
                "question": question, "answer": answer,
            }, ensure_ascii=False) + "\n")
        st = load_state()
        st["since_reflect"] = st.get("since_reflect", 0) + 1
        save_state(st)
        reason = None
        if CORRECTION_RE.search(question):
            reason = "correction"
        elif st["since_reflect"] >= REFLECT_EVERY:
            reason = "periodic"
        if reason:
            threading.Thread(target=reflect, args=(reason,), daemon=True).start()
        return reason

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    st = load_state()
    if st.get("reflecting"):  # 上次整理到一半被中断
        st["reflecting"] = False
        save_state(st)
    log(f"LeetCode bridge 监听 http://{HOST}:{PORT} ，目录 {BASE} ，claude: {CLAUDE}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
