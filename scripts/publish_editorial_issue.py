#!/usr/bin/env python3
"""Validate, render and send a human-readable editor-authored AI briefing.

This module deliberately does not fetch news and does not call an LLM. Research
and writing happen before delivery. GitHub Actions only accepts a reviewed issue
file, turns it into email/Pages HTML, obtains a send lock and delivers it.
"""

import argparse
import base64
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
EDITORIAL_DIR = ROOT / "data" / "editorial"
MARKER_DIR = ROOT / "data" / "sent_markers"
DOCS_DIR = ROOT / "docs" / "daily"
BEIJING = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")
SECTIONS = ("lead", "use", "think", "quick")
SECTION_LABELS = {
    "lead": "今天真正重要的三件事",
    "use": "可以拿来用",
    "think": "值得多想一步",
    "quick": "还有几件小事",
}
SOURCE_TYPES = {"official", "primary", "research", "media", "author"}
BANNED_COPY = (
    "值得持续关注", "行业将迎来变革", "未来可期", "具有重要意义",
    "目前更像一个行业信号", "普通用户会逐步感受到变化", "赋能千行百业",
)


def parse_now(value):
    if not value:
        return datetime.now(BEIJING)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=BEIJING)).astimezone(BEIJING)


def edition_values(now, requested):
    slug = requested if requested in ("morning", "evening") else ("morning" if now.hour < 18 else "evening")
    return slug, "每日版" if slug == "morning" else "补充版"


def canonical_url(value):
    parsed = urllib.parse.urlsplit(value.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, val) for key, val in query if not key.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urllib.parse.urlencode(query), ""))


def chinese_count(value):
    return len(re.findall(r"[\u4e00-\u9fff]", str(value or "")))


