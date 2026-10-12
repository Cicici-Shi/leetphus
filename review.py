"""红黄绿复习表。

每道题的每次「做」都分一个等级：
- 绿：做出来之前没问过侧边栏
- 黄：问过（思路、报错、语法都算），或者 AC 之前交错过 1 次
- 红：侧边栏给过的代码和最后 AC 的代码大段重合，等于是看着答案写的；要过第 4 档提示（完整题解）；或者 AC 之前交错过 2 次以上
「交错」是力扣提交或白板提交的 WA / TLE / 运行出错 / 编译出错；白板判不出（根本没交上去）不算。

数据来源：
- 力扣上的 AC 提交、白板「提交」判出的 AC（history/ 里的快照）自动算一次；离上一次不到 1 小时的不算，免得重交一遍也被当成复习
- 白板上自己点绿/黄/红，记在 reviews.json；带 override 的是改刚记的那次的颜色，不另算一次
复习间隔看第一次做的等级；之后每复习一次往后走一格，复习时又红了就按红重新开始，走完就算掌握了。
"""
import difflib
import json
import re
import time
from datetime import date, datetime, timedelta

LADDER = {"green": [7, 30], "yellow": [1, 3, 7, 21], "red": [0, 1, 3, 7, 14, 30]}  # 第几天复习
RED_SIM = 0.6          # 侧边栏给的某段代码覆盖了 AC 代码的这么多行，就算红
MERGE_SECONDS = 3600   # 离上一次不到这么久的 AC 不另算一次
AUTO_LABEL = "提交通过 · 自动检查代码"  # AC 后的自动检查，不是用户问的
TALK_TAG = "【模拟面试：口述思路】"  # 白板里讲思路给「面试官」听，不算要提示
HINT4_TAG = "【提示 4/4"  # 白板「要提示」的第 4 档（完整题解），见 board.html 的 HINTS
SUBMIT_KINDS = ("提交", "白板提交")  # 快照里算「交了」的类型；「运行」「白板运行」不算
# 算「交错一次」的结果；判不出、未知不算
FAIL_RESULTS = ("Wrong Answer", "Time Limit Exceeded", "Runtime Error", "Compile Error",
                "Memory Limit Exceeded", "Output Limit Exceeded")


def _code_lines(code):
    return [s.strip() for s in code.splitlines() if s.strip() and not s.strip().startswith("#")]


def code_overlap(block, code):
    """block 里有多少行按顺序出现在 code 里，占 code 行数的比例。"""
    a, b = _code_lines(block), _code_lines(code)
    if not a or not b:
        return 0.0
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(x.size for x in m.get_matching_blocks()) / len(b)


def grade_of(answers, code):
    """answers：这次做题期间侧边栏的回答。"""
    if not answers:
        return "green"
    for a in answers:
        for blk in re.findall(r"```[\w+-]*\n([\s\S]*?)```", a):
            if code_overlap(blk, code) >= RED_SIM:
                return "red"
    return "yellow"


def grade_auto(answers, code, hint4=False, fails=0):
    """一次 AC 的等级和原因。answers 同 grade_of；hint4：要过第 4 档提示；fails：AC 之前交错了几次。"""
    g = grade_of(answers, code)
    why = []
    if g == "red":
        why.append("侧边栏给的代码和你交的大段重合")
    if hint4:
        g = "red"
        why.append("看了第 4 档提示（完整题解）")
    if fails >= 2:
        g = "red"
    elif fails == 1 and g == "green":
        g = "yellow"
    if fails:
        why.append(f"提交没过 {fails} 次")
    if answers:
        why.append(f"问过侧边栏 {len(answers)} 次")
    return g, "，".join(why) or "没问侧边栏，一次过"


# ---------- 读本地数据 ----------
def _submits(hist_dir):
    """history/ 里每份「交了」的快照：(slug, 时间戳, 结果, 快照全文)，按时间排。"""
    for f in sorted(hist_dir.glob("*.md")):
        m = re.match(r"(\d{8}-\d{6})-(.+)\.md$", f.name)
        if not m:
            continue
        t = f.read_text(encoding="utf-8")
        kind = re.search(r"类型：\*\*(.+?)\*\*", t)
        res = re.search(r"结果：\*\*(.+?)\*\*", t)
        if not kind or kind[1] not in SUBMIT_KINDS or not res:
            continue
        yield m[2], time.mktime(time.strptime(m[1], "%Y%m%d-%H%M%S")), res[1], t


def load_ac(hist_dir):
    """{slug: [(时间戳, AC 代码, 题目)]}，按时间排。力扣提交和白板提交都算。"""
    out = {}
    for slug, ts, res, t in _submits(hist_dir):
        if res != "Accepted":
            continue
        code = re.search(r"## 我的代码\n```[^\n]*\n([\s\S]*?)```", t)
        title = re.match(r"# (.+)", t)
        out.setdefault(slug, []).append((ts, code[1] if code else "", title[1].strip() if title else slug))
    return out


def load_fails(hist_dir):
    """{slug: [时间戳]}：每次交错（WA、TLE、运行出错、编译出错等）。"""
    out = {}
    for slug, ts, res, _ in _submits(hist_dir):
        if res in FAIL_RESULTS:
            out.setdefault(slug, []).append(ts)
    return out


def load_qa(qa_log):
    """{slug: [(时间戳, 回答, 题目, 问题)]}，去掉自动检查和口述。"""
    out = {}
    if not qa_log.exists():
        return out
    for line in qa_log.read_text(encoding="utf-8").splitlines():
        try:
            x = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not x.get("slug") or x.get("question", "").startswith((AUTO_LABEL, TALK_TAG)):
            continue
        ts = time.mktime(time.strptime(x["time"], "%Y-%m-%d %H:%M:%S"))
        out.setdefault(x["slug"], []).append((ts, x.get("answer", ""), x.get("title") or x["slug"], x.get("question", "")))
    return out


