"""python3 -m unittest test_judge（不联网：力扣接口全是假的）"""
import ast
import tempfile
import unittest
from pathlib import Path

import judge
import review

SUBSETS = """class Solution:
    def subsets(self, nums: List[int]) -> List[List[int]]:
        """
REVERSE = """# Definition for singly-linked list.
# class ListNode:
#     def __init__(self, val=0, next=None):
#         self.val = val
#         self.next = next
class Solution:
    def reverseList(self, head: Optional[ListNode]) -> Optional[ListNode]:
        """
TREE = """# Definition for a binary tree node.
# class TreeNode:
#     def __init__(self, val=0, left=None, right=None):
#         self.val = val
#         self.left = left
#         self.right = right
class Solution:
    def maxDepth(self, root: Optional[TreeNode]) -> int:
        """
ROTATE = """class Solution:
    def rotate(self, nums: List[int], k: int) -> None:
        \"\"\"
        Do not return anything, modify nums in-place instead.
        \"\"\"
        """
LRU = """class LRUCache:

    def __init__(self, capacity: int):


    def get(self, key: int) -> int:

"""

BOARD = """import sys
sys.setrecursionlimit(10000)
LIMIT = 10


def sub_sets(nums):
    res, track = [], []
    def bt(i):
        res.append(track[:])
        for j in range(i, len(nums)):
            track.append(nums[j])
            bt(j + 1)
            track.pop()
    bt(0)
    return res


tests = [[1, 2, 3], [0]]
out = sub_sets([1, 2, 3])
print(out)
for t in tests:
    print(sub_sets(t))
assert len(out) == 8
if __name__ == "__main__":
    print(sub_sets([0]))
"""


def body_names(code):
    """改写后顶层还剩下的东西：函数/类名，其他语句用 unparse 的文本。"""
    return [n.name if isinstance(n, (ast.FunctionDef, ast.ClassDef)) else ast.unparse(n) for n in ast.parse(code).body]


class Snippet(unittest.TestCase):
    def test_plain(self):
        s = judge.parse_snippet(SUBSETS)
        self.assertEqual((s["method"], s["params"], s["list_param"], s["returns_none"], s["nodes"]),
                         ("subsets", ["nums"], "nums", False, {}))

    def test_nodes_and_returns_none(self):
        self.assertEqual(judge.parse_snippet(REVERSE)["nodes"], {"ListNode": {"val", "next"}})
        self.assertEqual(judge.parse_snippet(TREE)["nodes"], {"TreeNode": {"val", "left", "right"}})
        s = judge.parse_snippet(ROTATE)
        self.assertEqual((s["params"], s["returns_none"]), (["nums", "k"], True))

    def test_design_problem_cant(self):
        with self.assertRaises(judge.CantJudge):
            judge.parse_snippet(LRU)


class Strip(unittest.TestCase):
    def test_keeps_imports_constants_drops_tests(self):
        code, how = judge.prepare(BOARD, judge.parse_snippet(SUBSETS))
        self.assertEqual(body_names(code), ["import sys", "sys.setrecursionlimit(10000)", "LIMIT = 10", "sub_sets",
                                            "tests = [[1, 2, 3], [0]]", "Solution"])
        self.assertIn("入口：sub_sets", how)
        # 删掉的行换成空行，行号和白板一致
        self.assertEqual(code.split("\n")[5], "def sub_sets(nums):")

    def test_wrapper_runs(self):
        code, _ = judge.prepare(BOARD, judge.parse_snippet(SUBSETS))
        ns = {}
        exec(code, ns)  # 删干净了：执行时不会 print，也不会因为 assert 出错
        self.assertEqual(len(ns["Solution"]().subsets([1, 2, 3])), 8)

    def test_tainted_variables_follow(self):
        b = "def f(nums):\n    return nums\n\nhead = f([1])\nx = head[0]\ny = 3\n"
        code, _ = judge.prepare(b, judge.parse_snippet(SUBSETS))
        self.assertEqual(body_names(code), ["f", "y = 3", "Solution"])

    def test_syntax_error_goes_as_is(self):
        code, how = judge.prepare("def f(:\n", judge.parse_snippet(SUBSETS))
        self.assertEqual(code, "def f(:\n")
        self.assertIn("语法错误", how)