def issue_date_prefix(value):
    """Return the reader-facing date prefix required by every issue headline."""
    try:
        parsed = datetime.strptime(str(value or ""), "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{parsed.year}年{parsed.month}月{parsed.day}日｜"


def validate_issue(issue, expected_date=None, expected_edition=None):
    """Return all editorial problems instead of silently repairing weak copy."""
    errors = []
    if not isinstance(issue, dict):
        return ["稿件根节点必须是 JSON 对象"]
    if expected_date and issue.get("date") != expected_date:
        errors.append(f"date 必须为 {expected_date}")
    if expected_edition and issue.get("edition") != expected_edition:
        errors.append(f"edition 必须为 {expected_edition}")
    if issue.get("edition") not in ("morning", "evening"):
        errors.append("edition 只能是 morning 或 evening")
    date_prefix = issue_date_prefix(issue.get("date"))
    if not date_prefix:
        errors.append("date 必须使用 YYYY-MM-DD 格式")
    for field, minimum, maximum in (
        ("subject", 8, 34), ("title", 8, 28), ("standfirst", 70, 260), ("editor_note", 45, 220),
    ):
        value = str(issue.get(field, "")).strip()
        if field in ("subject", "title") and date_prefix and not value.startswith(date_prefix):
            errors.append(f"{field} 必须以当天日期“{date_prefix}”开头")
        prose = value[len(date_prefix):] if field in ("subject", "title") and value.startswith(date_prefix) else value
        count = chinese_count(prose)
        if count < minimum or count > maximum:
            errors.append(f"{field} 需包含 {minimum}～{maximum} 个汉字，当前 {count}")
        if any(phrase in value for phrase in BANNED_COPY):
            errors.append(f"{field} 含空泛表达")

    daily_quote = issue.get("daily_quote")
    if not isinstance(daily_quote, dict):
        errors.append("daily_quote 必须是对象")
    else:
        quote_text = str(daily_quote.get("text", "")).strip()
        quote_author = str(daily_quote.get("author", "")).strip()
        quote_source_name = str(daily_quote.get("source_name", "")).strip()
        quote_source_url = str(daily_quote.get("source_url", "")).strip()
        if not 8 <= len(quote_text) <= 140:
            errors.append("daily_quote.text 需为 8～140 个字符")
        if not 2 <= len(quote_author) <= 80:
            errors.append("daily_quote.author 需为 2～80 个字符")
        if not 2 <= len(quote_source_name) <= 100:
            errors.append("daily_quote.source_name 需为 2～100 个字符")
        try:
            parsed_quote_source = urllib.parse.urlsplit(quote_source_url)
            if parsed_quote_source.scheme != "https" or not parsed_quote_source.netloc:
                raise ValueError
        except ValueError:
            errors.append("daily_quote.source_url 必须是可核验的 https 来源")

    stories = issue.get("stories")
    if not isinstance(stories, list):
        return errors + ["stories 必须是数组"]
    if not 6 <= len(stories) <= 12:
        errors.append(f"每期只允许 6～12 条精编内容，当前 {len(stories)}")
    counts = {section: 0 for section in SECTIONS}
    urls, domains, primary_count = set(), set(), 0
    for index, story in enumerate(stories, 1):
        label = f"stories[{index}]"
        if not isinstance(story, dict):
            errors.append(f"{label} 必须是对象")
            continue
        section = story.get("section")
        if section not in SECTIONS:
            errors.append(f"{label}.section 非法")
            continue
        counts[section] += 1
        title = str(story.get("title", "")).strip()
        teaser = str(story.get("teaser", "")).strip()
        if not 6 <= chinese_count(title) <= 38:
            errors.append(f"{label}.title 需为 6～38 个汉字")
        if not 20 <= chinese_count(teaser) <= 90:
            errors.append(f"{label}.teaser 需为 20～90 个汉字")
        source = story.get("source")
        if not isinstance(source, dict):
            errors.append(f"{label}.source 必须是对象")
            source = {}
        source_type = source.get("type")
        if source_type not in SOURCE_TYPES:
            errors.append(f"{label}.source.type 非法")
        if source_type in ("official", "primary", "research"):
            primary_count += 1
        source_url = str(source.get("url", "")).strip()
        try:
            parsed = urllib.parse.urlsplit(source_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError
            normalized = canonical_url(source_url)
            if normalized in urls:
                errors.append(f"{label}.source.url 与其他条目重复")
            urls.add(normalized)
            domains.add(parsed.netloc.lower().removeprefix("www."))
        except ValueError:
            errors.append(f"{label}.source.url 必须是 https 原始来源")
        if len(str(source.get("name", "")).strip()) < 2:
            errors.append(f"{label}.source.name 缺失")
        if not re.match(r"^\d{4}-\d{2}-\d{2}", str(source.get("published_at", ""))):
            errors.append(f"{label}.source.published_at 缺失或格式错误")
        body = story.get("body")
        if not isinstance(body, list) or not 1 <= len(body) <= 3 or not all(isinstance(p, str) and p.strip() for p in body):
            errors.append(f"{label}.body 必须包含 1～3 个非空段落")
            body = []
        body_count = sum(chinese_count(paragraph) for paragraph in body)
        minimum = 150 if section == "lead" else 90 if section in ("use", "think") else 45
        maximum = 520 if section == "lead" else 360 if section in ("use", "think") else 180
        if not minimum <= body_count <= maximum:
            errors.append(f"{label}.body 需为 {minimum}～{maximum} 个汉字，当前 {body_count}")
        combined = " ".join([title, teaser, *body])
        if any(phrase in combined for phrase in BANNED_COPY):
            errors.append(f"{label} 含空泛表达")
        tags = story.get("tags", [])
        if not isinstance(tags, list) or not 1 <= len(tags) <= 3:
            errors.append(f"{label}.tags 需有 1～3 个标签")
    if counts["lead"] != 3:
        errors.append(f"lead 必须恰好 3 条，当前 {counts['lead']}")
    if counts["use"] < 1:
        errors.append("至少需要 1 条可以实际使用的内容")
    if counts["think"] < 1:
        errors.append("至少需要 1 条值得思考的内容")
    if counts["quick"] < 1:
        errors.append("至少需要 1 条短讯")
    if len(domains) < 4:
        errors.append(f"至少需要 4 个不同来源域名，当前 {len(domains)}")
    if primary_count < 2:
        errors.append(f"至少需要 2 条官方、第一手或研究原文，当前 {primary_count}")
    return errors


def load_issue(path, expected_date=None, expected_edition=None):
    issue = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_issue(issue, expected_date, expected_edition)
    if errors:
        raise ValueError("稿件质量校验失败：\n- " + "\n- ".join(errors))
    return issue


def story_anchor(story):
    return "story-" + hashlib.sha1(canonical_url(story["source"]["url"]).encode()).hexdigest()[:12]


def render_story_email(story, number=None):
    prefix = f"{number:02d} · " if number else ""
    paragraphs = "".join(f'<p style="margin:10px 0;color:#292524;font-size:16px;line-height:1.82">{html.escape(p)}</p>' for p in story["body"])
    tags = "".join(f'<span style="display:inline-block;margin-right:6px;padding:3px 8px;border-radius:999px;background:#f1ede6;color:#57534e;font-size:12px">{html.escape(tag)}</span>' for tag in story.get("tags", []))
    return f'''<article style="padding:24px 0;border-bottom:1px solid #ded8ce">
<div style="font-size:12px;color:#a16207;font-weight:800;letter-spacing:.08em">{prefix}{html.escape(story["source"]["name"])} · {html.escape(story["source"]["published_at"][:10])}</div>
<h3 style="font:700 23px/1.38 Georgia,'Microsoft YaHei',serif;margin:8px 0;color:#1c1917">{html.escape(story["title"])}</h3>
{paragraphs}<div style="margin-top:13px">{tags}<a href="{html.escape(story["source"]["url"], quote=True)}" style="float:right;color:#1d4ed8;text-decoration:none;font-weight:700">核对原始来源 ↗</a></div><div style="clear:both"></div></article>'''


def render_email(issue, page_url):
    grouped = {section: [story for story in issue["stories"] if story["section"] == section] for section in SECTIONS}
    guide = "".join(f'<div style="padding:9px 0;border-bottom:1px solid #e7e5e4"><b>{index}. {html.escape(story["title"])}</b><div style="color:#57534e;margin-top:3px">{html.escape(story["teaser"])}</div></div>' for index, story in enumerate(grouped["lead"], 1))
    parts = []
    for section in SECTIONS:
        if not grouped[section]:
            continue
        label = SECTION_LABELS[section]
        cards = "".join(render_story_email(story, index if section == "lead" else None) for index, story in enumerate(grouped[section], 1))
        parts.append(f'<h2 style="font-size:13px;letter-spacing:.1em;color:#a16207;margin:36px 0 0">{html.escape(label)}</h2>{cards}')
    quote = issue["daily_quote"]
    quote_block = f'''<blockquote style="margin:18px 0 22px;padding:14px 18px;border-left:3px solid #d6a756;background:#fffaf0;color:#44403c">
<p style="margin:0;font:italic 17px/1.7 Georgia,'Noto Serif SC','Microsoft YaHei',serif">“{html.escape(quote["text"])}”</p>
<footer style="margin-top:7px;color:#78716c;font-size:12px">— {html.escape(quote["author"])} · <a href="{html.escape(quote["source_url"], quote=True)}" style="color:#78716c">{html.escape(quote["source_name"])}</a></footer>
</blockquote>'''
    body = f'''<!doctype html><html lang="zh-CN"><body style="margin:0;background:#f5f5f4;font-family:Arial,'Microsoft YaHei',sans-serif;color:#292524"><main style="max-width:700px;margin:auto;background:#fff;padding:34px 30px">
<div style="font-size:12px;color:#a16207;font-weight:800;letter-spacing:.14em">TRENDING AI · EDITED BY CODEX</div>
<h1 style="font:700 31px/1.24 Georgia,'Microsoft YaHei',serif;letter-spacing:-.02em;margin:9px 0">{html.escape(issue["title"])}</h1>
{quote_block}
<p style="color:#57534e;font-size:16px;line-height:1.75;margin:0 0 22px">{html.escape(issue["standfirst"])}</p>
<div style="padding:18px 20px;background:#fafaf9;border-left:4px solid #f59e0b"><b style="font-size:13px;color:#a16207">今天怎么读</b>{guide}</div>
<p style="margin:18px 0"><a href="{html.escape(page_url, quote=True)}" style="display:inline-block;padding:10px 15px;background:#1c1917;color:#fff;border-radius:8px;text-decoration:none;font-weight:700">打开网页版与全部来源 ↗</a></p>
{''.join(parts)}
<aside style="margin-top:34px;padding:18px;background:#f5f3ff;border-radius:9px;color:#4c1d95;line-height:1.75"><b>主编手记</b><br>{html.escape(issue["editor_note"])}</aside>
<footer style="margin-top:30px;padding-top:16px;border-top:1px solid #ded8ce;color:#78716c;font-size:12px"><p>本期共 {len(issue["stories"])} 条，全部由 Codex 阅读并编辑；脚本只负责校验、排版与发送。</p><p>欢迎转发。订阅请发送【订阅】到 19731018777@163.com。</p></footer>
</main></body></html>'''
    return issue["subject"], body


def render_page(issue, edition_label, output_dir=DOCS_DIR, base_url=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_name = f'{issue["date"]}-{issue["edition"]}.html'
    base_url = (base_url or os.environ.get("PAGES_BASE_URL") or "https://leftseinem.github.io/TrendingAI/daily").rstrip("/")
    report_url = f"{base_url}/{report_name}"
    grouped = {section: [story for story in issue["stories"] if story["section"] == section] for section in SECTIONS}
    guide = "".join(f'<a href="#{story_anchor(story)}"><span>0{index}</span><b>{html.escape(story["title"])}</b><small>{html.escape(story["teaser"])}</small></a>' for index, story in enumerate(grouped["lead"], 1))

    def page_story(story, number=None):
        prefix = f'<span class="number">0{number}</span>' if number else ""
        paragraphs = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in story["body"])
        tags = "".join(f"<span>{html.escape(tag)}</span>" for tag in story.get("tags", []))
        return f'''<article id="{story_anchor(story)}"><div class="meta">{prefix}{html.escape(story["source"]["name"])} · {html.escape(story["source"]["published_at"][:10])}</div><h3>{html.escape(story["title"])}</h3>{paragraphs}<div class="foot"><div class="tags">{tags}</div><a href="{html.escape(story["source"]["url"], quote=True)}" target="_blank">核对原始来源 ↗</a></div></article>'''

    sections = "".join(f'<section><div class="label">{index:02d}</div><h2>{html.escape(SECTION_LABELS[section])}</h2>{"".join(page_story(story, position if section == "lead" else None) for position, story in enumerate(grouped[section], 1))}</section>' for index, section in enumerate(SECTIONS, 1) if grouped[section])
    archive_path = output_dir / "archive.json"
    try:
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        archive = []
    archive = [row for row in archive if row.get("file") != report_name]
    archive.insert(0, {"date": issue["date"], "edition": edition_label, "file": report_name})
    archive = archive[:120]
    archive_path.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive_links = "".join(f'<a href="{html.escape(row["file"], quote=True)}">{html.escape(row["date"])} · {html.escape(row["edition"])}</a>' for row in archive[:16])
    quote = issue["daily_quote"]
    quote_block = f'''<blockquote class="daily-quote"><p>“{html.escape(quote["text"])}”</p><footer>— {html.escape(quote["author"])} · <a href="{html.escape(quote["source_url"], quote=True)}" target="_blank">{html.escape(quote["source_name"])}</a></footer></blockquote>'''
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(issue["subject"])}</title><style>
:root{{--paper:#fbfaf7;--ink:#1c1917;--soft:#57534e;--muted:#78716c;--line:#ddd8cf;--accent:#a16207;--blue:#1d4ed8}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.82 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}main{{width:min(100% - 36px,1040px);margin:auto;padding:38px 0 70px}}header{{padding:22px 0 32px;border-bottom:1px solid var(--line)}}.brand,.label{{font-size:12px;font-weight:850;letter-spacing:.15em;color:var(--accent)}}h1,h2,h3{{font-family:Georgia,"Noto Serif SC","Microsoft YaHei",serif;letter-spacing:-.025em}}h1{{font-size:clamp(38px,7vw,68px);line-height:1.08;margin:9px 0 16px}}header>p{{max-width:760px;color:var(--soft);font-size:18px}}.daily-quote{{max-width:760px;margin:22px 0;padding:16px 20px;border-left:3px solid #d6a756;background:#fffaf0;color:#44403c}}.daily-quote p{{margin:0;font:italic 19px/1.7 Georgia,"Noto Serif SC","Microsoft YaHei",serif}}.daily-quote footer{{margin-top:8px;color:var(--muted);font-size:12px}}.daily-quote a{{color:inherit}}nav{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-bottom:1px solid var(--line)}}nav a{{display:flex;flex-direction:column;gap:5px;padding:22px 18px;text-decoration:none;border-right:1px solid var(--line)}}nav a:first-child{{padding-left:0}}nav a:last-child{{border:0}}nav span{{color:var(--accent);font-weight:850}}nav b{{line-height:1.45}}nav small{{color:var(--muted);line-height:1.55}}.content{{width:min(100%,760px);margin:auto}}section{{padding-top:55px}}h2{{font-size:34px;margin:8px 0}}article{{padding:29px 0;border-bottom:1px solid var(--line)}}article h3{{font-size:clamp(24px,4vw,34px);line-height:1.35;margin:8px 0 15px}}article p{{margin:10px 0}}.meta{{color:var(--muted);font-size:12px}}.number{{color:var(--accent);font-weight:850;margin-right:9px}}.foot{{display:flex;justify-content:space-between;gap:16px;margin-top:17px}}.foot a{{color:var(--blue);font-weight:750;text-decoration:none}}.tags span,.archive a{{display:inline-block;padding:4px 9px;margin-right:5px;border-radius:999px;background:#eeeae2;color:var(--soft);font-size:12px;text-decoration:none}}aside{{margin:55px 0 0;padding:22px;background:#f5f3ff;border-radius:10px;color:#4c1d95}}.archive{{margin-top:48px;padding-top:24px;border-top:1px solid var(--line)}}@media(max-width:680px){{main{{width:min(100% - 30px,1040px);padding-top:18px}}h1{{font-size:36px}}nav{{display:block}}nav a{{border-right:0;border-bottom:1px solid var(--line);padding:16px 0}}section{{padding-top:42px}}h2{{font-size:28px}}.foot{{align-items:flex-start;flex-direction:column}}}}
</style></head><body><main><header><div class="brand">TRENDING AI · EDITED BY CODEX</div><h1>{html.escape(issue["title"])}</h1>{quote_block}<p>{html.escape(issue["standfirst"])}</p><small>{html.escape(issue["date"])} · {html.escape(edition_label)} · {len(issue["stories"])} 条精编</small></header><nav>{guide}</nav><div class="content">{sections}<aside><b>主编手记</b><p>{html.escape(issue["editor_note"])}</p></aside><div class="archive"><div class="label">ARCHIVE</div><h2>历史日报</h2>{archive_links}</div></div></main></body></html>'''
    (output_dir / report_name).write_text(page, encoding="utf-8")
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    return report_url


def remote_json(url, timeout=12, headers=None):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "TrendingAI-Editorial/1.0", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def marker_is_sent(marker_file, marker_key):
    try:
        if marker_file.exists() and json.loads(marker_file.read_text(encoding="utf-8")).get("status") == "sent":
            return True
    except json.JSONDecodeError:
        pass
    try:
        remote = f"https://raw.githubusercontent.com/LeftSeineM/TrendingAI/main/data/sent_markers/{marker_key}.json"
        return remote_json(remote).get("status") == "sent"
    except Exception:
        return False


def cloud_marker(marker_key, payload=None, sha=None):
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "LeftSeineM/TrendingAI")
    if not token:
        return None
    path = f"data/sent_markers/{marker_key}.json"
    url = f"https://api.github.com/repos/{repository}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "TrendingAI-Editorial/1.0"}
    if payload is None:
        try:
            data = remote_json(url, headers=headers)
            content = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
            return {
                "status": content.get("status"),
                "started_at": content.get("started_at", ""),
                "sha": data.get("sha"),
            }
        except Exception:
            return None
    body = {"message": f"chore: {payload['status']} digest {marker_key}", "content": base64.b64encode((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()).decode(), "branch": "main"}
    if sha:
        body["sha"] = sha
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**headers, "Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    return {"status": payload["status"], "sha": result.get("content", {}).get("sha")}


def sending_marker_is_fresh(marker, now=None):
    if not marker or marker.get("status") != "sending":
        return False
    try:
        started = datetime.fromisoformat(str(marker.get("started_at", "")).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return False
    return (now or datetime.now(UTC)) - started < timedelta(minutes=45)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", choices=("current", "morning", "evening"), default="current")
    parser.add_argument("--now", default="")
    parser.add_argument("--issue", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--output", default="outputs/email-preview.html")
    parser.add_argument("--docs-output", default="")
    args = parser.parse_args()
    now = parse_now(args.now)
    edition_slug, edition_label = edition_values(now, args.edition)
    date = f"{now:%Y-%m-%d}"
    marker_key = f"{date}-{edition_slug}"
    issue_path = Path(args.issue) if args.issue else EDITORIAL_DIR / f"{marker_key}.json"
    if not issue_path.exists():
        raise FileNotFoundError(f"缺少 Codex 主编稿：{issue_path}；为保证质量，本期不会退回脚本模板发送。")
    issue = load_issue(issue_path, date, edition_slug)
    if args.validate_only:
        print(f"EDITORIAL_VALID issue={issue_path} stories={len(issue['stories'])}")
        return
    marker_file = MARKER_DIR / f"{marker_key}.json"
    if not args.dry_run and marker_is_sent(marker_file, marker_key):
        print(f"SKIP_ALREADY_SENT marker={marker_key}")
        return
    page_url = render_page(issue, edition_label, args.docs_output or DOCS_DIR)
    subject, body = render_email(issue, page_url)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    if args.dry_run:
        print(f"DRY_RUN_OK issue={issue_path} stories={len(issue['stories'])} page={page_url} email={output}")
        return

    sender = os.environ["QQ_EMAIL"].strip()
    auth_code = os.environ["QQ_SMTP_AUTH_CODE"].strip()
    configured = os.environ.get("DIGEST_RECIPIENTS") or os.environ.get("DIGEST_RECIPIENT", sender)
    recipients = list(dict.fromkeys(address.strip() for address in configured.split(",") if address.strip()))
    if not recipients:
        raise RuntimeError("DIGEST_RECIPIENTS 为空")
    lock = None
    if os.environ.get("GITHUB_TOKEN"):
        existing = cloud_marker(marker_key)
        if existing and existing.get("status") == "sent":
            print(f"SKIP_MARKER_STATE marker={marker_key} status={existing['status']}")
            return
        if sending_marker_is_fresh(existing):
            print(f"SKIP_MARKER_STATE marker={marker_key} status=sending")
            return
        lock = cloud_marker(
            marker_key,
            {"marker": marker_key, "status": "sending", "started_at": datetime.now(UTC).isoformat(), "run_id": os.environ.get("GITHUB_RUN_ID", "")},
            sha=existing.get("sha") if existing else None,
        )
    with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(sender, auth_code)
        message = EmailMessage()
        message["Subject"], message["From"], message["To"] = subject, sender, sender
        message["Bcc"] = ", ".join(recipients)
        message.set_content(f"请使用支持 HTML 的邮件客户端查看。网页版：{page_url}")
        message.add_alternative(body, subtype="html")
        smtp.send_message(message)
    success = {"marker": marker_key, "status": "sent", "sent_at": datetime.now(UTC).isoformat(), "edition": edition_slug, "recipient_count": len(recipients), "page_url": page_url, "editorial_issue": str(issue_path.relative_to(ROOT)).replace("\\", "/")}
    if lock:
        cloud_marker(marker_key, success, sha=lock.get("sha"))
    else:
        MARKER_DIR.mkdir(parents=True, exist_ok=True)
        marker_file.write_text(json.dumps(success, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"SENT marker={marker_key} stories={len(issue['stories'])} recipients={len(recipients)} page={page_url}")


if __name__ == "__main__":
    main()
