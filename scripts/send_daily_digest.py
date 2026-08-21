#!/usr/bin/env python3
"""Send an editorial TrendingAI digest with GitHub, HN and Product Hunt items."""

import html
import hashlib
import argparse
import base64
import json
import os
import re
import smtplib
import ssl
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

UA = "Mozilla/5.0 (compatible; TrendingAI-Digest/2.0)"
AI_WORDS = ("ai", "agent", "llm", "gpt", "model", "machine learning",
            "transformer", "diffusion", "inference", "rag", "copilot",
            "multimodal", "人工智能", "大模型", "机器人")
APP_WORDS = ("app", "desktop", "mobile", "browser", "productivity", "voice",
             "video", "image", "file", "download", "design", "工具", "应用",
             "效率", "语音", "视频", "文件", "下载", "设计")
BUILDER_FEEDS = {
    "X 动态": "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json",
    "播客": "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-podcasts.json",
    "官方博客": "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-blogs.json",
}

EDITORIAL_FEEDS = {
    "OpenAI 官方": ("https://openai.com/news/rss.xml", "官方", 100),
    "Hugging Face 官方": ("https://huggingface.co/blog/feed.xml", "官方", 96),
    "智谱官方": ("https://wechat2rss.bestblogs.dev/feed/433d2134dca54d80804daf32e8be546155be3300.xml", "官方", 94),
    "Kimi 官方": ("https://wechat2rss.bestblogs.dev/feed/c5c43d4bc17bae656763859ed0903bb6314ec6fe.xml", "官方", 94),
    "通义官方": ("https://wechat2rss.bestblogs.dev/feed/4ebee6222ae08705b8aabc9116f0defbcb6b17c6.xml", "官方", 92),
    "腾讯混元官方": ("https://wechat2rss.bestblogs.dev/feed/306ce19a1ca590c9c2df781789e828d1acfa1356.xml", "官方", 92),
    "机器之心": ("https://wechat2rss.bestblogs.dev/feed/8d97af31b0de9e48da74558af128a4673d78c9a3.xml", "专业媒体", 90),
    "新智元": ("https://wechat2rss.bestblogs.dev/feed/e531a18b21c34cf787b83ab444eef659d7a980de.xml", "专业媒体", 86),
    "智东西": ("https://wechat2rss.bestblogs.dev/feed/cfd52b4245ca611b2fda4ef934832c689028927.xml", "专业媒体", 84),
    "数字生命卡兹克": ("https://wechat2rss.bestblogs.dev/feed/ff621c3e98d6ae6fceb3397e57441ffc6ea3c17f.xml", "个人作者", 88),
    "Simon Willison": ("https://simonwillison.net/tags/ai.atom", "个人作者", 88),
}
HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "sent_history.json"
ROOT_PATH = Path(__file__).resolve().parent.parent
MARKER_PATH = ROOT_PATH / "data" / "sent_markers"
DOCS_PATH = ROOT_PATH / "docs" / "daily"
BEIJING = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")

TOPICS = (
    (("agent", "copilot", "automation", "workflow", "智能体"),
     "AI 智能体与自动化",
     "它关注的是让模型真正执行任务，而不只是聊天。重点看它如何连接工具、保存上下文，以及失败后如何恢复。",
     "经常处理重复工作、想搭建自动化流程的人"),
    (("llm", "gpt", "model", "transformer", "inference", "大模型"),
     "大模型与推理",
     "它和模型能力、推理速度或部署成本有关。值得留意效果提升是否依赖更高算力，以及能否在本地运行。",
     "AI 开发者、模型使用者和关注成本的人"),
    (("rag", "vector", "embedding", "search", "retrieval"),
     "知识库与搜索",
     "它试图让 AI 更可靠地找到外部资料。关键看检索质量、数据更新方式，以及能否给出可核验的引用。",
     "需要企业知识库、文档问答或研究助手的人"),
    (("image", "video", "audio", "voice", "multimodal", "diffusion"),
     "多模态与内容创作",
     "它把 AI 用到了图片、视频或声音上。实际价值通常取决于可控性、生成速度和商用授权。",
     "设计师、内容创作者和产品团队"),
    (("security", "vulnerability", "privacy", "auth", "安全"),
     "安全与隐私",
     "它解决的是风险发现或数据保护问题。使用前应重点检查权限范围、数据是否上传云端，以及误报率。",
     "安全工程师、运维人员和重视数据隐私的团队"),
    (("database", "data", "sql", "analytics"),
     "数据与分析",
     "它可能帮助整理、查询或理解数据。值得看是否支持现有数据源，以及复杂查询下的准确性和性能。",
     "数据分析师、后端开发者和业务团队"),
    (("developer", "code", "programming", "sdk", "api", "cli"),
     "开发者工具",
     "它主要减少编码、调试或集成成本。判断是否好用，可以看安装复杂度、语言覆盖和能否融入现有工作流。",
     "程序员、独立开发者和技术团队"),
    (("open source", "self-host", "local", "开源"),
     "开源与本地部署",
     "它的吸引力在于可定制和数据自主。需要同时关注许可证、部署门槛、硬件要求与维护活跃度。",
     "喜欢折腾开源工具或需要私有部署的人"),
)