class NodeClasses(unittest.TestCase):
    def test_matching_list_node_is_dropped(self):
        b = ("class ListNode:\n    def __init__(self, val=0, next=None):\n        self.val = val\n        self.next = next\n\n"
             "def reverse(head):\n    prev = None\n    while head:\n        head.next, prev, head = prev, head, head.next\n    return prev\n\n"
             "h = ListNode(1, ListNode(2))\nprint(reverse(h).val)\n")
        code, _ = judge.prepare(b, judge.parse_snippet(REVERSE))
        self.assertEqual(body_names(code), ["reverse", "Solution"])

    def test_matching_tree_node_is_dropped(self):
        b = ("class TreeNode:\n    def __init__(self, val, left=None, right=None):\n        self.val, self.left, self.right = val, left, right\n"
             "def depth(root):\n    return 0 if not root else 1 + max(depth(root.left), depth(root.right))\n")
        # self.val, self.left, self.right = ... 是元组赋值，也要认出来
        for src in (b, b.replace("self.val, self.left, self.right = val, left, right",
                                 "self.val = val\n        self.left = left\n        self.right = right")):
            code, _ = judge.prepare(src, judge.parse_snippet(TREE))
            self.assertEqual(body_names(code), ["depth", "Solution"])

    def test_wrong_attr_names_cant(self):
        b = ("class ListNode:\n    def __init__(self, x):\n        self.data = x\n        self.nxt = None\n"
             "def reverse(head):\n    return head\n")
        with self.assertRaisesRegex(judge.CantJudge, "data/nxt"):
            judge.prepare(b, judge.parse_snippet(REVERSE))

    def test_node_class_not_in_snippet_is_kept(self):
        b = "class ListNode:\n    def __init__(self, v):\n        self.v = v\ndef sub(nums):\n    return [[]]\n"
        code, _ = judge.prepare(b, judge.parse_snippet(SUBSETS))
        self.assertEqual(body_names(code), ["ListNode", "sub", "Solution"])


class Entry(unittest.TestCase):
    S = judge.parse_snippet(SUBSETS)

    def entry(self, b):
        return judge.prepare(b, self.S)[1]

    def test_existing_solution_used_as_is(self):
        b = "class Solution:\n    def subsets(self, nums):\n        return [[]]\n\nprint(Solution().subsets([1]))\n"
        code, how = judge.prepare(b, self.S)
        self.assertEqual(body_names(code), ["Solution"])
        self.assertIn("原样", how)

    def test_solution_without_method_cant(self):
        with self.assertRaisesRegex(judge.CantJudge, "没有 subsets"):
            self.entry("class Solution:\n    def sub(self, nums):\n        return [[]]\n")

    def test_called_def_wins_over_arity(self):
        b = "def helper(xs):\n    return xs\n\ndef go(nums):\n    return [helper(nums)]\n\nprint(go([1]))\n"
        self.assertIn("入口：go", self.entry(b))

    def test_called_ignores_wrong_arity(self):
        # 测试代码调用了 build（两个参数）和 go，只有 go 能当 subsets(nums)
        b = "def build(a, b):\n    return a\n\ndef go(nums):\n    return [nums]\n\nx = build(1, 2)\nprint(go([1]))\n"
        self.assertIn("入口：go", self.entry(b))

    def test_unique_arity_when_nothing_called(self):
        b = "def bt(nums, i, track):\n    pass\n\ndef solve(nums):\n    return [[]]\n"
        self.assertIn("入口：solve", self.entry(b))

    def test_defaults_count(self):
        b = "def solve(nums, start=0):\n    return [[]]\n"
        self.assertIn("入口：solve", self.entry(b))

    def test_ambiguous_cant(self):
        b = "def a(nums):\n    return nums\n\ndef b(nums):\n    return nums\n"
        with self.assertRaisesRegex(judge.CantJudge, "a、b"):
            self.entry(b)

    def test_ambiguous_broken_by_method_name(self):
        b = "def build(nums):\n    return nums\n\ndef sub_sets(nums):\n    return [nums]\n\nprint(sub_sets(build([1])))\n"
        self.assertIn("入口：sub_sets", self.entry(b))

    def test_no_candidate_cant(self):
        with self.assertRaisesRegex(judge.CantJudge, "找不到"):
            self.entry("x = 1\n")


