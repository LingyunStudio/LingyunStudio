# -*- coding: utf-8 -*-
# GitHub 统计徽章自动刷新（GitHub Actions 定时任务版，无 GUI）
#
# 从 github.py / github_gui.py 抽出的核心逻辑：
#   · 抓取某用户的总 Star / 总 Fork / Release 总下载量
#   · 用与 GUI 完全相同的规则更新 README.md 里的三个统计徽章
#   · 顺便产出 stats.json（含发布数 / Issues 等扩展数据，以后可接 Cloudflare Pages 做网页面板）
#
# 只用 Python 标准库，Actions 里不需要 pip install。
#
# 环境变量：
#   GITHUB_USER    要统计的用户名（默认 LingyunStudio）
#   GITHUB_TOKEN   可选，提升 API 限额；Actions 里直接传内置的 secrets.GITHUB_TOKEN
#   INCLUDE_FORKS  是否把 Fork 仓库计入统计（默认计入，对应 GUI 的「包含 Fork」开关）
#   README_PATH    徽章所在的 README 路径（默认 README.md）

import json
import os
import re
import sys
import time
import urllib.request

API = "https://api.github.com"


def api_get(url, token):
    """GET 一个 GitHub API 地址，带简单重试；返回解析后的 JSON。"""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "github-stats-action",
    })
    if token:
        req.add_header("Authorization", f"token {token}")

    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:                       # 网络/5xx 抖动重试
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"请求 {url} 失败：{last_err}")


def fetch_stats(username, token, include_forks=True):
    """抓取全部仓库并汇总，与 GUI「包含 Fork」开关联动。"""
    repos, page = [], 1
    while True:
        data = api_get(f"{API}/users/{username}/repos?per_page=100&page={page}", token)
        if not data:
            break
        repos.extend(data)
        page += 1

    totals = {"repos": 0, "stars": 0, "forks": 0,
              "downloads": 0, "releases": 0, "issues": 0}
    for repo in repos:
        if not include_forks and repo.get("fork"):
            continue
        totals["repos"] += 1
        totals["stars"] += repo.get("stargazers_count", 0)
        totals["forks"] += repo.get("forks_count", 0)
        totals["issues"] += repo.get("open_issues_count", 0)

        releases_url = repo.get("releases_url", "").replace("{/id}", "")
        if releases_url:
            releases = api_get(releases_url + "?per_page=100", token) or []
            for release in releases:
                totals["releases"] += 1
                for asset in release.get("assets", []):
                    totals["downloads"] += asset.get("download_count", 0)
    return totals


# ---------------------------------------------------------------------------
# 徽章替换逻辑：与 github_gui.py 的 patch_readme_badges 完全一致，只改数字
# ---------------------------------------------------------------------------
README_BADGE_PATTERNS = (
    ("Total_Stars",     re.compile(r"Total_Stars-([\d,]+)")),
    ("Total_Forks",     re.compile(r"Total_Forks-([\d,]+)")),
    ("Total_Downloads", re.compile(r"Total_Downloads-([\d,]+)((?:%2B|\+)?)")),
)


def patch_readme_badges(content, totals):
    """返回 (新内容, 缺失的徽章列表)。数字无变化时新内容与原文相同。"""
    values = {"Total_Stars": totals["stars"], "Total_Forks": totals["forks"],
              "Total_Downloads": totals["downloads"]}
    new, missing = content, []
    for name, pat in README_BADGE_PATTERNS:
        m = pat.search(new)
        if not m:
            missing.append(name)
            continue
        plus = "%2B" if (pat.groups == 2 and m.group(2)) else ""
        fmt = f"{values[name]:,}" if "," in m.group(1) else str(values[name])
        new = new[:m.start()] + f"{name}-{fmt}{plus}" + new[m.end():]
    return new, missing


def main():
    username = os.environ.get("GITHUB_USER", "LingyunStudio")
    token = os.environ.get("GITHUB_TOKEN")
    readme_path = os.environ.get("README_PATH", "README.md")
    include_forks = os.environ.get("INCLUDE_FORKS", "1") not in ("0", "false", "no")

    print(f"正在统计 {username}（{'含' if include_forks else '不含'} Fork）…")
    totals = fetch_stats(username, token, include_forks)
    print("统计完成：" + " / ".join(f"{k}={v:,}" for k, v in totals.items()))

    # 产出 stats.json，供以后做网页面板 / 记录历史
    with open("stats.json", "w", encoding="utf-8") as f:
        json.dump({"username": username, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   **totals}, f, ensure_ascii=False, indent=2)

    if not os.path.exists(readme_path):
        print(f"⚠️ 未找到 {readme_path}，跳过徽章更新（stats.json 已生成）")
        return

    with open(readme_path, encoding="utf-8") as f:
        content = f.read()
    new, missing = patch_readme_badges(content, totals)
    if missing:
        print("⚠️ README 中未找到徽章：" + "、".join(missing) +
              "（脚本只更新已有徽章的数字，不会新建徽章）")
    if new != content:
        with open(readme_path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        print("✅ README 徽章已更新")
    else:
        print("✅ README 徽章已是最新，无需提交")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
