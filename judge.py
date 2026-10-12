"""白板「提交」：把白板上手写的代码改写成力扣要的 class Solution，交给 leetcode.cn 判题。

流程（server.py 的 /board-submit 只负责接线）：
1. 查这道题的 questionId 和 python3 代码模板（lc_gql，缓存在 board/.questions/<slug>.lc.json），
   从模板里读出方法名、参数、返回值是不是 None、题目自带的节点类（ListNode / TreeNode / Node）
2. 用 ast 改写白板代码：删掉测试用的 print、调用、if __name__ == "__main__" 和自己写的节点类，
   再找出哪个函数是入口，末尾补一个 class Solution 转发过去。不调用模型，猜不出就判不出
3. 提交、每秒查一次结果（最多 60 秒），结果映射成 Accepted / WA / TLE / 运行出错 / 编译出错 / 判不出
4. 写一份 history/ 快照（类型「白板提交」，格式同 content.js），review.py 据此记分；
   结果存 board/.judge/<slug>.json，刷新白板能恢复。同样的代码一小时内不重交
"""
import ast
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
SITE = "https://leetcode.cn"
POLL_LIMIT = 60     # 秒；判题最多等这么久
RETRY_WAIT = 5      # 秒；429 或报错时等这么久再试一次
DUP_SECONDS = 3600  # 同样的代码这么久之内不重交
KIND = "白板提交"   # 快照里的类型，review.SUBMIT_KINDS 认它
VERDICTS = {"Accepted": "accepted", "Wrong Answer": "wa", "Time Limit Exceeded": "tle",
            "Runtime Error": "re", "Compile Error": "ce"}
CODES = {10: "accepted", 11: "wa", 14: "tle", 15: "re", 20: "ce"}  # status_msg 缺了时按 status_code


class CantJudge(Exception):
    """判不出：原因直接给用户看。"""


# ---------- 力扣接口 ----------
def lc_headers(cookies, referer=SITE + "/"):
    """和浏览器里一样的头，lc_gql 也用这个。"""
    return {
        "Content-Type": "application/json",
        "Cookie": f"LEETCODE_SESSION={cookies.get('session', '')}; csrftoken={cookies.get('csrf', '')}",
        "x-csrftoken": cookies.get("csrf", ""),
        "Referer": referer,
        "Origin": SITE,
        "User-Agent": UA,
    }