class Wrapper(unittest.TestCase):
    def test_copy_back_into_first_list(self):
        b = "def rot(nums, k):\n    k %= len(nums)\n    return nums[-k:] + nums[:-k]\n"
        code, how = judge.prepare(b, judge.parse_snippet(ROTATE))
        self.assertIn("nums[:] = r", code)
        self.assertIn("拷回 nums", how)
        ns = {}
        exec(code, ns)
        a = [1, 2, 3, 4, 5]
        self.assertIsNone(ns["Solution"]().rotate(a, 2))
        self.assertEqual(a, [4, 5, 1, 2, 3])

    def test_in_place_user_code_no_copy(self):
        b = "def rot(nums, k):\n    nums.reverse()\n"
        code, _ = judge.prepare(b, judge.parse_snippet(ROTATE))
        self.assertNotIn("[:] = r", code)

    def test_nested_return_doesnt_count(self):
        b = "def rot(nums, k):\n    def h():\n        return 1\n    nums.reverse()\n"
        self.assertNotIn("[:] = r", judge.prepare(b, judge.parse_snippet(ROTATE))[0])


class Verdicts(unittest.TestCase):
    def test_five_states(self):
        for msg, v in (("Accepted", "accepted"), ("Wrong Answer", "wa"), ("Time Limit Exceeded", "tle"),
                       ("Runtime Error", "re"), ("Compile Error", "ce"), ("Memory Limit Exceeded", "other")):
            self.assertEqual(judge.verdict({"status_msg": msg}), v)
        self.assertEqual(judge.verdict({"status_code": 11}), "wa")

    def test_wa_gives_input_only(self):
        s = judge.summarize({"status_msg": "Wrong Answer", "last_testcase": "[1,2]", "code_output": "[[]]",
                             "expected_output": "[[],[1]]", "total_correct": 3, "total_testcases": 10})
        self.assertEqual(s["input"], "[1,2]")
        self.assertNotIn("[[],[1]]", str(s))
        self.assertNotIn("[[]]", str(s))

    def test_re_and_ce_carry_error(self):
        self.assertEqual(judge.summarize({"status_msg": "Runtime Error", "full_runtime_error": "IndexError"})["error"], "IndexError")
        self.assertEqual(judge.summarize({"status_msg": "Compile Error", "compile_error": "SyntaxError"})["error"], "SyntaxError")


class FakeLC:
    """假的力扣：按顺序回放响应，记下请求。"""
    def __init__(self, *resp):
        self.resp, self.calls = list(resp), []

    def __call__(self, method, url, body=None):
        self.calls.append((method, url, body))
        r = self.resp.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


AC = {"state": "SUCCESS", "status_msg": "Accepted", "status_code": 10, "total_correct": 10, "total_testcases": 10,
      "status_runtime": "0 ms", "status_memory": "16 MB"}
WA = {"state": "SUCCESS", "status_msg": "Wrong Answer", "status_code": 11, "total_correct": 3, "total_testcases": 10,
      "last_testcase": "[1,2,3]", "code_output": "[[]]", "expected_output": "[[],[1]]"}
