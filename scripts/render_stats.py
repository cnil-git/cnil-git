#!/usr/bin/env python3
"""Render the profile stat cards as SVG from the GitHub GraphQL API.

Usage: GH_TOKEN=... python3 scripts/render_stats.py <login> <out_dir>

Only aggregate numbers are rendered. No repository names are written, so
private work is counted without being exposed.
"""

import datetime as dt
import json
import math
import os
import sys
import urllib.request
from xml.sax.saxutils import escape

FONT = "Segoe UI, Helvetica Neue, Helvetica, Arial, sans-serif"
BG_TOP, BG_BOTTOM = "#3a1c03", "#241000"
LEAF, DEEP, MOSS = "#8CE99C", "#318C3B", "#589562"
TEXT, MUTED, SOIL, GRID = "#d8c7b0", "#a8957d", "#4a2a10", "#3d220b"
LEVELS = ["#2f6b36", "#3f9a4b", "#62c574", LEAF]
LANG_COLORS = [LEAF, "#3FAE4F", "#C97B3D", "#E3C770", MOSS, "#B9A58A"]
OTHER_COLOR = "#6b5a45"

QUERY = """
query($login: String!) {
  user(login: $login) {
    id
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date weekday contributionCount contributionLevel } }
      }
      commitContributionsByRepository(maxRepositories: 100) {
        contributions { totalCount }
        repository { nameWithOwner ...langs }
      }
    }
  }
}
"""

# Private repositories are left out of commitContributionsByRepository, so
# also count the user's own commits on each default branch they can see.
REPO_QUERY = """
query($login: String!, $uid: ID!, $since: GitTimestamp!) {
  user(login: $login) {
    repositories(ownerAffiliations: [OWNER, ORGANIZATION_MEMBER, COLLABORATOR],
                 isFork: false, first: 100, orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes {
        nameWithOwner
        ...langs
        defaultBranchRef { target { ... on Commit { history(since: $since, author: {id: $uid}) { totalCount } } } }
      }
    }
  }
}
"""

LANGS_FRAGMENT = """
fragment langs on Repository {
  languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
    totalSize
    edges { size node { name } }
  }
}
"""

# A simple leaf centered on the origin, pointing up, roughly 2 units tall.
LEAF_PATH = "M0,-1C0.62,-0.62 0.62,0.55 0,1C-0.62,0.55 -0.62,-0.62 0,-1Z"


def graphql(query, variables, token, required=True):
    body = json.dumps({"query": query + LANGS_FRAGMENT, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": "bearer " + token, "User-Agent": "profile-stats"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("errors"):
        if required:
            sys.exit("GraphQL error: %s" % data["errors"])
        print("warning: skipping optional query: %s" % data["errors"], file=sys.stderr)
        return None
    return data["data"]["user"]


def fetch(login, token):
    user = graphql(QUERY, {"login": login}, token)
    cc = user["contributionsCollection"]
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repos = graphql(REPO_QUERY, {"login": login, "uid": user["id"], "since": since}, token, False)
    repos = repos or {"repositories": {"nodes": []}}
    commits = {}
    for entry in cc["commitContributionsByRepository"]:
        repo = entry["repository"]
        commits[repo["nameWithOwner"]] = (entry["contributions"]["totalCount"], repo["languages"])
    for repo in repos["repositories"]["nodes"]:
        target = (repo.get("defaultBranchRef") or {}).get("target") or {}
        count = (target.get("history") or {}).get("totalCount", 0)
        name = repo["nameWithOwner"]
        if count > commits.get(name, (0,))[0]:
            commits[name] = (count, repo["languages"])
    cc["commitsByRepo"] = list(commits.values())
    return cc


def leaf(x, y, size, rotate, fill, opacity=1.0, vein=None):
    out = '<g transform="translate(%.1f %.1f) rotate(%d) scale(%.2f)">' % (x, y, rotate, size)
    out += '<path d="%s" fill="%s" opacity="%.2f"/>' % (LEAF_PATH, fill, opacity)
    if vein:
        out += '<path d="M0,-0.8V0.95" stroke="%s" stroke-width="%.3f" opacity="0.6"/>' % (vein, 1.2 / size)
    return out + "</g>"


def text(x, y, value, size, fill, weight=400, anchor="start"):
    return '<text x="%.1f" y="%.1f" font-family="%s" font-size="%d" font-weight="%d" fill="%s" text-anchor="%s">%s</text>' % (
        x, y, FONT, size, weight, fill, anchor, escape(str(value)))


def card(w, h, title, subtitle, body, label):
    return "\n".join([
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s">' % (w, h, w, h, escape(label)),
        '<defs><clipPath id="c"><rect width="%d" height="%d" rx="18"/></clipPath>' % (w, h),
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient></defs>' % (BG_TOP, BG_BOTTOM),
        '<g clip-path="url(#c)">',
        '<rect width="%d" height="%d" fill="url(#bg)"/>' % (w, h),
        leaf(w - 48, h - 30, 62, -32, DEEP, 0.09),
        leaf(w - 118, h - 2, 34, 28, DEEP, 0.06),
        leaf(36, 37, 9, 35, LEAF, vein=BG_BOTTOM),
        text(54, 44, title, 20, LEAF, 700),
        text(w - 28, 44, subtitle, 13, MUTED, anchor="end") if subtitle else "",
        body,
        "</g>",
        "</svg>",
        "",
    ])


def days_of(cc):
    return [d for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]]


def streaks(days):
    longest = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] else 0
        longest = max(longest, run)
    current = 0
    tail = days[:-1] if days and not days[-1]["contributionCount"] else days
    for d in reversed(tail):
        if not d["contributionCount"]:
            break
        current += 1
    return current, longest