def fetch(url, accept="text/html,application/json,application/xml", timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def translate_zh(text):
    """Best-effort public translation; retain the original when unavailable."""
    text = clean(text)[:1000]
    if not text or sum(ord(char) < 128 for char in text) < len(text) * 0.65:
        return text
    query = urllib.parse.urlencode({
        "client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text,
    })
    try:
        data = json.loads(fetch(
            "https://translate.googleapis.com/translate_a/single?" + query,
            "application/json",
        ).decode("utf-8"))
        translated = "".join(part[0] for part in data[0] if part and part[0])
        return clean(translated) or text
    except Exception:
        return text


def ensure_chinese(text):
    """Allow English titles, but never leave an English paragraph in the body."""
    original = clean(text)
    latin = len(re.findall(r"[A-Za-z]", original))
    han = len(re.findall(r"[\u4e00-\u9fff]", original))
    if latin <= max(30, han * 2):
        return original
    translated = translate_zh(original)
    latin_after = len(re.findall(r"[A-Za-z]", translated))
    han_after = len(re.findall(r"[\u4e00-\u9fff]", translated))
    if latin_after > max(30, han_after * 2):
        return "中文摘要暂时生成失败，请打开原文查看完整内容。"
    return translated


def parse_feed_time(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("UTC"))
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None


def in_recent_days(published_at, days=3, now=None):
    """Return whether an item falls in the latest Beijing calendar days."""
    if not published_at:
        return False
    now = now or datetime.now(BEIJING)
    local_date = published_at.astimezone(BEIJING).date()
    return local_date >= (now.date() - timedelta(days=days - 1))


def extract_article_text(raw):
    """Extract likely article copy from ordinary pages and WeChat articles."""
    page = raw.decode("utf-8", "replace")
    page = re.sub(r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
                  " ", page, flags=re.I | re.S)
    candidates = []
    for pattern in (
        r'<div\b[^>]*\bid=["\']js_content["\'][^>]*>(.*?)</div>\s*(?:<script|<div[^>]+id=["\']js)',
        r"<article\b[^>]*>(.*?)</article>",
        r"<main\b[^>]*>(.*?)</main>",
    ):
        candidates.extend(clean(match) for match in re.findall(pattern, page, re.I | re.S))
    candidates = [text for text in candidates if len(text) >= 200]
    return max(candidates, key=len)[:6000] if candidates else ""


def hydrate_editorial_articles(items):
    """Replace RSS teasers with article copy, using short concurrent requests."""
    targets = [
        item for item in items
        if item.get("daily_scope") and item.get("source_class")
    ]

    def read_article(item):
        try:
            return item, extract_article_text(fetch(item["url"], timeout=10))
        except Exception:
            return item, ""

    with ThreadPoolExecutor(max_workers=min(6, len(targets) or 1)) as executor:
        for item, article_text in executor.map(read_article, targets):
            if len(article_text) > len(item.get("summary", "")) + 100:
                item["summary"] = article_text
                item["full_text"] = True
            item["weak_summary"] = (
                not item.get("full_text")
                and len(item.get("summary", "")) < 120
            )


def editorial_feeds():
    """Read every available feed article from the latest three Beijing calendar days."""
    now = datetime.now(UTC)
    result, errors = [], []
    for source, (url, source_class, authority) in EDITORIAL_FEEDS.items():
        try:
            root = ET.fromstring(fetch(url, "application/rss+xml,application/atom+xml,text/xml"))
            entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] in ("item", "entry")]
            # Do not cap before filtering: busy sources may publish many articles
            # during the three-day window.
            for entry in entries:
                fields = {}
                links = []
                for child in entry.iter():
                    key = child.tag.rsplit("}", 1)[-1]
                    if key == "link":
                        links.append(child.attrib.get("href") or (child.text or ""))
                    elif key not in fields and child.text:
                        fields[key] = child.text
                title = clean(fields.get("title", ""))
                url_value = next((clean(link) for link in links if clean(link).startswith("http")), "")
                summary = clean(fields.get("description") or fields.get("summary")
                                or fields.get("content") or fields.get("encoded") or "")
                published = fields.get("pubDate") or fields.get("published") or fields.get("updated") or ""
                published_at = parse_feed_time(published)
                if not title or not url_value:
                    continue
                if published_at and not in_recent_days(published_at, 3, now.astimezone(BEIJING)):
                    continue
                result.append({
                    "source": source,
                    "source_class": source_class,
                    "title": title,
                    "url": url_value,
                    "summary": summary[:1200] or "原始来源未提供摘要，请打开原文查看。",
                    "score": authority,
                    "created_at": published,
                    "daily_scope": in_recent_days(published_at, 3),
                })
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")
    hydrate_editorial_articles(result)
    return result, errors


def normalized_title(title):
    text = re.sub(r"https?://\S+|[\W_]+", "", title.lower())
    for word in ("重磅", "突发", "最新", "官宣", "发布", "正式", "深度", "独家"):
        text = text.replace(word, "")
    return text[:160]


def canonical_url(url):
    if re.match(r"https?://mp\.weixin\.qq\.com/s(?:\?|$)", url.strip(), re.I):
        return re.sub(r"#.*$", "", url.strip()).rstrip("/")
    return re.sub(r"[?#].*$", "", url.strip()).rstrip("/")


def deduplicate(items):
    """Prefer authoritative sources when URLs or story titles describe the same event."""
    ordered = sorted(items, key=lambda item: (
        item.get("score", 0),
        1 if item.get("source_class") == "官方" else 0,
        len(item.get("summary", "")),
    ), reverse=True)
    result, seen_urls, seen_titles = [], set(), []
    for item in ordered:
        url_key = canonical_url(item["url"])
        title_key = normalized_title(item["title"])
        if not title_key:
            continue
        duplicate_index = next(
            (index for index, old in enumerate(result)
             if canonical_url(old["url"]) == url_key
             or SequenceMatcher(None, title_key, seen_titles[index]).ratio() >= 0.78),
            None,
        )
        if duplicate_index is not None:
            primary = result[duplicate_index]
            related = primary.setdefault("related_links", [])
            if item["url"] != primary["url"] and all(link["url"] != item["url"] for link in related):
                related.append({"source": item["source"], "url": item["url"]})
            continue
        seen_urls.add(url_key)
        seen_titles.append(title_key)
        result.append(item)
    return result


def load_history():
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def filter_history(items, history=None):
    history = history if history is not None else load_history()
    now_utc = datetime.now(UTC)
    today_beijing = now_utc.astimezone(BEIJING).date()
    cutoff = now_utc - timedelta(days=30)
    active = []
    same_day_titles = []
    for row in history:
        sent_at = parse_feed_time(row.get("sent_at", ""))
        if sent_at and sent_at.astimezone(UTC) >= cutoff:
            active.append(row)
            if sent_at.astimezone(BEIJING).date() == today_beijing and row.get("title_key"):
                same_day_titles.append(row["title_key"])
    seen_urls = {row.get("url_hash") for row in active}
    seen_titles = [row.get("title_key", "") for row in active if row.get("title_key")]
    fresh = []
    for item in deduplicate(items):
        url_hash = hashlib.sha256(canonical_url(item["url"]).encode()).hexdigest()[:20]
        title_key = normalized_title(item["title"])
        if url_hash in seen_urls:
            continue
        # The afternoon edition should feel new. Use a stricter similarity
        # threshold for stories already mailed earlier on the same Beijing day,
        # including the same event reported by a different publication.
        if any(SequenceMatcher(None, title_key, old).ratio() >= 0.72
               for old in same_day_titles):
            continue
        if any(SequenceMatcher(None, title_key, old).ratio() >= 0.84 for old in seen_titles):
            continue
        item["url_hash"] = url_hash
        item["title_key"] = title_key
        fresh.append(item)
    return fresh, active