META = {"questionId": "78", "codeSnippets": [{"langSlug": "cpp", "code": "x"}, {"langSlug": "python3", "code": SUBSETS}]}


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.store, self.hist = d / "judge", d / "history"
        self.store.mkdir()
        self.hist.mkdir()
        self.t = 1760000000.0
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_judge(self, http, code=BOARD, meta=lambda: META):
        return judge.judge("subsets", code, meta=meta, http=http, store_dir=self.store, hist_dir=self.hist,
                           title="78. 子集", desc="题面", now=lambda: self.t, sleep=self.sleeps.append)

    def test_submit_and_poll(self):
        lc = FakeLC((200, {"submission_id": 99}), (200, {"state": "PENDING"}), (200, {"state": "STARTED"}), (200, AC))
        r = self.run_judge(lc)
        self.assertEqual(r["verdict"], "accepted")
        m, url, body = lc.calls[0]
        self.assertEqual((m, url), ("POST", "https://leetcode.cn/problems/subsets/submit/"))
        self.assertEqual((body["lang"], body["question_id"]), ("python3", "78"))
        self.assertIn("class Solution", body["typed_code"])
        self.assertEqual(lc.calls[1][1], "https://leetcode.cn/submissions/detail/99/check/")
        self.assertEqual(self.sleeps, [1, 1, 1])
        self.assertEqual(judge.load_last(self.store, "subsets")["verdict"], "accepted")

    def test_snapshot_read_back_by_review(self):
        self.run_judge(FakeLC((200, {"submission_id": 1}), (200, AC)))
        ac = review.load_ac(self.hist)["subsets"]
        self.assertEqual(len(ac), 1)
        self.assertEqual(ac[0][1].rstrip("\n"), BOARD.rstrip("\n"))  # 快照里是白板原文，不是改写后的
        self.assertEqual(ac[0][2], "78. 子集")
        md = next(self.hist.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("- 类型：**白板提交**", md)

    def test_wa_snapshot_counts_as_fail(self):
        r = self.run_judge(FakeLC((200, {"submission_id": 1}), (200, WA)))
        self.assertEqual((r["verdict"], r["input"]), ("wa", "[1,2,3]"))
        self.assertNotIn("expected", str(r))
        self.assertEqual(len(review.load_fails(self.hist)["subsets"]), 1)
        md = next(self.hist.glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("### 失败用例输入", md)
        self.assertNotIn("预期输出", md)  # 侧边栏能读快照，不能把答案写进去
        self.assertNotIn("我的输出", md)

    def test_429_retry_once_then_give_up(self):
        lc = FakeLC((429, None), (429, {"error": "too many"}))
        r = self.run_judge(lc)
        self.assertEqual(r["verdict"], "cant")
        self.assertIn("429", r["reason"])
        self.assertEqual((len(lc.calls), self.sleeps), (2, [5]))
        # 判不出也写快照，但不算交错
        self.assertEqual(len(list(self.hist.glob("*.md"))), 1)
        self.assertEqual(review.load_fails(self.hist), {})

    def test_429_then_ok(self):
        lc = FakeLC((429, None), (200, {"submission_id": 1}), (200, AC))
        self.assertEqual(self.run_judge(lc)["verdict"], "accepted")
        self.assertEqual(self.sleeps, [5, 1])

    def test_poll_timeout(self):
        lc = FakeLC((200, {"submission_id": 1}), *[(200, {"state": "PENDING"})] * judge.POLL_LIMIT)
        r = self.run_judge(lc)
        self.assertEqual(r["verdict"], "cant")
        self.assertIn("60 秒", r["reason"])

    def test_same_code_within_an_hour_not_resubmitted(self):
        self.run_judge(FakeLC((200, {"submission_id": 1}), (200, WA)))
        self.t += 600
        lc = FakeLC()
        r = self.run_judge(lc, code=BOARD + "print('再看一眼')\n")  # 只多了测试 print，改写后一样
        self.assertTrue(r["dup"])
        self.assertEqual((r["verdict"], lc.calls), ("wa", []))
        self.assertEqual(len(list(self.hist.glob("*.md"))), 1)  # 没交就不写快照
        self.t += 3600
        lc = FakeLC((200, {"submission_id": 2}), (200, WA))
        self.assertNotIn("dup", self.run_judge(lc))  # 过了一小时可以再交

    def test_cant_result_does_not_block_retry(self):
        self.run_judge(FakeLC((500, None), (500, None)))
        lc = FakeLC((200, {"submission_id": 2}), (200, AC))
        self.assertEqual(self.run_judge(lc)["verdict"], "accepted")

    def test_no_snippet_cant(self):
        r = self.run_judge(FakeLC(), meta=lambda: {"questionId": "1", "codeSnippets": None})
        self.assertEqual(r["verdict"], "cant")

    def test_meta_error_cant(self):
        def boom():
            raise RuntimeError("还没拿到力扣登录状态")
        self.assertIn("登录状态", self.run_judge(FakeLC(), meta=boom)["reason"])

    def test_busy_when_locked(self):
        lock = judge._locks.setdefault("subsets", judge.threading.Lock())
        lock.acquire()
        try:
            self.assertEqual(self.run_judge(FakeLC())["verdict"], "busy")
        finally:
            lock.release()


class Meta(unittest.TestCase):
    def test_cached(self):
        calls = []

        def gql(q, v):
            calls.append(v)
            return {"question": META}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(judge.question_meta("subsets", gql, Path(d)), META)
            self.assertEqual(judge.question_meta("subsets", gql, Path(d)), META)
        self.assertEqual(len(calls), 1)

    def test_headers(self):
        h = judge.lc_headers({"session": "S", "csrf": "C"}, "https://leetcode.cn/problems/subsets/")
        self.assertEqual(h["Cookie"], "LEETCODE_SESSION=S; csrftoken=C")
        self.assertEqual((h["x-csrftoken"], h["Origin"]), ("C", "https://leetcode.cn"))
        self.assertIn("Chrome/", h["User-Agent"])


if __name__ == "__main__":
    unittest.main()
