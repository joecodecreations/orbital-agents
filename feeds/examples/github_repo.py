"""GitHub repository stats from the public API: stars, forks, issues, releases.

Shows: a sidebar section with one node per repository, each node with tabs (Overview, Releases,
Issues), a stars-over-time chart kept across runs, tables, key/value facts, link buttons, and an
alert when a new release appears. Lives in the "Projects" space. No key needed (60 requests an
hour); set GITHUB_TOKEN in the environment for a higher limit.

    python feeds/examples/github_repo.py --repo python/cpython [--repo owner/name …] [--dry-run]
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

import feedkit  # noqa: E402

from orbital.feeds import blocks as b  # noqa: E402

FEED = "github-repos"
API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
KEEP_DAYS = 90


class RateLimited(RuntimeError):
    pass


def _get(client: httpx.Client, path: str, params: dict | None = None):
    response = client.get(f"{API}{path}", params=params)
    if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
        reset = int(response.headers.get("x-ratelimit-reset") or 0)
        raise RateLimited(f"GitHub rate limit reached; resets at {feedkit.local_time(reset)}" if reset else "GitHub rate limit reached")
    response.raise_for_status()
    return response.json()


def gather(repo: str, client: httpx.Client) -> dict:
    info = _get(client, f"/repos/{repo}")
    releases = _get(client, f"/repos/{repo}/releases", {"per_page": 5})
    issues = [i for i in _get(client, f"/repos/{repo}/issues", {"state": "open", "per_page": 15, "sort": "created"})
              if "pull_request" not in i][:10]
    return {
        "repo": repo,
        "name": info.get("name") or repo.split("/")[-1],
        "description": info.get("description") or "",
        "url": info.get("html_url") or f"https://github.com/{repo}",
        "homepage": info.get("homepage") or "",
        "stars": int(info.get("stargazers_count") or 0),
        "forks": int(info.get("forks_count") or 0),
        "watchers": int(info.get("subscribers_count") or info.get("watchers_count") or 0),
        "open_issues": int(info.get("open_issues_count") or 0),   # GitHub counts open PRs here too
        "language": info.get("language") or "-",
        "license": ((info.get("license") or {}).get("spdx_id")) or "-",
        "default_branch": info.get("default_branch") or "-",
        "pushed_at": info.get("pushed_at") or "",
        "archived": bool(info.get("archived")),
        "releases": [{"name": r.get("name") or r.get("tag_name"), "tag": r.get("tag_name"), "url": r.get("html_url"),
                      "published_at": r.get("published_at") or "", "prerelease": bool(r.get("prerelease"))}
                     for r in releases if isinstance(r, dict)],
        "issues": [{"number": i.get("number"), "title": i.get("title") or "", "url": i.get("html_url"),
                    "created_at": i.get("created_at") or "", "comments": int(i.get("comments") or 0)}
                   for i in issues if isinstance(i, dict)],
    }


def gather_all(repos: list[str], state: dict) -> tuple[list[dict], list[str]]:
    """Fresh data where the API answers; the last good copy (marked stale) where it doesn't."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out, problems = [], []
    cache = state.setdefault("cache", {})
    with feedkit.http_client(15) as client:
        client.headers.update(headers)
        for repo in repos:
            try:
                data = gather(repo, client)
                cache[repo] = data
                out.append(data)
            except (httpx.HTTPError, RateLimited, ValueError) as exc:
                reason = str(exc) if isinstance(exc, RateLimited) else f"{exc.__class__.__name__}: {str(exc)[:120]}"
                problems.append(f"{repo}: {reason}")
                if repo in cache:
                    out.append(dict(cache[repo], stale=reason))
    return out, problems


def _day(stamp: str) -> str:
    return stamp[:10] if stamp else "-"


def remember(state: dict, repos: list[dict]) -> tuple[dict, list[dict]]:
    """Daily star counts per repo, and the releases that are new since the last run."""
    today = time.strftime("%Y-%m-%d")
    stars = state.setdefault("stars", {})
    seen = state.setdefault("releases_seen", {})
    new_releases = []
    for repo in repos:
        if repo.get("stale"):
            continue
        days = stars.setdefault(repo["repo"], {})
        days[today] = repo["stars"]
        for old in sorted(days)[:-KEEP_DAYS]:
            del days[old]
        tags = [r["tag"] for r in repo["releases"] if r.get("tag")]
        known = seen.get(repo["repo"])
        if known is not None:
            new_releases += [dict(r, repo=repo["repo"]) for r in repo["releases"] if r.get("tag") and r["tag"] not in known]
        seen[repo["repo"]] = (tags + [t for t in (known or []) if t not in tags])[:50]
    return state, new_releases