def load_manual(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def add_manual(path, slug, grade, at=None, override=False):
    """override：改刚记的那次（1 小时内）的颜色，不另算一次。"""
    rows = load_manual(path)
    rows.append({"slug": slug, "grade": grade, "at": at or time.time()} | ({"override": True} if override else {}))
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 算 ----------
def attempts(ac, qa, manual, fails=None):
    """{slug: [{"at", "grade", "kind", "why"}]}：每道题做过的每一次。fails 是 load_fails 的结果。"""
    out, fails = {}, fails or {}
    for slug in set(ac) | {r["slug"] for r in manual}:
        evs = [(t, "auto", code) for t, code, _ in ac.get(slug, [])]
        evs += [(r["at"], "override" if r.get("override") else "manual", r["grade"]) for r in manual if r["slug"] == slug]
        evs.sort(key=lambda e: e[0])
        res, prev = [], None
        for t, kind, x in evs:
            if kind == "override":
                if res and t - res[-1]["at"] < MERGE_SECONDS:  # 改刚记的那次，不另算
                    res[-1].update(grade=x, override=True, why="自己改的颜色")
                    continue
                kind = "manual"  # 前面没有能改的，就当自己打了一次分
            if kind == "auto" and prev is not None and t - prev < MERGE_SECONDS:
                continue
            if kind == "auto":
                # 窗口：上一次之后到这次 AC。问题要算 AC 那一刻的，交错的只算 AC 之前的
                qs = [q for q in qa.get(slug, []) if (prev is None or q[0] > prev) and q[0] <= t]
                n_fail = sum(1 for ft in fails.get(slug, []) if (prev is None or ft > prev) and ft < t)
                hint4 = any(len(q) > 3 and q[3].startswith(HINT4_TAG) for q in qs)
                g, why = grade_auto([q[1] for q in qs], x, hint4, n_fail)
            else:
                g, why = x, "自己打的分"
            res.append({"at": t, "grade": g, "kind": kind, "why": why})
            prev = t
        if res:
            out[slug] = res
    return out


def schedule(atts):
    """按做过的几次，算出现在用哪个间隔表、下次哪天复习（None = 已掌握）。"""
    day = lambda t: date.fromtimestamp(t)
    ladder, k, last = None, 0, None
    for a in atts:
        if ladder is None or a["grade"] == "red":
            ladder, k = LADDER[a["grade"]], 0
            due = day(a["at"]) + timedelta(days=ladder[0])
        else:
            k += 1
            due = None if k >= len(ladder) else day(a["at"]) + timedelta(days=ladder[k] - ladder[k - 1])
        last = a
    first = atts[0]["grade"]
    return {"first": first, "last": last["grade"], "last_at": last["at"], "step": k, "steps": len(ladder),
            "due": due.isoformat() if due else None}


def status(hist_dir, qa_log, manual_path, slug):
    """一道题现在的复习状态。"""
    atts = attempts(load_ac(hist_dir), load_qa(qa_log), load_manual(manual_path), load_fails(hist_dir)).get(slug)
    return schedule(atts) | {"attempt": atts[-1]} if atts else None


def build(hist_dir, qa_log, manual_path, today=None):
    today = today or date.today()
    ac, qa, manual = load_ac(hist_dir), load_qa(qa_log), load_manual(manual_path)
    titles = {s: v[-1][2] for s, v in qa.items()} | {s: v[-1][2] for s, v in ac.items()}
    due, upcoming, done = [], [], []
    for slug, atts in attempts(ac, qa, manual, load_fails(hist_dir)).items():
        s = schedule(atts) | {"slug": slug, "title": titles.get(slug, slug), "attempts": atts}
        if s["due"] is None:
            done.append(s)
        elif date.fromisoformat(s["due"]) <= today:
            s["overdue"] = (today - date.fromisoformat(s["due"])).days
            due.append(s)
        else:
            upcoming.append(s)
    worst = {"red": 0, "yellow": 1, "green": 2}
    due.sort(key=lambda s: (worst[s["last"]], -s["overdue"]))
    upcoming.sort(key=lambda s: s["due"])
    done.sort(key=lambda s: -s["last_at"])
    have = set(ac) | {r["slug"] for r in manual}
    unfinished = sorted(({"slug": s, "title": v[-1][2], "last_at": v[-1][0]} for s, v in qa.items() if s not in have),
                        key=lambda x: -x["last_at"])
    return {"today": today.isoformat(), "due": due, "upcoming": upcoming, "done": done, "unfinished": unfinished}


if __name__ == "__main__":  # 在终端里看一眼：python3 review.py
    from pathlib import Path
    B = Path(__file__).resolve().parent
    r = build(B / "history", B / "qa.jsonl", B / "reviews.json")
    cn = {"red": "红", "yellow": "黄", "green": "绿"}
    for k, name in (("due", "今天该复习"), ("upcoming", "接下来"), ("done", "已掌握")):
        print(f"== {name}（{len(r[k])}）")
        for s in r[k]:
            print(f"  {cn[s['first']]}→{cn[s['last']]}  第 {s['step'] + 1}/{s['steps']} 次  {s['due'] or '-'}  {s['title']}")
    print(f"== 没做完（{len(r['unfinished'])}）")
    for s in r["unfinished"]:
        print(f"  {datetime.fromtimestamp(s['last_at']):%m-%d}  {s['title']}")