def stats_card(cc):
    days = days_of(cc)
    current, longest = streaks(days)
    active = sum(1 for d in days if d["contributionCount"])
    items = [
        ("Contributions", cc["contributionCalendar"]["totalContributions"]),
        ("Commits", cc["totalCommitContributions"]),
        ("Pull requests", cc["totalPullRequestContributions"]),
        ("Code reviews", cc["totalPullRequestReviewContributions"]),
        ("Issues", cc["totalIssueContributions"]),
        ("Repos committed to", sum(1 for n, _ in cc["commitsByRepo"] if n)),
        ("Current streak", "%d days" % current),
        ("Longest streak", "%d days" % longest),
    ]
    body = []
    for i, (label, value) in enumerate(items):
        col, row = i % 2, i // 2
        x, y = 30 + col * 205, 92 + row * 42
        body.append(leaf(x + 5, y - 5, 5.5, 40 if col else -40, LEVELS[min(row, 3)]))
        body.append(text(x + 18, y, label, 13, MUTED))
        body.append(text(x + 190, y, value, 16, TEXT, 700, "end"))
    body.append('<rect x="30" y="252" width="375" height="1" fill="%s"/>' % GRID)
    body.append(text(30, 274, "Active on %d days in the last year" % active, 12, MUTED))
    return card(435, 300, "Growth this year", "last 12 months", "\n".join(body), "Contribution stats")


def languages_card(cc, top=6):
    weights = {}
    for commits, langs in cc["commitsByRepo"]:
        total = langs["totalSize"] or 0
        for e in langs["edges"]:
            if total:
                weights[e["node"]["name"]] = weights.get(e["node"]["name"], 0) + commits * e["size"] / total
    ranked = sorted(weights.items(), key=lambda kv: -kv[1])
    grand = sum(weights.values()) or 1
    shown = ranked[:top]
    rest = sum(v for _, v in ranked[top:])
    if rest:
        shown.append(("Other", rest))

    body, x = [], 30.0
    bar_w = 375.0
    body.append('<clipPath id="bar"><rect x="30" y="76" width="%d" height="12" rx="6"/></clipPath><g clip-path="url(#bar)">' % bar_w)
    for i, (name, value) in enumerate(shown):
        w = bar_w * value / grand
        color = OTHER_COLOR if name == "Other" else LANG_COLORS[i % len(LANG_COLORS)]
        body.append('<rect x="%.2f" y="76" width="%.2f" height="12" fill="%s"/>' % (x, w + 0.5, color))
        x += w
    body.append("</g>")
    for i, (name, value) in enumerate(shown):
        col, row = i % 2, i // 2
        lx, ly = 30 + col * 205, 128 + row * 36
        color = OTHER_COLOR if name == "Other" else LANG_COLORS[i % len(LANG_COLORS)]
        body.append(leaf(lx + 5, ly - 5, 6, 35, color))
        body.append(text(lx + 18, ly, name, 14, TEXT))
        body.append(text(lx + 190, ly, "%.1f%%" % (100 * value / grand), 13, MUTED, anchor="end"))
    body.append('<rect x="30" y="252" width="375" height="1" fill="%s"/>' % GRID)
    body.append(text(30, 274, "Weighted by commits in each repository", 12, MUTED))
    return card(435, 300, "Languages", "by commits", "\n".join(body), "Top languages")


