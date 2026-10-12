"""python3 -m unittest test_review"""
import json
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path

import review

CODE = """class Solution:
    def subsets(self, nums):
        res, track = [], []
        def bt(start):
            res.append(track[:])
            for i in range(start, len(nums)):
                track.append(nums[i])
                bt(i + 1)
                track.pop()
        bt(0)
        return res
"""


def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()


def snap(kind, result, code):
    return (f"# 78. 子集\n\n## 题目描述\n略\n\n## 我的代码\n```python3\n{code}```\n\n"
            f"## 最近一次运行 / 提交\n- 类型：**{kind}**（2026/10/5 10:00:00）\n- 结果：**{result}**\n")


class Grade(unittest.TestCase):
    def test_no_questions_is_green(self):
        self.assertEqual(review.grade_of([], CODE), "green")

    def test_questions_without_the_answer_is_yellow(self):
        self.assertEqual(review.grade_of(["`start` 是这层从哪开始挑。\n```python\nfor i in range(start, n):\n```"], CODE), "yellow")

    def test_answer_code_matching_final_code_is_red(self):
        self.assertEqual(review.grade_of(["直接给你：\n```python\n" + CODE + "```"], CODE), "red")


class LoadQa(unittest.TestCase):
    def test_auto_check_and_talk_are_not_hints(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "qa.jsonl"
            rows = [{"time": "2026-10-05 09:00:00", "slug": "s", "question": review.AUTO_LABEL, "answer": "a"},
                    {"time": "2026-10-05 09:01:00", "slug": "s", "question": review.TALK_TAG + "我想用双指针", "answer": "b"},
                    {"time": "2026-10-05 09:02:00", "slug": "s", "question": "为啥越界", "answer": "c"}]
            f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
            self.assertEqual([q[1] for q in review.load_qa(f)["s"]], ["c"])
            self.assertEqual(review.load_qa(f)["s"][0][3], "为啥越界")  # 带上问题原文，判「看了第 4 档提示」用


class Attempts(unittest.TestCase):
    def test_resubmit_within_an_hour_is_not_a_review(self):
        ac = {"s": [(ts("2026-10-05 10:00"), CODE, "t"), (ts("2026-10-05 10:20"), CODE, "t")]}
        self.assertEqual(len(review.attempts(ac, {}, [])["s"]), 1)

    def test_each_attempt_only_counts_questions_since_the_last_one(self):
        ac = {"s": [(ts("2026-10-05 10:00"), CODE, "t"), (ts("2026-10-07 10:00"), CODE, "t")]}
        qa = {"s": [(ts("2026-10-05 09:30"), "提示", "t")]}
        self.assertEqual([a["grade"] for a in review.attempts(ac, qa, [])["s"]], ["yellow", "green"])

    def test_manual_board_review_counts_even_right_after(self):
        ac = {"s": [(ts("2026-10-05 10:00"), CODE, "t")]}
        manual = [{"slug": "s", "grade": "yellow", "at": ts("2026-10-05 10:10")}]
        self.assertEqual(len(review.attempts(ac, {}, manual)["s"]), 2)


def att(day, grade):
    return {"at": ts(day + " 12:00"), "grade": grade, "kind": "manual"}


class Schedule(unittest.TestCase):
    def test_first_due_follows_first_grade(self):
        self.assertEqual(review.schedule([att("2026-10-05", "green")])["due"], "2026-10-12")
        self.assertEqual(review.schedule([att("2026-10-05", "yellow")])["due"], "2026-10-06")
        self.assertEqual(review.schedule([att("2026-10-05", "red")])["due"], "2026-10-05")

    def test_review_moves_one_step_from_the_review_day(self):
        s = review.schedule([att("2026-10-05", "yellow"), att("2026-10-08", "green")])
        self.assertEqual((s["step"], s["due"]), (1, "2026-10-10"))  # 第 1 天 → 第 3 天，隔 2 天

    def test_red_review_restarts_on_red_ladder(self):
        s = review.schedule([att("2026-10-05", "green"), att("2026-10-12", "red")])
        self.assertEqual((s["steps"], s["step"], s["due"]), (6, 0, "2026-10-12"))

    def test_last_step_means_mastered(self):
        self.assertIsNone(review.schedule([att("2026-10-05", "green"), att("2026-10-12", "green"),
                                           att("2026-11-04", "green")])["due"])


class Build(unittest.TestCase):
    def test_from_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "history").mkdir()
            (d / "history" / "20261005-100000-subsets.md").write_text(snap("提交", "Accepted", CODE), encoding="utf-8")
            (d / "history" / "20261005-095000-subsets.md").write_text(snap("运行", "Accepted", CODE), encoding="utf-8")
            rows = [{"time": "2026-10-05 09:40:00", "slug": "subsets", "title": "78. 子集", "question": "给我代码", "answer": "```python\n" + CODE + "```"},
                    {"time": "2026-10-05 10:01:00", "slug": "subsets", "title": "78. 子集", "question": review.AUTO_LABEL, "answer": "AC 了"},
                    {"time": "2026-10-06 10:00:00", "slug": "word-break", "title": "139. 单词拆分", "question": "咋做", "answer": "dp"}]
            (d / "qa.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
            r = review.build(d / "history", d / "qa.jsonl", d / "reviews.json", today=date(2026, 10, 8))
            self.assertEqual([(s["slug"], s["first"], s["overdue"]) for s in r["due"]], [("subsets", "red", 3)])
            self.assertEqual([s["slug"] for s in r["unfinished"]], ["word-break"])

            review.add_manual(d / "reviews.json", "subsets", "green", at=ts("2026-10-08 12:00"))
            s = review.status(d / "history", d / "qa.jsonl", d / "reviews.json", "subsets")
            self.assertEqual((s["last"], s["due"]), ("green", "2026-10-09"))


def hist(d, files):
    """files: {文件名时间: (类型, 结果)}，写进 d/history/"""
    h = Path(d) / "history"
    h.mkdir(exist_ok=True)
    for stamp, (kind, result) in files.items():
        (h / f"{stamp}-subsets.md").write_text(snap(kind, result, CODE), encoding="utf-8")
    return h


class LoadSubmits(unittest.TestCase):
    def test_board_submit_accepted_counts_but_not_its_wa_or_board_run(self):
        with tempfile.TemporaryDirectory() as d:
            h = hist(d, {"20261005-100000": ("白板提交", "Accepted"), "20261005-095000": ("白板提交", "Wrong Answer"),
                         "20261005-094000": ("白板运行", "Accepted"), "20261005-093000": ("提交", "Accepted")})
            self.assertEqual(len(review.load_ac(h)["subsets"]), 2)
            self.assertEqual(review.load_ac(h)["subsets"][1][1], CODE)

    def test_fails_count_wa_tle_re_ce_but_not_cant_judge(self):
        with tempfile.TemporaryDirectory() as d:
            h = hist(d, {"20261005-090000": ("白板提交", "Wrong Answer"), "20261005-090100": ("提交", "Time Limit Exceeded"),
                         "20261005-090200": ("白板提交", "Runtime Error"), "20261005-090300": ("白板提交", "Compile Error"),
                         "20261005-090400": ("白板提交", "判不出"), "20261005-090500": ("运行", "Wrong Answer"),
                         "20261005-090600": ("白板运行", "退出码 1")})
            self.assertEqual(len(review.load_fails(h)["subsets"]), 4)


class NewRules(unittest.TestCase):
    T = ts("2026-10-05 10:00")

    def grade(self, qs=(), fails=(), manual=()):
        ac = {"s": [(self.T, CODE, "t")]}
        qa = {"s": [(ts(q[0]), "答", "t", q[1]) for q in qs]}
        return review.attempts(ac, qa, list(manual), {"s": [ts(f) for f in fails]})["s"]

    def test_hint4_is_red_hint1_is_yellow(self):
        self.assertEqual(self.grade(qs=[("2026-10-05 09:50", "【提示 4/4：完整题解】我在白板上卡住了。")])[0]["grade"], "red")
        self.assertEqual(self.grade(qs=[("2026-10-05 09:50", "【提示 1/4：题型】我在白板上卡住了。")])[0]["grade"], "yellow")

    def test_one_fail_turns_green_into_yellow(self):
        a = self.grade(fails=["2026-10-05 09:55"])[0]
        self.assertEqual(a["grade"], "yellow")
        self.assertIn("提交没过 1 次", a["why"])

    def test_two_fails_is_red(self):
        self.assertEqual(self.grade(fails=["2026-10-05 09:50", "2026-10-05 09:55"])[0]["grade"], "red")

    def test_fails_outside_window_dont_count(self):
        ac = {"s": [(ts("2026-10-05 10:00"), CODE, "t"), (ts("2026-10-07 10:00"), CODE, "t")]}
        fails = {"s": [ts("2026-10-05 09:50"), ts("2026-10-05 10:30")]}  # 第一次 AC 前一次，之后一次
        self.assertEqual([a["grade"] for a in review.attempts(ac, {}, [], fails)["s"]], ["yellow", "yellow"])
        fails = {"s": [ts("2026-10-07 10:05")]}  # AC 之后才错的，不算这次
        self.assertEqual([a["grade"] for a in review.attempts(ac, {}, [], fails)["s"]], ["green", "green"])

    def test_cant_judge_snapshots_dont_count_as_fails(self):
        with tempfile.TemporaryDirectory() as d:
            h = hist(d, {"20261005-095000": ("白板提交", "判不出"), "20261005-100000": ("白板提交", "Accepted")})
            atts = review.attempts(review.load_ac(h), {}, [], review.load_fails(h))["subsets"]
            self.assertEqual(atts[0]["grade"], "green")

    def test_board_ac_and_leetcode_ac_within_an_hour_merge(self):
        with tempfile.TemporaryDirectory() as d:
            h = hist(d, {"20261005-100000": ("白板提交", "Accepted"), "20261005-103000": ("提交", "Accepted")})
            self.assertEqual(len(review.attempts(review.load_ac(h), {}, [], review.load_fails(h))["subsets"]), 1)

    def test_override_replaces_grade_without_new_attempt(self):
        atts = self.grade(manual=[{"slug": "s", "grade": "red", "at": self.T + 60, "override": True}])
        self.assertEqual([(a["grade"], a["kind"]) for a in atts], [("red", "auto")])

    def test_override_without_previous_event_is_manual(self):
        manual = [{"slug": "s", "grade": "yellow", "at": ts("2026-10-08 10:00"), "override": True}]
        atts = review.attempts({}, {}, manual)["s"]
        self.assertEqual([(a["grade"], a["kind"]) for a in atts], [("yellow", "manual")])
        # 前一次超过 1 小时：也是新的一次
        self.assertEqual(len(self.grade(manual=[{"slug": "s", "grade": "red", "at": self.T + 7200, "override": True}])), 2)

    def test_add_manual_override_and_status_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            h = hist(d, {})
            review.add_manual(Path(d) / "r.json", "subsets", "green", at=ts("2026-10-05 10:00"))
            review.add_manual(Path(d) / "r.json", "subsets", "red", at=ts("2026-10-05 10:01"), override=True)
            self.assertTrue(review.load_manual(Path(d) / "r.json")[1]["override"])
            s = review.status(h, Path(d) / "qa.jsonl", Path(d) / "r.json", "subsets")
            self.assertEqual((s["last"], s["attempt"]["grade"]), ("red", "red"))


if __name__ == "__main__":
    unittest.main()