def save_history(sent_items, history):
    now = datetime.now(ZoneInfo("UTC")).isoformat()
    rows = history + [{
        "url_hash": item["url_hash"],
        "title_key": item["title_key"],
        "sent_at": now,
    } for item in sent_items if item.get("url_hash")]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(rows[-600:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def github_trending():
    page = fetch("https://github.com/trending?since=daily").decode("utf-8", "replace")
    result = []
    for article in re.findall(r"<article[^>]*Box-row[^>]*>(.*?)</article>", page, re.S | re.I):
        match = re.search(r'href="(/[^/\"\s]+/[^/\"\s]+)"', article)
        if not match:
            continue
        path = match.group(1)
        desc = re.search(r"<p[^>]*>(.*?)</p>", article, re.S | re.I)
        stars = re.search(r"([\d,]+)\s+stars\s+today", clean(article), re.I)
        result.append({
            "source": "GitHub Trending",
            "title": path.strip("/"),
            "url": "https://github.com" + path,
            "summary": clean(desc.group(1)) if desc else "",
            "score": int(stars.group(1).replace(",", "")) if stars else 0,
            "created_at": "",
            "time_label": "今日榜单抓取",
        })
    return result[:25]


def hacker_news():
    data = json.loads(fetch("https://hn.algolia.com/api/v1/search?tags=front_page").decode())
    return [{
        "source": "Hacker News",
        "title": hit.get("title") or hit.get("story_title"),
        "url": hit.get("url") or "https://news.ycombinator.com/item?id=" + hit["objectID"],
        "summary": f"这条内容在 Hacker News 获得 {hit.get('points', 0)} 分，并有 {hit.get('num_comments', 0)} 条讨论。",
        "score": int(hit.get("points") or 0),
        "created_at": hit.get("created_at", ""),
    } for hit in data.get("hits", []) if hit.get("title") or hit.get("story_title")][:30]


def product_hunt():
    ns = "{http://www.w3.org/2005/Atom}"
    root = ET.fromstring(fetch("https://www.producthunt.com/feed", "application/xml,text/xml"))
    result = []
    for entry in root.findall(ns + "entry"):
        link = entry.find(ns + "link")
        title = (entry.findtext(ns + "title") or "").strip()
        url = link.get("href", "") if link is not None else ""
        if title and url:
            result.append({
                "source": "Product Hunt",
                "title": title,
                "url": url,
                "summary": clean(entry.findtext(ns + "content") or entry.findtext(ns + "summary")),
                "score": 0,
                "created_at": entry.findtext(ns + "updated") or entry.findtext(ns + "published") or "",
            })
    return result[:25]


def chinese_indie_apps():
    """Read the newest application entries from the main independent-developer board."""
    raw = fetch(
        "https://raw.githubusercontent.com/1c7/chinese-independent-developer/"
        "master/README.md",
        "text/plain",
    ).decode("utf-8", "replace")
    headings = list(re.finditer(
        r"(?m)^###\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]添加\s*$",
        raw,
    ))
    result = []
    for index, heading in enumerate(headings[:3]):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(raw)
        section = raw[heading.end():end]
        date_text = f"{heading.group(1)}-{int(heading.group(2)):02d}-{int(heading.group(3)):02d}"
        for match in re.finditer(
            r"(?m)^\s*[*-]\s*(?::white_check_mark:|:clock\d*:|✅|🕗)?\s*"
            r"\[([^\]]+)\]\((https?://[^)]+)\)\s*[：:]\s*(.+?)\s*$",
            section,
        ):
            title, url, description = match.groups()
            description = clean(description)
            if title and description:
                result.append({
                    "source": "独立开发者新品",
                    "title": title.strip(),
                    "url": url.strip(),
                    "summary": f"{description}（{date_text} 收录）",
                    "score": max(90 - index * 15, 50),
                    "created_at": date_text,
                    "time_label": "收录时间",
                })
    return result[:20]


def scrapling_updates():
    """Include Scrapling only when a recent release is available."""
    data = json.loads(fetch(
        "https://api.github.com/repos/D4Vinci/Scrapling/releases/latest",
        "application/vnd.github+json",
    ).decode())
    published = data.get("published_at", "")
    published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
    if datetime.now(published_at.tzinfo) - published_at > timedelta(days=30):
        return []
    notes = clean(data.get("body", ""))[:500]
    return [{
        "source": "关注项目 · Scrapling",
        "title": f"Scrapling {data.get('name') or data.get('tag_name') or '新版本'}",
        "url": data.get("html_url") or "https://github.com/D4Vinci/Scrapling",
        "summary": notes or "Scrapling 发布了新版本：它是一套能适应网页变化、支持动态页面和批量任务的网页采集工具。",
        "score": 80,
        "created_at": published,
    }]


def follow_builders():
    """Read follow-builders' public central feeds without scraping X or YouTube."""
    result, feed_errors = [], []
    for kind, url in BUILDER_FEEDS.items():
        try:
            data = json.loads(fetch(url, "application/json").decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Feed 根节点不是对象")
            if kind == "X 动态":
                groups = data.get("x", [])
                if not isinstance(groups, list):
                    raise ValueError("x 字段结构变化")
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    name = clean(group.get("name") or group.get("handle") or "AI Builder")
                    for post in group.get("tweets", []) if isinstance(group.get("tweets"), list) else []:
                        if not isinstance(post, dict):
                            continue
                        text = clean(post.get("text"))
                        url_value = post.get("url")
                        if len(text) < 45 or not url_value:
                            continue
                        engagement = sum(int(post.get(key) or 0) for key in ("likes", "retweets", "replies"))
                        result.append({
                            "source": "AI 人物与观点",
                            "kind": kind,
                            "title": name,
                            "url": str(url_value),
                            "summary": text,
                            "created_at": post.get("createdAt", ""),
                            "score": engagement,
                            "daily_scope": in_recent_days(
                                parse_feed_time(post.get("createdAt", ""))
                            ),
                        })
            else:
                key = "podcasts" if kind == "播客" else "blogs"
                entries = data.get(key, [])
                if not isinstance(entries, list):
                    raise ValueError(f"{key} 字段结构变化")
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    title = clean(entry.get("title") or entry.get("episodeTitle") or entry.get("name"))
                    name = clean(entry.get("podcast") or entry.get("show") or entry.get("source")
                                 or entry.get("author") or entry.get("publisher") or kind)
                    text = clean(entry.get("summary") or entry.get("description")
                                 or entry.get("content") or entry.get("transcript"))
                    url_value = entry.get("url") or entry.get("link") or entry.get("videoUrl")
                    if title and url_value:
                        result.append({
                            "source": "AI 人物与观点",
                            "kind": kind,
                            "title": name,
                            "url": str(url_value),
                            "summary": f"{title}：{text}" if text else title,
                            "created_at": entry.get("publishedAt") or entry.get("published")
                                          or entry.get("date") or "",
                            "score": 200,
                            "daily_scope": in_recent_days(parse_feed_time(
                                entry.get("publishedAt") or entry.get("published")
                                or entry.get("date") or ""
                            )),
                        })
            for error in data.get("errors", []) if isinstance(data.get("errors"), list) else []:
                feed_errors.append(f"follow-builders {kind}: {clean(str(error))[:180]}")
        except Exception as exc:
            feed_errors.append(f"follow-builders {kind}: {type(exc).__name__}: {exc}")
    if not result and len(feed_errors) == len(BUILDER_FEEDS):
        raise RuntimeError("；".join(feed_errors))
    return result, feed_errors


def collect():
    items, errors = [], []
    editorial, editorial_errors = editorial_feeds()
    items.extend(editorial)
    errors.extend(editorial_errors)
    for name, loader in (("独立开发者新品", chinese_indie_apps),
                         ("Scrapling 更新", scrapling_updates),
                         ("GitHub Trending", github_trending),
                         ("Hacker News", hacker_news),
                         ("Product Hunt", product_hunt)):
        try:
            items.extend(loader())
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    try:
        builders, builder_errors = follow_builders()
        items.extend(builders)
        errors.extend(builder_errors)
    except Exception as exc:
        errors.append(f"follow-builders: {type(exc).__name__}: {exc}")
    if not items:
        raise RuntimeError("全部资讯源抓取失败：" + "；".join(errors))
    return items, errors


def rank(item):
    text = (item["title"] + " " + item["summary"]).lower()
    app_bonus = sum(word in text for word in APP_WORDS)
    source_bonus = 7 if item["source"] == "Product Hunt" else 0
    technical_penalty = sum(word in text for word in
                            ("framework", "library", "sdk", "api", "cli", "inference", "benchmark"))
    return (sum(word in text for word in AI_WORDS),
            app_bonus + source_bonus - technical_penalty * 3,
            item["score"])


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=ZoneInfo("UTC"))


def display_time(item):
    value = item.get("created_at", "")
    label = item.get("time_label", "发布时间")
    if value:
        parsed = parse_feed_time(str(value))
        if parsed:
            return f"{label}：{parsed.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M}"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
            return f"{label}：{value}"
    if item.get("time_label") == "今日榜单抓取":
        return f"榜单时间：{datetime.now(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M}"
    return "发布时间：原始来源未提供"


def select_builder_items(items, now):
    candidates = [item for item in items if item["source"] == "AI 人物与观点"]
    useful_words = AI_WORDS + ("product", "build", "startup", "codex", "claude",
                               "openai", "anthropic", "tool", "workflow")
    candidates = [item for item in candidates if (
        item["kind"] in ("播客", "官方博客")
        or (
            len(item["summary"]) >= 120
            and any(
                re.search(r"\bai\b", (item["summary"] + " " + item["title"]).lower())
                if word == "ai" else word in (item["summary"] + " " + item["title"]).lower()
                for word in useful_words
            )
        )
    )]
    candidates.sort(key=lambda item: (item["score"], len(item["summary"])), reverse=True)
    is_afternoon = now.hour >= 16
    morning_cutoff = now.replace(hour=11, minute=0, second=0, microsecond=0)
    new_items, morning_items, seen = [], [], set()
    for item in candidates:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        created = parse_time(item.get("created_at")).astimezone(ZoneInfo("Asia/Shanghai"))
        item["morning_repeat"] = is_afternoon and created < morning_cutoff
        (morning_items if item["morning_repeat"] else new_items).append(item)
    return (new_items + morning_items)[:4]


def item_tags(item):
    if item.get("ai_tags"):
        return item["ai_tags"]
    text = (item["title"] + " " + item["summary"]).lower()
    tags = []
    rules = (
        (("agent", "智能体", "automation", "workflow"), "Agent"),
        (("robot", "robotics", "具身", "机器人"), "具身智能"),
        (("llm", "大模型", "language model", "gpt", "claude"), "大模型"),
        (("image", "video", "audio", "music", "图像", "视频", "音乐"), "内容创作"),
        (("security", "privacy", "安全", "隐私"), "安全"),
        (("startup", "founder", "创业", "融资"), "创业"),
        (("research", "paper", "benchmark", "研究", "论文"), "研究"),
        (("developer", "code", "api", "sdk", "cli", "开发"), "开发工具"),
        (("productivity", "效率", "办公", "note"), "效率工具"),
    )
    for words, tag in rules:
        if any(word in text for word in words):
            tags.append(tag)
    if item.get("source_class"):
        tags.insert(0, item["source_class"])
    if item["source"] in ("独立开发者新品", "Product Hunt"):
        tags.insert(0, "产品")
    elif item["source"] in ("GitHub Trending", "关注项目 · Scrapling"):
        tags.insert(0, "开源")
    elif item["source"] == "Hacker News":
        tags.insert(0, "科技新闻")
    elif item["source"] == "AI 人物与观点":
        tags.insert(0, "人物观点")
    names = {"AI": "人工智能", "Agent": "智能体", "API": "接口", "CLI": "命令行工具"}
    return [names.get(tag, tag) for tag in list(dict.fromkeys(tags or ["人工智能"]))[:3]]


def tag_html(item):
    tag_names = {
        "AI": "人工智能",
        "Agent": "智能体",
        "API": "接口",
        "CLI": "命令行工具",
        "Open Source": "开源",
    }
    return "".join(
        f'<span style="display:inline-block;margin:0 5px 5px 0;padding:3px 8px;'
        f'border-radius:999px;background:#e0e7ff;color:#3730a3;font-size:12px">'
        f'{html.escape(tag_names.get(tag, tag))}</span>'
        for tag in item_tags(item)
    )


def back_to_toc():
    return '<a href="#toc" style="font-size:12px;color:#64748b;text-decoration:none">↑ 返回目录</a>'


def detail_link(item):
    if not item.get("detail_url"):
        return ""
    return (f' · <a href="{html.escape(item["detail_url"], quote=True)}" '
            'style="color:#7c3aed;text-decoration:none">阅读详细版 →</a>')


def builder_card(item):
    said = ensure_chinese(item.get("ai_first_value") or item["summary"])[:700]
    text = (item["summary"] + " " + item["title"]).lower()
    if any(word in text for word in ("product", "build", "startup", "launch", "用户", "产品")):
        why = "AI 产品正在从概念进入真实用户和商业验证阶段，这条内容能帮助判断需求、定位或发布方式。"
    elif any(word in text for word in ("code", "codex", "developer", "agent", "workflow")):
        why = "开发工具和工作流正在快速变化，一线建设者的实际反馈比单纯的功能宣传更有参考价值。"
    else:
        why = "这条内容补充了产品新闻背后的行业判断，有助于区分短期热点与长期变化。"
    repeat = '<div style="margin-top:6px;color:#b45309;font-weight:600">上午已收录</div>' if item.get("morning_repeat") else ""
    return (
        '<div style="padding:12px;background:#fff;border:1px solid #e5e7eb;border-radius:8px;margin:8px 0">'
        f'<div style="font-size:15px;font-weight:700">{html.escape(item["title"])} '
        f'<span style="font-size:12px;color:#7c3aed">· {html.escape(item["kind"])}</span></div>'
        f'<div style="margin-top:5px;color:#64748b;font-size:12px">{html.escape(display_time(item))}</div>'
        f'<div style="margin-top:7px">{tag_html(item)}</div>'
        f'<div style="margin-top:6px;line-height:1.55"><b>{html.escape(item.get("ai_first_label", "核心观点"))}：</b>{html.escape(said)}</div>'
        f'<div style="margin-top:6px;line-height:1.55"><b>{html.escape(item.get("ai_second_label", "为什么现在值得注意"))}：</b>{html.escape(ensure_chinese(item.get("ai_second_value", why)))}</div>'
        f'{repeat}<div style="margin-top:9px"><a href="{html.escape(item["url"], quote=True)}" '
        f'style="color:#2563eb">查看原始内容 →</a>{detail_link(item)}</div></div>'
    )


def editorial_type(item):
    text = (item["title"] + " " + item["summary"]).lower()
    if item["source"] in ("独立开发者新品", "Product Hunt"):
        return "product"
    if item["source"] in ("GitHub Trending", "关注项目 · Scrapling"):
        return "open_source"
    if any(word in text for word in ("research", "paper", "model", "benchmark", "研究", "论文", "模型")):
        return "research"
    return "news"


def editorial_fields(item):
    if all(item.get(f"ai_{key}") for key in ("first_label", "first_value", "second_label", "second_value")):
        return ("AI 编辑", item["ai_first_label"], ensure_chinese(item["ai_first_value"]),
                item["ai_second_label"], ensure_chinese(item["ai_second_value"]))
    kind = editorial_type(item)
    summary = ensure_chinese(item["summary"][:650] or "原始来源暂未提供简介，建议打开链接查看完整说明。")
    text = (item["title"] + " " + item["summary"]).lower()
    if kind == "product":
        if any(word in text for word in ("cli", "api", "sdk", "python", "framework", "部署")):
            threshold = "需要安装、配置或一定技术基础，建议先看文档和演示。"
        elif any(word in text for word in ("web", "browser", "online", "网页", "在线")):
            threshold = "通常打开网页即可体验，使用门槛较低；付费与地区限制需以产品页为准。"
        else:
            threshold = "先查看支持的平台、免费额度和是否需要注册，再决定是否安装或付费。"
        return "产品", "能干什么", summary, "使用门槛", threshold
    if kind == "open_source":
        trial = ("适合开发者试用；普通用户如果没有部署或二次开发需求，可以先收藏观察。"
                 if any(word in text for word in ("cli", "api", "sdk", "python", "framework", "library"))
                 else "如果有在线演示或安装包，可以直接试用；否则先看 README、更新频率和 Issue。")
        return "开源项目", "解决什么问题", summary, "是否值得尝试", trial
    if kind == "research":
        impact = ("短期内主要影响开发者和产品能力，普通用户会逐步在搜索、创作或智能助手中感受到变化。"
                  if not any(word in text for word in ("cost", "price", "cheap", "local", "成本", "本地"))
                  else "更低成本或本地运行会让普通用户更容易获得相关能力，也可能带来新的隐私与选择空间。")
        return "研究 / 模型", "能力变化", summary, "普通人会受到什么影响", impact
    if any(word in text for word in ("security", "privacy", "regulation", "安全", "隐私", "监管")):
        impact = "它可能改变产品的合规、安全边界或用户数据处理方式，值得关注后续政策与实际执行。"
    elif any(word in text for word in ("launch", "release", "model", "product", "发布", "推出")):
        impact = "它可能改变现有产品竞争、价格或可用能力，但仍需等待真实体验和后续反馈。"
    else:
        impact = "目前更像一个行业信号，是否产生长期影响要看后续采用、成本和用户反馈。"
    return "新闻", "发生了什么", summary, "有何影响", impact


def editorial_card(item, number=None):
    prefix = f"{number}. " if number else ""
    kind, first_label, first_value, second_label, second_value = editorial_fields(item)
    return (
        '<div style="padding:22px 0;border-bottom:1px solid #e7e5e4">'
        f'<div style="font-size:12px;line-height:1.4;color:#78716c;margin-bottom:8px">{html.escape(item["source"])} · {kind} · {html.escape(display_time(item))}</div>'
        f'<div style="font-size:20px;line-height:1.4;font-weight:750;letter-spacing:-.01em">{prefix}<a style="color:#1c1917;text-decoration:none" '
        f'href="{html.escape(item["url"], quote=True)}">{html.escape(item["title"])}</a></div>'
        f'<div style="margin-top:10px">{tag_html(item)}</div>'
        f'<div style="margin-top:10px;color:#292524;font-size:16px;line-height:1.78"><b>{html.escape(first_label)}：</b>{html.escape(first_value)}</div>'
        f'<div style="margin-top:9px;color:#57534e;font-size:15px;line-height:1.78"><b>{html.escape(second_label)}：</b>{html.escape(second_value)}</div>'
        f'<div style="margin-top:12px"><a href="{html.escape(item["url"], quote=True)}" '
        f'style="color:#1d4ed8;text-decoration:none;font-weight:700">打开原文 ↗</a>{detail_link(item)}</div>'
        "</div>"
    )


def compact_link(item):
    summary = ensure_chinese(item.get("ai_first_value") or item.get("summary", ""))[:160]
    return (
        '<div style="padding:14px 0;border-bottom:1px solid #e7e5e4;line-height:1.65">'
        f'<a href="{html.escape(item["url"], quote=True)}" '
        f'style="color:#1c1917;text-decoration:none;font-size:16px;font-weight:700">{html.escape(item["title"])}</a>'
        f'<div style="margin-top:4px;color:#78716c;font-size:12px">{html.escape(item["source"])} · {html.escape(display_time(item))}</div>'
        f'<div style="margin-top:6px;color:#57534e;font-size:14px">{html.escape(summary)}</div></div>'
    )


def reader_line(item, limit=92):
    """Return the useful first sentence used in the issue's reading guide."""
    text = ensure_chinese(item.get("ai_first_value") or item.get("summary", ""))
    sentence = re.split(r"(?<=[。！？])", text, maxsplit=1)[0].strip()
    return (sentence or text)[:limit]


EDITORIAL_PROMPT = """你是《每日 AI 日报》的主编，而不是摘要生成器。你的读者是对新知识有好奇心的大学生，以及普通产品经理、创业者和开发者；他们聪明，但没有时间研究论文、榜单或底层参数。

你的目标不是把原文压缩一遍，而是替读者完成一次编辑判断：先讲清楚真正发生了什么，再说明它与学习、工作、创作、消费、求职或使用数字产品有什么关系。读者看完应当知道“这件事为什么值得占用我一分钟”，必要时还能产生一个值得继续想的问题。

【编辑步骤】
1. 只从输入的标题、摘要、来源和时间中提取可以核验的事实，不猜测原文未提供的功能、价格、结论或影响。
2. 找出这条信息唯一最重要的新变化。不要把所有细节平均罗列，也不要复述标题提出的问题。
3. 判断它更接近产品、新闻、人物观点、开源项目、研究/模型或产业事件，再选择最适合它的叙述方式。
4. 把专业概念翻译成具体场景：它可能改变谁的哪一步工作，节省什么，增加什么限制，或让哪件以前很难的事变得可行。
5. 区分“现在就能使用”“正在测试”“研究结果”和“未来信号”。尚未落地的内容不得写成已经可用。
6. 如果素材不足以支持有价值的解读，诚实说明目前能确认到哪里，不要用空泛判断补齐篇幅。
7. 这不是“资讯清单”。宁可只保留一句有用的事实，也不要把原始抓取文本、社交媒体口号、榜单热度或无关评论改写成长段正文。

【不同素材怎么写】
- 产品或应用：优先写它能替读者完成什么任务、替代哪种繁琐做法，以及真实使用门槛，如是否要安装、付费、懂代码、上传隐私数据。不要把功能列表逐项抄写。
- 新闻或公司动态：写清事件本身和已经能够确认的变化。只有存在具体影响时才谈影响；普通公告可以短写，不必硬凑“适合谁”。
- 人物观点：提炼核心主张及其语境，并说明这个观点为什么此刻值得讨论。观点不是事实，要保留说话者和来源。
- 开源项目：说明它解决的实际问题、有没有界面或可直接体验的入口、普通人是否需要配置环境。Star 数、编程语言和框架名称只能作辅助信息。
- 研究或模型：把论文指标翻译成能力边界和可能的实际变化。说明这是实验结果还是已进入产品的能力。
- 产业、商业或创业：说明谁做了什么决定，以及它可能改变成本、竞争、渠道或用户选择的哪一部分，避免空洞的“行业将迎来变革”。

【关于跑分、榜单和参数】
- 跑分只是支持判断的证据，不是结论，更不是文章主角。
- 不要用“获得了多少分”“参数达到多少”作为正文开头，也不能整段只比较数字。
- 必须先解释测试测的是什么能力，再说明这种能力若进入真实产品，用户可能在哪个场景感受到变化。
- 如果现有材料无法证明普通用户会受益，就明确写“目前主要是研究或开发层面的进展”，不要强行制造日常意义。
- 除非数字直接影响价格、速度、设备要求或使用限制，否则只保留最必要的一个数字。

【语言与可读性】
- 标题可保留英文原名；tags、段落标签和正文必须是自然中文，不得输出完整英文句子或英文段落。
- 使用具体名词和动词，短句优先。避免翻译腔、宣传腔、论文腔和一串术语。
- 格式不必死板。两段可以分别承担“事实 + 编辑解读”“场景 + 限制”“核心观点 + 值得想一想”等不同任务。
- first_label 和 second_label 应根据内容自然命名，例如“真正的新变化”“你可能在哪里用到”“现在值得试吗”“值得想一想”“需要留意”。不要机械地给所有同类内容使用相同标签。
- 两段必须互相补充，不能换一种说法重复标题。每段通常 1～2 句，两段合计约 100～220 个汉字；简单新闻可以更短，复杂内容可以略长。
- 可以给出克制的编辑判断，但必须能由输入事实支持。不要反复使用“目前更像一个行业信号”“长期影响要看后续采用”“值得持续关注”等万能句。
- 不要把每条都写成“它是什么、为什么值得看、适合谁”的流水账。没有实际意义的栏目可以省略，由两个灵活标签承载真正重要的信息。
- 不在正文中裸露 URL，链接由排版程序单独加入。
- 如果输入只是“在某个平台获得多少票 / 多少讨论”，没有原始事件或文章摘要，请不要为该项输出内容；它不应进入日报正文。
- 不要复用“目前更像一个行业信号”“值得持续关注”“普通用户会逐步感受到变化”等句式。没有具体、可验证的影响时，直接说明“现有信息不足以判断实际影响”。

【质量对照】
不合格：某模型在基准测试中获得 92.3 分，性能显著提升，值得关注。
合格：这次提升集中在长文档检索：模型更不容易漏掉藏在几十页材料里的条件。如果它进入你常用的阅读工具，最直接的变化不是“回答更聪明”，而是查合同、论文或课程资料时少一次人工翻找；目前仍是测试结果，不能等同于现有产品体验。

不合格：该工具功能强大，适合开发者和普通用户使用。
合格：它把网页里的表格和正文直接整理成可继续处理的数据，省掉复制、清洗这一步。已有 Python 环境的人可以很快试用；只想点开即用的普通用户仍会被安装和配置挡住。

【输出格式】
只返回一个合法 JSON 对象：{\"items\":[...]}，不得加 Markdown 代码块或额外说明。每项必须保留输入 id，并包含：
- tags：1～3 个简短中文 Tag；
- first_label：第一段的自然中文小标题；
- first_value：第一段正文；
- second_label：第二段的自然中文小标题；
- second_value：第二段正文。

输入如下：
"""


def ai_enrich(items):
    """Optionally edit selected items with an OpenAI-compatible chat API."""
    api_key = os.environ.get("AI_API_KEY", "").strip()
    base_url = os.environ.get("AI_BASE_URL", "").strip().rstrip("/")
    model = os.environ.get("AI_MODEL", "").strip()
    if not (api_key and base_url and model and items):
        return
    prompt_prefix = EDITORIAL_PROMPT
    # A previous version sent up to twelve long articles in one request. Flash
    # models often timed out or returned incomplete JSON, after which the digest
    # silently fell back to boilerplate. Small editorial batches make a complete
    # piece of copy much more reliable than a large summarisation job.
    batch_size = 4
    failures = []

    def usable_copy(value):
        value = clean(str(value or ""))
        han_count = len(re.findall(r"[\u4e00-\u9fff]", value))
        banned = ("中文摘要暂时生成失败", "目前更像一个行业信号", "值得持续关注")
        return han_count >= 18 and not any(phrase in value for phrase in banned)

    def edit_batch(start):
        batch = items[start:start + batch_size]
        payload_items = [{
            "id": start + index,
            "source": item["source"],
            "title": item["title"],
            "summary": item["summary"][:1600],
            "kind": item.get("kind", ""),
            "published_at": item.get("created_at", ""),
        } for index, item in enumerate(batch)]
        request_body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一位谨慎、具体、会删掉无价值内容的中文科技主编。"},
                {"role": "user", "content": prompt_prefix + json.dumps(payload_items, ensure_ascii=False)},
            ],
            "temperature": 0.35,
            "max_tokens": 2600,
            "response_format": {"type": "json_object"},
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=request_body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
            parsed = json.loads(content)
            rows = parsed.get("items", parsed) if isinstance(parsed, dict) else parsed
            if not isinstance(rows, list):
                return []
            return rows
        except Exception as exc:
            failures.append(type(exc).__name__)
            return []

    starts = list(range(0, len(items), batch_size))
    with ThreadPoolExecutor(max_workers=min(3, len(starts))) as executor:
        futures = {executor.submit(edit_batch, start): start for start in starts}
        for future in as_completed(futures):
            try:
                rows = future.result()
            except Exception:
                rows = []
            for row in rows:
                index = row.get("id") if isinstance(row, dict) else None
                if not isinstance(index, int) or not 0 <= index < len(items):
                    continue
                tags = row.get("tags")
                values = {key: clean(str(row.get(key, ""))) for key in
                          ("first_label", "first_value", "second_label", "second_value")}
                # Do not let a half-finished JSON response overwrite the stable
                # fallback copy. An item only becomes AI-edited when both ideas
                # read like complete Chinese sentences.
                if not (usable_copy(values["first_value"]) and usable_copy(values["second_value"]) and
                        values["first_label"] and values["second_label"]):
                    continue
                if isinstance(tags, list):
                    items[index]["ai_tags"] = [clean(str(tag))[:16] for tag in tags[:3] if clean(str(tag))]
                for key, value in values.items():
                    items[index][f"ai_{key}"] = value[:500]
    edited = sum(all(item.get(f"ai_{key}") for key in
                     ("first_label", "first_value", "second_label", "second_value")) for item in items)
    print(f"AI_EDITORIAL edited={edited}/{len(items)} failed_batches={len(failures)}")


def low_signal_item(item):
    """Discard items that cannot support a useful reader-facing paragraph."""
    summary = clean(item.get("summary", ""))
    if item.get("weak_summary") or "中文摘要暂时生成失败" in summary:
        return True
    if item.get("source") == "Hacker News" and summary.startswith("这条内容在 Hacker News 获得"):
        return True
    if len(summary) < 55:
        return True
    return False


def render(items, errors, now=None, enrich=True, page_url="", edition_override=None):
    now = now or datetime.now(BEIJING)
    edition = edition_override or ("上午篇" if now.hour < 18 else "下午篇")
    # A good daily is an edited selection, not a three-day crawl rendered in
    # full. Low-signal entries are still available at their source, but do not
    # earn space in a reader's inbox merely because they were easy to fetch.
    usable_items = [x for x in items if not low_signal_item(x)]
    authority = [x for x in usable_items if x.get("source_class") in ("官方", "专业媒体", "个人作者")]
    authority.sort(key=lambda x: (
        1 if x.get("daily_scope") else 0,
        x.get("score", 0),
        parse_time(x.get("created_at")),
    ), reverse=True)
    highlights, source_counts = [], {}
    for item in (x for x in authority if not x.get("weak_summary")):
        if source_counts.get(item["source"], 0) >= 1:
            continue
        highlights.append(item)
        source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1
        if len(highlights) == 3:
            break
    if len(highlights) < 3:
        highlights += [x for x in authority if x not in highlights and not x.get("weak_summary")][:3 - len(highlights)]
    highlight_urls = {x["url"] for x in highlights}
    authoritative_more, source_counts = [], {}
    for item in authority:
        if (item["url"] in highlight_urls or item.get("weak_summary")
                or source_counts.get(item["source"], 0) >= 2):
            continue
        authoritative_more.append(item)
        source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1
        if len(authoritative_more) == 3:
            break
    used_urls = highlight_urls | {x["url"] for x in authoritative_more}
    indie_all = [x for x in usable_items if x["source"] == "独立开发者新品"][:3]
    indie = indie_all[:2]
    used_urls |= {x["url"] for x in indie_all}
    builders = [x for x in select_builder_items(usable_items, now) if x["url"] not in used_urls
                and not low_signal_item(x)][:2]
    used_urls |= {x["url"] for x in builders}
    discovered = [x for x in usable_items if x["url"] not in used_urls and not x.get("source_class")
                  and x["source"] not in ("独立开发者新品", "AI 人物与观点")]
    tech = sorted(discovered, key=rank, reverse=True)[:3]
    used_urls |= {x["url"] for x in tech}
    original_sources = ("GitHub Trending", "Hacker News", "Product Hunt", "关注项目 · Scrapling")
    trend_latest = sorted(
        [x for x in usable_items if x["source"] in original_sources and x["url"] not in used_urls],
        key=lambda x: (parse_time(x.get("created_at")), x.get("score", 0)), reverse=True,
    )[:4]
    used_urls |= {x["url"] for x in trend_latest}
    remaining = [x for x in usable_items if x["url"] not in used_urls
                 and x["source"] != "独立开发者新品"
                 and x["source"] not in original_sources and
                 (x["source"] != "AI 人物与观点" or len(x.get("summary", "")) >= 120)]
    # Keep the latest three days complete; older non-editorial fallback items are
    # capped so the digest never turns into an archive dump.
    recent_more = [x for x in remaining if x.get("daily_scope")]
    older_more = [x for x in remaining if not x.get("daily_scope")]
    more = sorted(recent_more, key=lambda x: (x.get("score", 0), rank(x)), reverse=True)[:6]
    more += sorted(older_more, key=lambda x: (x.get("score", 0), rank(x)), reverse=True)[:2]
    indie_more = indie_all[3:]
    selected, selected_urls = [], set()
    for item in highlights + authoritative_more + indie_all + builders + tech + more + trend_latest:
        if item["url"] not in selected_urls:
            selected.append(item)
            selected_urls.add(item["url"])
    if enrich:
        ai_enrich(selected)
    warning = '<p style="color:#64748b">本次所有来源均正常。</p>'
    if errors:
        warning = '<p style="background:#fff7ed;padding:10px">部分来源暂时不可用：' + html.escape("；".join(errors)) + "</p>"
    practical_pool = indie + [x for x in tech + authoritative_more if editorial_type(x) in ("product", "open_source")]
    practical, seen = [], set(highlight_urls)
    for item in practical_pool:
        if item["url"] not in seen:
            practical.append(item)
            seen.add(item["url"])
        if len(practical) == 2:
            break
    thinking_pool = builders + [x for x in tech + authoritative_more if editorial_type(x) == "research"]
    thinking = []
    for item in thinking_pool:
        if item["url"] not in seen:
            thinking.append(item)
            seen.add(item["url"])
        if len(thinking) == 2:
            break
    briefing = [x for x in more + trend_latest + authoritative_more + tech if x["url"] not in seen][:6]
    reading_minutes = max(3, round((sum(len(reader_line(x, 500)) for x in highlights + practical + thinking) + 420) / 500))
    guide = "".join(
        f'<div style="padding:9px 0;border-bottom:1px solid #e7e5e4"><a href="{html.escape(x["url"], quote=True)}" style="color:#1c1917;text-decoration:none;font-weight:750">{i}. {html.escape(x["title"])}</a><div style="margin-top:4px;color:#57534e">{html.escape(reader_line(x))}</div></div>'
        for i, x in enumerate(highlights, 1)
    ) or '<p style="color:#78716c">本期没有足够可靠的重点内容。</p>'
    body = f"""<!doctype html><html lang="zh-CN"><body style="margin:0;background:#f5f5f4;font-family:Arial,'Microsoft YaHei',sans-serif;color:#292524">
<div style="max-width:700px;margin:auto;background:#fff;padding:34px 30px;font-size:15px;line-height:1.75">
<div style="font-size:12px;color:#a16207;font-weight:800;letter-spacing:.12em">TRENDING AI · {edition}</div>
<h1 style="font-size:30px;line-height:1.2;letter-spacing:-.03em;margin:8px 0 10px">今天的 AI，先读这三件事</h1>
<p style="color:#78716c;margin:0 0 22px">{now:%Y-%m-%d} · 北京时间 · 约 {reading_minutes} 分钟读完</p>
<div style="padding:18px 20px;background:#fafaf9;border-left:4px solid #f59e0b;margin:0 0 28px">
<div style="font-size:13px;font-weight:800;color:#a16207;margin-bottom:4px">今日阅读路线</div>{guide}</div>
{f'<p style="margin:0 0 28px"><a href="{html.escape(page_url, quote=True)}" style="display:inline-block;padding:11px 17px;background:#1c1917;color:#fff;border-radius:8px;text-decoration:none;font-weight:750">打开完整日报与资料库 ↗</a></p>' if page_url else ''}
<h2 style="font-size:13px;letter-spacing:.1em;color:#a16207;margin:30px 0 0">01 · 今天最值得知道</h2>
{''.join(editorial_card(x, i) for i, x in enumerate(highlights, 1)) if highlights else '<p>本期暂无足够可靠的重点内容。</p>'}
{f'<h2 style="font-size:13px;letter-spacing:.1em;color:#166534;margin:34px 0 0">02 · 可以马上试试</h2><p style="color:#78716c;margin:7px 0 0">只收录能说清用途、门槛和限制的工具。</p>{"".join(editorial_card(x) for x in practical)}' if practical else ''}
{f'<h2 style="font-size:13px;letter-spacing:.1em;color:#6d28d9;margin:34px 0 0">03 · 值得想一想</h2><p style="color:#78716c;margin:7px 0 0">研究、观点和仍未落地的变化，与现成产品分开看。</p>{"".join(editorial_card(x) for x in thinking)}' if thinking else ''}
<h2 style="font-size:13px;letter-spacing:.1em;color:#475569;margin:34px 0 0">04 · 其余速览</h2>
{''.join(compact_link(x) for x in briefing) if briefing else '<p style="color:#78716c">没有需要补充的短讯。</p>'}
<div style="margin-top:34px;padding-top:18px;border-top:1px solid #d6d3d1;font-size:13px;color:#78716c">{warning}
<p>欢迎转发。订阅请发送【订阅】到 19731018777@163.com。</p>
<p style="color:#a8a29e">本期从 {len(items)} 条候选中编辑选出；数量不是目标，读完有用才是。</p></div>
</div></body></html>"""
    return f"每日 AI 日报｜{edition}｜{now:%m月%d日 %H:%M}", body


def parse_now(value):
    if not value:
        return datetime.now(BEIJING)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=BEIJING)).astimezone(BEIJING)