def garden_card(cc):
    weeks = cc["contributionCalendar"]["weeks"]
    total = cc["contributionCalendar"]["totalContributions"]
    level_index = {"FIRST_QUARTILE": 0, "SECOND_QUARTILE": 1, "THIRD_QUARTILE": 2, "FOURTH_QUARTILE": 3}
    pitch, x0, y0 = 14.6, 68, 92
    defs = ['<defs><circle id="s" r="1.8" fill="%s"/>' % SOIL]
    for i, color in enumerate(LEVELS):
        size = 3.4 + i * 0.9
        defs.append('<g id="l%d"><g transform="rotate(35) scale(%.2f)"><path d="%s" fill="%s"/></g></g>' % (i, size, LEAF_PATH, color))
    defs.append("</defs>")
    body = ["".join(defs)]
    last_month = None
    for wi, week in enumerate(weeks):
        for d in week["contributionDays"]:
            cx, cy = x0 + wi * pitch, y0 + d["weekday"] * pitch
            ref = "l%d" % level_index[d["contributionLevel"]] if d["contributionLevel"] in level_index else "s"
            body.append('<use href="#%s" x="%.1f" y="%.1f"/>' % (ref, cx, cy))
        month = dt.date.fromisoformat(week["contributionDays"][0]["date"]).strftime("%b")
        if month != last_month and wi < len(weeks) - 2:
            if last_month is not None or week["contributionDays"][0]["weekday"] == 0:
                body.append(text(x0 + wi * pitch - 4, y0 - 18, month, 11, MUTED))
            last_month = month
    for wd, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        body.append(text(x0 - 16, y0 + wd * pitch + 4, name, 11, MUTED, anchor="end"))
    ly = y0 + 7 * pitch + 14
    lx = x0 + 52 * pitch - 5 * 18 - 4
    body.append(text(lx - 12, ly + 4, "Less", 11, MUTED, anchor="end"))
    for i, ref in enumerate(["s", "l0", "l1", "l2", "l3"]):
        body.append('<use href="#%s" x="%.1f" y="%.1f"/>' % (ref, lx + i * 18, ly))
    body.append(text(lx + 5 * 18 - 2, ly + 4, "More", 11, MUTED))
    body.append(text(x0 - 4, ly + 4, "Each sprout is a day; fuller leaves mean more contributions", 11, MUTED))
    return card(880, 250, "Contribution garden", "%d contributions in the last year" % total,
                "\n".join(body), "Contribution calendar")


def smooth_path(points, floor, ceil):
    d = "M%.1f,%.1f" % points[0]
    for i in range(len(points) - 1):
        p0 = points[max(i - 1, 0)]
        p1, p2 = points[i], points[i + 1]
        p3 = points[min(i + 2, len(points) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        c1 = (c1[0], min(max(c1[1], ceil), floor))
        c2 = (c2[0], min(max(c2[1], ceil), floor))
        d += "C%.1f,%.1f %.1f,%.1f %.1f,%.1f" % (c1 + c2 + p2)
    return d


def activity_card(cc, span=31):
    days = days_of(cc)[-span:]
    counts = [d["contributionCount"] for d in days]
    raw = max(max(counts), 4) / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = int(next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw))
    ticks = max(1, math.ceil(max(counts) / step))
    ymax = step * ticks
    x0, x1, y0, y1 = 64, 852, 78, 214
    xs = [x0 + i * (x1 - x0) / (len(days) - 1) for i in range(len(days))]
    ys = [y1 - c * (y1 - y0) / ymax for c in counts]
    pts = list(zip(xs, ys))
    body = ['<defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="%s" stop-opacity="0.35"/>'
            '<stop offset="1" stop-color="%s" stop-opacity="0"/></linearGradient></defs>' % (LEAF, LEAF)]
    for k in range(ticks + 1):
        gy = y1 - k * (y1 - y0) / ticks
        body.append('<rect x="%d" y="%.1f" width="%d" height="1" fill="%s"/>' % (x0, gy, x1 - x0, GRID))
        body.append(text(x0 - 12, gy + 4, k * step, 11, MUTED, anchor="end"))
    line = smooth_path(pts, y1, y0)
    body.append('<path d="%sL%.1f,%dL%.1f,%dZ" fill="url(#area)"/>' % (line, x1, y1, x0, y1))
    body.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.5" stroke-linecap="round"/>' % (line, DEEP))
    for i, (x, y) in enumerate(pts):
        if counts[i]:
            body.append(leaf(x + (4 if i % 2 else -4), y - 6, 7, 40 if i % 2 else -40, LEAF, vein=DEEP))
        else:
            body.append('<circle cx="%.1f" cy="%.1f" r="2.2" fill="%s"/>' % (x, y, MOSS))
    for i in range(0, len(days), 5):
        label = dt.date.fromisoformat(days[i]["date"]).strftime("%b %d").replace(" 0", " ")
        body.append(text(xs[i], y1 + 22, label, 11, MUTED, anchor="middle"))
    return card(880, 260, "Recent activity", "contributions per day, last %d days" % span,
                "\n".join(body), "Recent activity graph")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    login, out = sys.argv[1], sys.argv[2]
    token = os.environ.get("GH_TOKEN") or sys.exit("GH_TOKEN is not set")
    cc = fetch(login, token)
    os.makedirs(out, exist_ok=True)
    cards = {
        "stats.svg": stats_card(cc),
        "languages.svg": languages_card(cc),
        "garden.svg": garden_card(cc),
        "activity.svg": activity_card(cc),
    }
    for name, svg in cards.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(svg.encode("ascii", "xmlcharrefreplace"))
    print("wrote", ", ".join(sorted(cards)))


if __name__ == "__main__":
    main()