def lc_http(cookies, slug):
    """真的发请求：http(method, url, body) 返回 (状态码, JSON 或 None)。测试里换成假的。"""
    def http(method, url, body=None):
        if not cookies.get("session"):
            raise CantJudge("还没拿到力扣登录状态：请在浏览器里打开或刷新任意一道力扣题目")
        req = urllib.request.Request(url, method=method, headers=lc_headers(cookies, f"{SITE}/problems/{slug}/"),
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None
    return http


def question_meta(slug, gql, cache_dir):
    """{"questionId", "codeSnippets"}；不是力扣题返回 None。"""
    f = cache_dir / f"{slug}.lc.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    q = gql("""query($titleSlug:String!){question(titleSlug:$titleSlug){questionId codeSnippets{langSlug code}}}""",
            {"titleSlug": slug})["question"]
    if q:
        f.write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
    return q


# ---------- 读代码模板 ----------
def parse_snippet(snippet):
    """python3 模板解析成 {"method", "params", "list_param", "returns_none", "nodes": {类名: {属性}}}。"""
    nodes, cur = {}, None
    for line in snippet.splitlines():  # 模板开头注释里的节点类定义
        m = re.match(r"\s*#\s*class (\w+)", line)
        if m:
            cur = m[1]
            nodes[cur] = set()
            continue
        m = re.match(r"\s*#\s*self\.(\w+)\s*=", line)
        if m and cur:
            nodes[cur].add(m[1])
    # 模板里的方法体是空的，补个 pass 才能 parse
    body = re.sub(r"^([ \t]*)(def .*:)[ \t]*$", lambda m: f"{m[1]}{m[2]}\n{m[1]}    pass", snippet, flags=re.M)
    try:
        tree = ast.parse(body)
    except SyntaxError as e:
        raise CantJudge(f"读不懂这题的代码模板：{e}") from e
    sol = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Solution"), None)
    if sol is None:
        raise CantJudge("这题不是 class Solution 形式（设计类题），判不出")
    fns = [n for n in sol.body if isinstance(n, ast.FunctionDef)]
    if any(f.name == "__init__" for f in fns):
        raise CantJudge("这题要先构造 Solution 再调用（设计类题），判不出")
    fns = [f for f in fns if not f.name.startswith("_")]
    if not fns:
        raise CantJudge("代码模板里没有方法")
    fn = fns[0]
    args = fn.args.args[1:]  # 去掉 self
    ann = {a.arg: ast.unparse(a.annotation) if a.annotation else "" for a in args}
    params = [a.arg for a in args]
    return {"method": fn.name, "params": params,
            "list_param": next((p for p in params if re.match(r"(List|list)\[", ann[p])), None),
            "returns_none": isinstance(fn.returns, ast.Constant) and fn.returns.value is None,
            "nodes": nodes}


# ---------- 改写白板代码 ----------
def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _calls(node):
    """按出现顺序，直接按名字调用的函数。"""
    return [n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]


def _assigned(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}


def _is_main(node):
    """if __name__ == "__main__":（左右反过来也认）"""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare) or len(node.test.comparators) != 1:
        return False
    sides = [node.test.left, node.test.comparators[0]]
    return (any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
            and any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides))


def _self_attrs(cls):
    """节点类的属性：__init__ 里 self.x = ... 的 x；没有 __init__ 就看类体里的标注。"""
    init = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
    if init is None:
        return {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    targets = [t for n in ast.walk(init) if isinstance(n, (ast.Assign, ast.AnnAssign))
               for t in (n.targets if isinstance(n, ast.Assign) else [n.target])]
    targets += [e for t in targets if isinstance(t, ast.Tuple) for e in t.elts]  # self.a, self.b = a, b
    return {t.attr for t in targets if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"}


def _returns_value(fn):
    """函数自己（不算里面嵌套的函数）有没有 return 某个值。"""
    todo = list(fn.body)
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(n, ast.Return) and n.value is not None and not (isinstance(n.value, ast.Constant) and n.value.value is None):
            return True
        todo.extend(ast.iter_child_nodes(n))
    return False


def _fits(fn, n):
    """能不能用 n 个位置参数调用。"""
    a = fn.args
    total = len(a.posonlyargs) + len(a.args)
    need = total - len(a.defaults)
    return need <= n and (n <= total or a.vararg is not None)


_norm = lambda s: s.replace("_", "").lower()


def _pick(names, defs, sn):
    """几个候选里挑入口：只有一个就是它；多个就挑和力扣方法名一样的（忽略大小写和下划线）。"""
    if len(names) == 1:
        return names[0]
    same = [f for f in names if _norm(f) == _norm(sn["method"])]
    return same[0] if len(same) == 1 else None


def prepare(code, sn):
    """白板代码改写成 (交给力扣的代码, 一句说明)。猜不出入口就 raise CantJudge。

    删掉的行换成空行，力扣报错里的行号和白板对得上。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, "代码有语法错误，原样交上去"  # 力扣会判编译出错，和面试一样算交错一次
    sig = f"{sn['method']}({', '.join(sn['params'])})"
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    # 引用了这些名字的顶层语句都是测试代码：自己的函数、类，以及测试代码里赋值出来的变量
    tainted = set(defs) | set(classes)
    drop, called = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ClassDef):
            want = sn["nodes"].get(node.name)
            if want is not None:  # 题目自带的节点类：属性一样就删掉用力扣的，不一样判不出
                have = _self_attrs(node)
                if have != want:
                    raise CantJudge(f"你写的 {node.name} 属性是 {'/'.join(sorted(have)) or '（没有）'}，"
                                    f"力扣的是 {'/'.join(sorted(want))}，判题会对不上")
                drop.append(node)
            continue
        if _is_main(node) or _names(node) & tainted or "print" in _calls(node):
            drop.append(node)
            called += [c for c in _calls(node) if c in defs and c not in called]
            tainted |= _assigned(node)
        # 其余的留着：常量、sys.setrecursionlimit、全局的 memo 之类

    if "Solution" in classes:
        sol = classes["Solution"]
        if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == sn["method"] for n in sol.body):
            raise CantJudge(f"class Solution 里没有 {sn['method']} 方法（力扣要的是 {sig}）")
        entry, how = None, "白板里已有 class Solution，原样交"
    else:
        n = len(sn["params"])
        fit = lambda names: [f for f in names if _fits(defs[f], n)]
        entry = _pick(fit(called), defs, sn) if fit(called) else _pick(fit(defs), defs, sn)
        if entry is None:
            cands = fit(called) or fit(defs)
            raise CantJudge(f"猜不出哪个函数是 {sig}：{'、'.join(cands)} 都有可能，把测试调用写在文件末尾，或者改成 class Solution"
                            if cands else f"找不到能当 {sig} 用的函数（要能用 {n} 个参数调用）")
        how = f"入口：{entry}，包成 Solution.{sn['method']}"

    lines = code.split("\n")
    kept_lines = {l for n in tree.body if n not in drop for l in range(n.lineno, n.end_lineno + 1)}
    spans = [(min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])]), n.end_lineno) for n in drop]
    if any(l in kept_lines for a, b in spans for l in range(a, b + 1)):
        # 同一行里有留有删（用了分号），按行删不了，退回 ast.unparse（注释会丢，不影响判题）
        out = "\n".join(ast.unparse(n) for n in tree.body if n not in drop)
    else:
        for a, b in spans:
            for i in range(a - 1, b):
                lines[i] = ""
        out = "\n".join(lines)
    out = out.rstrip() + "\n"
    if entry:
        args = ", ".join(sn["params"])
        body = f"        return {entry}({args})\n"
        if sn["returns_none"] and _returns_value(defs[entry]):
            # 力扣要原地改（返回 None），白板写的是返回新结果：拷回第一个 list 参数
            target = sn["list_param"] or sn["params"][0]
            body = f"        r = {entry}({args})\n        if r is not None:\n            {target}[:] = r\n"
            how += f"，返回值拷回 {target}"
        out += f"\n\nclass Solution:\n    def {sn['method']}(self{', ' if args else ''}{args}):\n{body}"
    return out, how