def repo_node(repo: dict, stars_by_day: dict, order: int) -> dict:
    ident = b.slug(repo["repo"].replace("/", "-"), 60)
    overview = []
    if repo.get("stale"):
        overview.append(b.callout("stale", f"Showing the last good read. {repo['stale']}", tone="warning",
                                  title="GitHub did not answer"))
    if repo.get("archived"):
        overview.append(b.callout("archived", "This repository is archived (read-only).", tone="info"))
    if repo["description"]:
        overview.append(b.text("about", repo["description"]))
    overview += [
        b.metrics("stats", [
            b.metric("Stars", f"{repo['stars']:,}", id="stars"),
            b.metric("Forks", f"{repo['forks']:,}", id="forks"),
            b.metric("Open issues + PRs", f"{repo['open_issues']:,}", id="issues"),
            b.metric("Watchers", f"{repo['watchers']:,}", id="watchers"),
        ]),
        b.chart("stars", b.chart_series("Stars", sorted(stars_by_day.items())), kind="line", title="Stars by day",
                y_format="compact", caption="One point per day this feed ran"),
        b.key_value("facts", [("Language", repo["language"]), ("License", repo["license"]),
                              ("Default branch", repo["default_branch"]), ("Last push", _day(repo["pushed_at"]))]
                    + ([("Homepage", repo["homepage"])] if repo["homepage"] else []), title="Repository"),
    ]
    links = [b.button("Open on GitHub", url=repo["url"], id="open")]
    if repo["homepage"].startswith(("https://", "http://")):
        links.append(b.button("Homepage", url=repo["homepage"], style="secondary", id="homepage"))
    overview.append(b.buttons("links", links))
    releases = [b.table("releases", ["Release", "Tag", "Published", "Kind"],
                        [[r["name"] or r["tag"], r["tag"], _day(r["published_at"]), "pre-release" if r["prerelease"] else "release"]
                         for r in repo["releases"]], title="Latest releases")] if repo["releases"] else \
        [b.callout("no-releases", "No releases published yet.")]
    issues = [b.table("issues", ["#", "Title", "Opened", "Comments"],
                      [[f"#{i['number']}", i["title"], _day(i["created_at"]), i["comments"]] for i in repo["issues"]],
                      title="Newest open issues", limit=10)] if repo["issues"] else \
        [b.callout("no-issues", "No open issues.", tone="success")]
    return b.node(ident, repo["repo"], order=order, subtitle=(repo["description"] or "")[:80] or None, icon="chevron.left.forwardslash.chevron.right",
                  tabs=[b.tab("overview", "Overview", overview, icon="star"),
                        b.tab("releases", "Releases", releases, icon="shippingbox"),
                        b.tab("issues", "Issues", issues, icon="exclamationmark.bubble")])


def build(repos: list[dict], state: dict, new_releases: list[dict], problems: list[str], *,
          section_id: str = "github") -> tuple[dict, list[dict]]:
    stars = state.get("stars") or {}
    nodes = [repo_node(repo, stars.get(repo["repo"], {}), order=(i + 1) * 10) for i, repo in enumerate(repos)]
    if not nodes:
        nodes = [b.node("waiting", "No data yet", [b.callout("waiting", "; ".join(problems) or "No repository configured.",
                                                             tone="warning", title="GitHub did not answer")])]
    total = sum(r["stars"] for r in repos)
    section = b.section(section_id, "GitHub", icon="chevron.left.forwardslash.chevron.right", order=30, nodes=nodes,
                        subtitle="Public repository stats", summary=f"{len(repos)} repos · {total:,} stars"[:80],
                        space="projects", space_title="Projects", space_icon="folder.fill")
    alerts = [{"id": f"github-{b.slug(r['repo'].replace('/', '-'), 40)}-{b.slug(r['tag'], 20)}",
               "title": f"{r['repo']} {r['tag']} released", "body": f"{r['name'] or r['tag']} was published on {_day(r['published_at'])}.",
               "level": "info"} for r in new_releases]
    return section, alerts


def main(argv: list[str] | None = None, client=None) -> int:
    parser = feedkit.arg_parser(__doc__.splitlines()[0])
    parser.add_argument("--repo", action="append", help="owner/name (repeat for more); default python/cpython")
    parser.add_argument("--section-id", default="github")
    args = parser.parse_args(argv)
    repos = args.repo or ["python/cpython"]
    bad = [r for r in repos if not REPO_RE.match(r)]
    if bad:
        print(f"not owner/name: {', '.join(bad)}", file=sys.stderr)
        return 2
    state = feedkit.load_state(FEED, args.home)
    data, problems = gather_all(repos, state)
    state, new_releases = remember(state, data)
    section, alerts = build(data, state, new_releases, problems, section_id=args.section_id)
    result = feedkit.push(section, alerts, dry_run=args.dry_run, client=client, home=args.home)
    if not args.dry_run:
        feedkit.save_state(FEED, state, args.home)
    for problem in problems:
        print(f"api: unavailable {problem}", file=sys.stderr)
    feedkit.result_line("github", pushed=1 if not args.dry_run else 0, repos=len(data),
                        stars=sum(r["stars"] for r in data), api="ok" if not problems else "unavailable",
                        alerts=result["alerts"], revision=result["revision"])
    return 0 if data else 1


if __name__ == "__main__":
    sys.exit(feedkit.run_feed(main))
