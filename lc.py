#!/usr/bin/env python3
"""给侧边栏里的 Claude 用的力扣查询工具。通过本地服务（带着你的登录状态）请求 leetcode.cn。

    lc.py submissions [题目slug] [--n 20]     我在这道题的提交记录（默认当前题）
    lc.py submission <提交id>                 某次提交的代码和结果
    lc.py solutions [题目slug] [--n 5] [--skip 0]   社区题解列表（按默认热度排序）
    lc.py solution <题解slug>                 某篇题解的全文
    lc.py question <题目slug>                 题目详情（题面、难度、标签）
    lc.py history [题目slug]                  本地记录的运行/提交快照列表（含失败用例和报错）
    lc.py history-show <文件名>               查看某条本地快照
"""
import json
import sys
import urllib.request

SERVER = "http://127.0.0.1:8765/lc"


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = args[0], args[1:]
    opts, pos = {}, []
    i = 0
    while i < len(rest):
        if rest[i].startswith("--") and i + 1 < len(rest):
            opts[rest[i][2:]] = rest[i + 1]
            i += 2
        else:
            pos.append(rest[i])
            i += 1
    req = urllib.request.Request(
        SERVER, data=json.dumps({"cmd": cmd, "args": pos, "opts": opts}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            sys.stdout.write(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.stdout.write(e.read().decode())
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"连不上本地服务：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