def code_hash(code):
    """去掉空行和行尾空白再算，只改了测试 print 的代码算同一份。"""
    norm = "\n".join(l.rstrip() for l in code.splitlines() if l.strip())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


# ---------- 提交、查结果 ----------
def _call(http, method, url, body, sleep):
    """发一次请求，429 / 报错等 5 秒再试一次，还不行就判不出。"""
    for i in range(2):
        try:
            st, data = http(method, url, body)
        except CantJudge:
            raise
        except Exception as e:  # noqa: BLE001
            st, data = None, {"error": str(e)}
        if st == 200 and isinstance(data, dict) and not data.get("error"):
            return data
        if i == 0:
            sleep(RETRY_WAIT)
    why = (data or {}).get("error") if isinstance(data, dict) else None
    raise CantJudge(f"力扣没接受（{'HTTP ' + str(st) if st else '连不上'}{'：' + str(why) if why else ''}）"
                    + ("，可能是登录过期：刷新一下力扣页面" if st in (401, 403) else ""))


def submit(slug, question_id, typed, http, sleep=time.sleep):
    """交上去，等到判完，返回 check 接口的 JSON。"""
    d = _call(http, "POST", f"{SITE}/problems/{slug}/submit/",
              {"lang": "python3", "question_id": str(question_id), "typed_code": typed}, sleep)
    sid = d.get("submission_id")
    if not sid:
        raise CantJudge(f"力扣没给提交编号：{json.dumps(d, ensure_ascii=False)[:200]}")
    for _ in range(POLL_LIMIT):
        sleep(1)
        j = _call(http, "GET", f"{SITE}/submissions/detail/{sid}/check/", None, sleep)
        if j.get("state") == "SUCCESS":
            return j
    raise CantJudge(f"{POLL_LIMIT} 秒内力扣没判完，去力扣的提交记录里看")


def verdict(j):
    return VERDICTS.get(j.get("status_msg")) or CODES.get(j.get("status_code")) or "other"


def summarize(j):
    """给白板看的结果：出错只给输入，不给预期输出和你的输出（防剧透）。"""
    v = verdict(j)
    out = {"verdict": v, "status_msg": j.get("status_msg") or "未知",
           "passed": j.get("total_correct"), "total": j.get("total_testcases")}
    if v in ("wa", "tle", "re", "other") and j.get("last_testcase"):
        out["input"] = j["last_testcase"]
    if v == "re":
        out["error"] = j.get("full_runtime_error") or j.get("runtime_error") or ""
    if v == "ce":
        out["error"] = j.get("full_compile_error") or j.get("compile_error") or ""
    return out


# ---------- 快照（格式同 extension/content.js 的 resultMd） ----------
def _clip_line(line):
    if len(line) <= 600:
        return line
    items = line.count(",") + 1
    return f"{line[:300]} …（这一行共 {len(line)} 字{f'、约 {items} 个元素' if items > 1 else ''}，中间省略）… {line[-100:]}"