def edition_values(now, requested):
    slug = requested if requested in ("morning", "evening") else ("morning" if now.hour < 18 else "evening")
    return slug, "上午篇" if slug == "morning" else "下午篇"


def marker_is_sent(marker_file, marker_key):
    if marker_file.exists():
        try:
            if json.loads(marker_file.read_text(encoding="utf-8")).get("status") == "sent":
                return True
        except json.JSONDecodeError:
            pass
    # A rerun uses the old commit checkout, so always consult the current main
    # branch as well. This closes the most common duplicate-send hole.
    remote = ("https://raw.githubusercontent.com/LeftSeineM/TrendingAI/main/"
              f"data/sent_markers/{marker_key}.json")
    try:
        return json.loads(fetch(remote, "application/json", timeout=10).decode("utf-8")).get("status") == "sent"
    except Exception:
        return False


def cloud_marker(marker_key, payload=None, sha=None):
    """Read or atomically create/update a marker through GitHub Contents API."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "LeftSeineM/TrendingAI")
    if not token:
        return None
    path = f"data/sent_markers/{marker_key}.json"
    url = f"https://api.github.com/repos/{repository}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "TrendingAI-Digest/3.0"}
    if payload is None:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
            return {"status": content.get("status"), "sha": data.get("sha")}
        except Exception:
            return None
    body = {"message": f"chore: {payload['status']} digest {marker_key}",
            "content": base64.b64encode((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()).decode(),
            "branch": "main"}
    if sha:
        body["sha"] = sha
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**headers, "Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    return {"status": payload["status"], "sha": result.get("content", {}).get("sha")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", choices=("current", "morning", "evening"), default="current")
    parser.add_argument("--now", default="")
    parser.add_argument("--dry-run", action="store_true", help="Build email and Pages HTML without SMTP or markers")
    parser.add_argument("--output", default="outputs/email-preview.html")
    args = parser.parse_args()
    now = parse_now(args.now)
    edition_slug, edition_label = edition_values(now, args.edition)
    marker_key = f"{now:%Y-%m-%d}-{edition_slug}"
    marker_file = MARKER_PATH / f"{marker_key}.json"
    if not args.dry_run and marker_is_sent(marker_file, marker_key):
        print(f"SKIP_ALREADY_SENT marker={marker_key}")
        return
    collected, errors = collect()
    # The web edition is intentionally complete for the three-day window. The
    # email edition separately applies send history so morning content is not
    # repeated in the afternoon.
    web_items = deduplicate([dict(item) for item in collected])
    items, history = filter_history(collected)
    # First pass performs optional AI editing. The static site then provides
    # stable per-item anchors used by the final email pass.
    render(web_items, errors, now=now, enrich=True, edition_override=edition_label)
    from digest_site import render_site
    base_url = os.environ.get("PAGES_BASE_URL", "https://leftseinem.github.io/TrendingAI/daily")
    funcs = {"editorial_fields": editorial_fields, "editorial_type": editorial_type,
             "item_tags": item_tags, "display_time": display_time}
    page_url, detail_urls = render_site(web_items, errors, now, edition_slug, edition_label,
                                        DOCS_PATH, base_url, funcs)
    enriched = {item["url"]: item for item in web_items}
    for item in items:
        source = enriched.get(item["url"], {})
        for key, value in source.items():
            if key.startswith("ai_"):
                item[key] = value
        item["detail_url"] = detail_urls.get(item["url"], page_url)
    subject, body = render(items, errors, now=now, enrich=False, page_url=page_url,
                           edition_override=edition_label)
    output = ROOT_PATH / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    if args.dry_run:
        print(f"DRY_RUN_OK items={len(items)} page={page_url} email={output}")
        return

    sender = os.environ["QQ_EMAIL"].strip()
    auth_code = os.environ["QQ_SMTP_AUTH_CODE"].strip()
    configured = os.environ.get("DIGEST_RECIPIENTS") or os.environ.get("DIGEST_RECIPIENT", sender)
    recipients = list(dict.fromkeys(address.strip() for address in configured.split(",") if address.strip()))
    if not recipients:
        raise RuntimeError("DIGEST_RECIPIENTS 为空")
    if marker_is_sent(marker_file, marker_key):
        print(f"SKIP_ALREADY_SENT marker={marker_key} phase=pre-smtp")
        return
    lock = None
    if os.environ.get("GITHUB_TOKEN"):
        existing = cloud_marker(marker_key)
        if existing and existing.get("status") in ("sending", "sent"):
            print(f"SKIP_MARKER_STATE marker={marker_key} status={existing['status']}")
            return
        lock_payload = {"marker": marker_key, "status": "sending",
                        "started_at": datetime.now(UTC).isoformat(),
                        "run_id": os.environ.get("GITHUB_RUN_ID", "")}
        try:
            lock = cloud_marker(marker_key, lock_payload)
        except Exception as exc:
            raise RuntimeError(f"无法取得云端发送锁，已停止发送：{exc}") from exc
    with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(sender, auth_code)
        message = EmailMessage()
        message["Subject"], message["From"], message["To"] = subject, sender, sender
        message["Bcc"] = ", ".join(recipients)
        message.set_content(f"请使用支持 HTML 的邮件客户端查看每日 AI 日报。完整深度版：{page_url}")
        message.add_alternative(body, subtype="html")
        smtp.send_message(message)
    # A successful marker is written only after the single BCC SMTP transaction
    # returns successfully. Scheduled, manual and fallback runs all check it.
    success_marker = {
        "marker": marker_key, "status": "sent", "sent_at": datetime.now(UTC).isoformat(),
        "edition": edition_slug, "recipient_count": len(recipients), "page_url": page_url,
    }
    if lock:
        cloud_marker(marker_key, success_marker, sha=lock.get("sha"))
    else:
        MARKER_PATH.mkdir(parents=True, exist_ok=True)
        marker_file.write_text(json.dumps(success_marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_history(items, history)
    print(f"SENT marker={marker_key} items={len(items)} recipients={len(recipients)} page={page_url}")


if __name__ == "__main__":
    main()