def fence(s, lang=""):
    return "```" + lang + "\n" + "\n".join(_clip_line(l) for l in str(s).rstrip("\n").split("\n")) + "\n```"


_arr = lambda v: "\n".join(map(str, v)) if isinstance(v, list) else (v or "")


def html_text(h):
    """题面 HTML 转纯文本（和 lc.py question 一样的粗处理）。"""
    t = re.sub(r"<[^>]+>", "", h or "")
    return t.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def snapshot_md(title, slug, desc, code, at, j=None, reason=None):
    """j：check 接口的 JSON；判不出时 j 为 None，reason 是原因。"""
    t = datetime.fromtimestamp(at)
    res = [f"- 类型：**{KIND}**（{t.year}/{t.month}/{t.day} {t:%H:%M:%S}）"]
    if j is None:
        res += ["- 结果：**判不出**", f"- 原因：{reason}"]
    else:
        res.append(f"- 结果：**{j.get('status_msg') or '未知'}**")
        if j.get("total_testcases") is not None:
            res.append(f"- 通过用例：{j.get('total_correct', '?')} / {j['total_testcases']}")
        if j.get("status_runtime") and j["status_runtime"] != "N/A":
            res.append(f"- 用时：{j['status_runtime']}，内存：{j.get('status_memory') or '?'}")
        err = j.get("full_compile_error") or j.get("compile_error") or j.get("full_runtime_error") or j.get("runtime_error")
        if err:
            res += ["", "### 报错", fence(err)]
        if j.get("last_testcase"):
            res += ["", "### 失败用例输入", fence(j["last_testcase"])]
        # 不写我的输出和预期输出：侧边栏能用 lc.py history-show 读快照，写了就等于把答案递过去
        stdout = _arr(j.get("std_output_list") or j.get("std_output") or "")
        if stdout.strip():
            res += ["", "### 标准输出（print）", fence(stdout)]
    return "\n".join([f"# {title}", "", f"链接：{SITE}/problems/{slug}/", "语言：python3", "",
                      "## 题目描述", desc or "_没有题面。_", "", "## 我的代码", fence(code, "python3"), "",
                      "## 最近一次运行 / 提交", *res, ""])


def write_snapshot(hist_dir, slug, md, at):
    """history/<时间>-<slug>.md；同一秒已有文件就往后挪一秒。返回实际用的时间戳。"""
    at = int(at)
    while (f := hist_dir / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(at))}-{slug}.md").exists():
        at += 1
    f.write_text(md, encoding="utf-8")
    return at


# ---------- 整个流程 ----------
_locks, _locks_guard = {}, threading.Lock()


def load_last(store_dir, slug):
    try:
        return json.loads((store_dir / f"{slug}.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def judge(slug, code, *, meta, http, store_dir, hist_dir, title, desc, now=time.time, sleep=time.sleep):
    """交一次。meta()：返回 question_meta 的结果。返回给白板的结果（也存进 store_dir/<slug>.json）。

    verdict：accepted / wa / tle / re / ce / other（其他没通过，比如 MLE）/ cant（判不出）/ busy（上一次还没判完）。"""
    with _locks_guard:
        lock = _locks.setdefault(slug, threading.Lock())
    if not lock.acquire(blocking=False):
        return {"verdict": "busy", "slug": slug}
    try:
        typed, how, h, j = None, "", None, None
        try:
            try:
                q = meta()
            except CantJudge:
                raise
            except Exception as e:  # noqa: BLE001  没登录状态 / 被拦
                raise CantJudge(f"查不到这题的代码模板：{e}") from e
            snip = next((s["code"] for s in (q or {}).get("codeSnippets") or [] if s["langSlug"] == "python3"), None)
            if not q or not snip:
                raise CantJudge("拿不到这题的 Python 代码模板（不是力扣题，或者是会员题）")
            typed, how = prepare(code, parse_snippet(snip))
            h = code_hash(typed)
            last = load_last(store_dir, slug)
            if last and last.get("hash") == h and last.get("verdict") != "cant" and now() - last.get("at", 0) < DUP_SECONDS:
                return last | {"dup": True}
            j = submit(slug, q["questionId"], typed, http, sleep)
            res = summarize(j)
        except CantJudge as e:
            res = {"verdict": "cant", "reason": str(e)}
        at = write_snapshot(hist_dir, slug, snapshot_md(title, slug, desc, code, now(), j, res.get("reason")), now())
        res |= {"slug": slug, "at": at, "hash": h, "how": how, "typed": typed}
        (store_dir / f"{slug}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        return res
    finally:
        lock.release()
