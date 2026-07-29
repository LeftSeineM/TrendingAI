#!/usr/bin/env python3
"""Send an editorial TrendingAI digest with GitHub, HN and Product Hunt items."""

import html
import hashlib
import json
import os
import re
import smtplib
import ssl
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
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


def fetch(url, accept="text/html,application/json,application/xml"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as response:
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


def editorial_feeds():
    """Collect a 14-day pool from official, edited-media and trusted-author feeds."""
    now = datetime.now(ZoneInfo("UTC"))
    result, errors = [], []
    for source, (url, source_class, authority) in EDITORIAL_FEEDS.items():
        try:
            root = ET.fromstring(fetch(url, "application/rss+xml,application/atom+xml,text/xml"))
            entries = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] in ("item", "entry")]
            for entry in entries[:20]:
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
                if published_at and now - published_at.astimezone(ZoneInfo("UTC")) > timedelta(days=14):
                    continue
                result.append({
                    "source": source,
                    "source_class": source_class,
                    "title": title,
                    "url": url_value,
                    "summary": summary[:1200] or "原始来源未提供摘要，请打开原文查看。",
                    "score": authority,
                    "created_at": published,
                })
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")
    return result, errors


def normalized_title(title):
    text = re.sub(r"https?://\S+|[\W_]+", "", title.lower())
    for word in ("重磅", "突发", "最新", "官宣", "发布", "正式", "深度", "独家"):
        text = text.replace(word, "")
    return text[:160]


def canonical_url(url):
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
        if not title_key or url_key in seen_urls:
            continue
        if any(SequenceMatcher(None, title_key, old).ratio() >= 0.78 for old in seen_titles):
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
    cutoff = datetime.now(ZoneInfo("UTC")) - timedelta(days=30)
    active = []
    for row in history:
        sent_at = parse_feed_time(row.get("sent_at", ""))
        if sent_at and sent_at.astimezone(ZoneInfo("UTC")) >= cutoff:
            active.append(row)
    seen_urls = {row.get("url_hash") for row in active}
    seen_titles = [row.get("title_key", "") for row in active if row.get("title_key")]
    fresh = []
    for item in deduplicate(items):
        url_hash = hashlib.sha256(canonical_url(item["url"]).encode()).hexdigest()[:20]
        title_key = normalized_title(item["title"])
        if url_hash in seen_urls:
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
    return list(dict.fromkeys(tags or ["AI"]))[:3]


def tag_html(item):
    return "".join(
        f'<span style="display:inline-block;margin:0 5px 5px 0;padding:3px 8px;'
        f'border-radius:999px;background:#e0e7ff;color:#3730a3;font-size:12px">{html.escape(tag)}</span>'
        for tag in item_tags(item)
    )


def back_to_toc():
    return '<a href="#toc" style="font-size:12px;color:#64748b;text-decoration:none">↑ 返回目录</a>'


def builder_card(item):
    said = item.get("ai_first_value") or translate_zh(item["summary"])[:700]
    text = (item["summary"] + " " + item["title"]).lower()
    if any(word in text for word in ("product", "build", "startup", "launch", "用户", "产品")):
        why = "AI 产品正在从概念进入真实用户和商业验证阶段，这条内容能帮助判断需求、定位或发布方式。"
    elif any(word in text for word in ("code", "codex", "developer", "agent", "workflow")):
        why = "开发工具和工作流正在快速变化，一线建设者的实际反馈比单纯的功能宣传更有参考价值。"
    else:
        why = "这条内容补充了产品新闻背后的行业判断，有助于区分短期热点与长期变化。"
    repeat = '<div style="margin-top:6px;color:#b45309;font-weight:600">上午已收录</div>' if item.get("morning_repeat") else ""
    return (
        '<div style="padding:15px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;margin:10px 0">'
        f'<div style="font-size:16px;font-weight:700">{html.escape(item["title"])} '
        f'<span style="font-size:12px;color:#7c3aed">· {html.escape(item["kind"])}</span></div>'
        f'<div style="margin-top:5px;color:#64748b;font-size:12px">{html.escape(display_time(item))}</div>'
        f'<div style="margin-top:7px">{tag_html(item)}</div>'
        f'<div style="margin-top:8px;line-height:1.65"><b>{html.escape(item.get("ai_first_label", "核心观点"))}：</b>{html.escape(said)}</div>'
        f'<div style="margin-top:8px;line-height:1.65"><b>{html.escape(item.get("ai_second_label", "为什么现在值得注意"))}：</b>{html.escape(item.get("ai_second_value", why))}</div>'
        f'{repeat}<div style="margin-top:9px"><a href="{html.escape(item["url"], quote=True)}" '
        'style="color:#2563eb">查看原始内容 →</a></div></div>'
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
        return ("AI 编辑", item["ai_first_label"], item["ai_first_value"],
                item["ai_second_label"], item["ai_second_value"])
    kind = editorial_type(item)
    summary = translate_zh(item["summary"][:650] or "原始来源暂未提供简介，建议打开链接查看完整说明。")
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
        '<div style="padding:15px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;margin:10px 0">'
        f'<div style="font-size:16px;font-weight:700">{prefix}<a style="color:#2563eb;text-decoration:none" '
        f'href="{html.escape(item["url"], quote=True)}">{html.escape(item["title"])}</a></div>'
        f'<div style="margin-top:5px;color:#6b7280;font-size:12px">{html.escape(item["source"])} · {kind}</div>'
        f'<div style="margin-top:4px;color:#64748b;font-size:12px">{html.escape(display_time(item))}</div>'
        f'<div style="margin-top:7px">{tag_html(item)}</div>'
        f'<div style="margin-top:7px;color:#374151;line-height:1.65"><b>{first_label}：</b>{html.escape(first_value)}</div>'
        f'<div style="margin-top:7px;color:#374151;line-height:1.65"><b>{second_label}：</b>{html.escape(second_value)}</div>'
        "</div>"
    )


def compact_link(item):
    summary = item.get("ai_first_value") or translate_zh(item.get("summary", ""))[:160]
    return (
        '<div style="padding:10px 0;border-bottom:1px solid #e5e7eb;line-height:1.5">'
        f'{tag_html(item)}<br><a href="{html.escape(item["url"], quote=True)}" '
        f'style="color:#2563eb;text-decoration:none;font-weight:600">{html.escape(item["title"])}</a>'
        f'<div style="margin-top:3px;color:#64748b;font-size:12px">{html.escape(display_time(item))}</div>'
        f'<div style="margin-top:5px;color:#475569">{html.escape(summary)}</div></div>'
    )


def ai_enrich(items):
    """Optionally edit selected items with an OpenAI-compatible chat API."""
    api_key = os.environ.get("AI_API_KEY", "").strip()
    base_url = os.environ.get("AI_BASE_URL", "").strip().rstrip("/")
    model = os.environ.get("AI_MODEL", "").strip()
    if not (api_key and base_url and model and items):
        return
    payload_items = [{
        "id": index,
        "source": item["source"],
        "title": item["title"],
        "summary": item["summary"][:900],
        "kind": item.get("kind", ""),
    } for index, item in enumerate(items)]
    prompt = """你是每日 AI 日报的中文编辑。只根据输入内容编辑，不补充无法核验的事实。
为每条内容返回：
1. tags：1～3 个明确短 Tag；
2. first_label、first_value；
3. second_label、second_value。
产品使用“能干什么 / 使用门槛”；新闻使用“发生了什么 / 有何影响”；
人物观点使用“核心观点 / 为什么现在值得注意”；开源项目使用“解决什么问题 / 是否值得尝试”；
研究或模型使用“能力变化 / 普通人会受到什么影响”。
写法接近专业科技媒体简报：先讲清事实，再给必要背景和判断，避免宣传腔、空话和机械套模板。
语言自然、具体、简短，英文信息翻译为中文。只返回 {"items": [...]} JSON 对象，每项保留输入 id。输入如下：
""" + json.dumps(payload_items, ensure_ascii=False)
    request_body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=request_body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        parsed = json.loads(content)
        rows = parsed.get("items", parsed) if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            return
        for row in rows:
            index = row.get("id") if isinstance(row, dict) else None
            if not isinstance(index, int) or not 0 <= index < len(items):
                continue
            tags = row.get("tags")
            if isinstance(tags, list):
                items[index]["ai_tags"] = [clean(str(tag))[:16] for tag in tags[:3] if clean(str(tag))]
            for key in ("first_label", "first_value", "second_label", "second_value"):
                value = clean(str(row.get(key, "")))
                if value:
                    items[index][f"ai_{key}"] = value[:700]
    except Exception:
        # AI editing is optional. Any API/configuration/format failure falls back
        # to deterministic editorial rules so the scheduled email still sends.
        return


def render(items, errors):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    authority = [x for x in items if x.get("source_class") in ("官方", "专业媒体", "个人作者")]
    authority.sort(key=lambda x: (x.get("score", 0), parse_time(x.get("created_at"))), reverse=True)
    highlights, source_counts = [], {}
    for item in authority:
        if source_counts.get(item["source"], 0) >= 1:
            continue
        highlights.append(item)
        source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1
        if len(highlights) == 4:
            break
    if len(highlights) < 4:
        highlights += [x for x in authority if x not in highlights][:4 - len(highlights)]
    highlight_urls = {x["url"] for x in highlights}
    authoritative_more, source_counts = [], {}
    for item in authority:
        if item["url"] in highlight_urls or source_counts.get(item["source"], 0) >= 2:
            continue
        authoritative_more.append(item)
        source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1
        if len(authoritative_more) == 5:
            break
    used_urls = highlight_urls | {x["url"] for x in authoritative_more}
    indie = [x for x in items if x["source"] == "独立开发者新品"][:3]
    used_urls |= {x["url"] for x in indie}
    builders = [x for x in select_builder_items(items, now) if x["url"] not in used_urls][:3]
    used_urls |= {x["url"] for x in builders}
    discovered = [x for x in items if x["url"] not in used_urls and not x.get("source_class")
                  and x["source"] not in ("独立开发者新品", "AI 人物与观点")]
    tech = sorted(discovered, key=rank, reverse=True)[:4]
    used_urls |= {x["url"] for x in tech}
    original_sources = ("GitHub Trending", "Hacker News", "Product Hunt", "关注项目 · Scrapling")
    trend_latest = sorted(
        [x for x in items if x["source"] in original_sources and x["url"] not in used_urls],
        key=lambda x: (parse_time(x.get("created_at")), x.get("score", 0)), reverse=True,
    )[:5]
    used_urls |= {x["url"] for x in trend_latest}
    more = sorted([x for x in items if x["url"] not in used_urls
                   and x["source"] != "独立开发者新品"
                   and x["source"] not in original_sources and
                   (x["source"] != "AI 人物与观点" or len(x.get("summary", "")) >= 120)],
                  key=lambda x: (x.get("score", 0), rank(x)), reverse=True)[:8]
    selected = highlights + authoritative_more + indie + builders + tech
    ai_enrich(selected + more)
    warning = '<p style="color:#64748b">本次所有来源均正常。</p>'
    if errors:
        warning = '<p style="background:#fff7ed;padding:10px">部分来源暂时不可用：' + html.escape("；".join(errors)) + "</p>"
    body = f"""<!doctype html><html><body style="margin:0;background:#f3f4f6;font-family:Arial,'Microsoft YaHei',sans-serif">
<div style="max-width:760px;margin:auto;background:white;padding:26px">
<a id="top" name="top"></a><h1>每日 AI 日报</h1>
<p style="color:#6b7280">{now:%Y-%m-%d %H:%M}（北京时间）· 共 {len(items)} 条</p>
<p style="color:#475569;line-height:1.7">从最近 14 天的官方发布、专业媒体、优质作者和实时榜单中筛选，并与近 30 天发送记录去重。</p>
<div id="toc" style="padding:15px 18px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;margin:16px 0">
<b>目录</b>
<div style="margin-top:9px;line-height:2">
<a href="#highlights" style="color:#2563eb">一、本期看点</a><br>
<a href="#authority" style="color:#2563eb">二、权威资讯与行业变化</a><br>
<a href="#voices" style="color:#2563eb">三、人物与观点</a><br>
<a href="#tech" style="color:#2563eb">四、开源、模型与研究</a><br>
<a href="#more" style="color:#2563eb">五、更多资讯</a><br>
<a href="#apps" style="color:#2563eb">六、今日独立开发者新品</a><br>
<a href="#trends" style="color:#2563eb">七、原始趋势源·今日速览</a>
</div></div>
<a id="highlights" name="highlights"></a>
<div style="padding:18px;background:#fff7ed;border-radius:10px;margin-bottom:18px">
<h2 style="color:#9a3412">一、本期看点</h2>{back_to_toc()}
<p style="color:#475569;line-height:1.6">优先采用官方原文和专业编辑来源，把同一事件合并后讲清楚。</p>
{''.join(editorial_card(x, i) for i, x in enumerate(highlights, 1)) if highlights else '<p>本期暂未发现足够重要的新内容。</p>'}
</div>
<a id="authority" name="authority"></a>
<div style="padding:18px;background:#f8fafc;border-radius:10px;margin-bottom:18px">
<h2 style="color:#334155">二、权威资讯与行业变化</h2>{back_to_toc()}
{''.join(editorial_card(x, i) for i, x in enumerate(authoritative_more, 1)) if authoritative_more else '<p>本期暂无补充。</p>'}
</div>
<a id="voices" name="voices"></a>
<div style="padding:18px;background:#f5f3ff;border-radius:10px;margin-bottom:18px">
<h2 style="color:#6d28d9">三、人物与观点</h2>{back_to_toc()}
<p style="color:#475569;line-height:1.6">只保留有完整论点、实际经验或明确判断的内容。</p>
{''.join(builder_card(x) for x in builders) if builders else '<p>本次中央 Feed 暂无足够有信息量的新内容。</p>'}
</div>
<a id="tech" name="tech"></a>
<div style="padding:18px;background:#eff6ff;border-radius:10px">
<h2 style="color:#1d4ed8">四、开源、模型与研究</h2>{back_to_toc()}
<p style="color:#475569;line-height:1.6">榜单只负责发现线索，优先保留真正解决问题或带来能力变化的项目。</p>
{''.join(editorial_card(x, i) for i, x in enumerate(tech, 1))}
</div>
<a id="more" name="more"></a><h1>五、更多资讯</h1>{back_to_toc()}
<p style="color:#64748b">每条保留一句话摘要，不再只列标题。</p>
{''.join(compact_link(x) for x in more)}
<a id="apps" name="apps"></a>
<div style="padding:18px;background:#f0fdf4;border-radius:10px;margin:20px 0">
<h2 style="color:#166534">六、今日独立开发者新品</h2>{back_to_toc()}
<p style="color:#475569;line-height:1.6">保留 Chinese Independent Developer，每期只展示当天或最近一批的 3 个最新应用。</p>
{''.join(editorial_card(x, i) for i, x in enumerate(indie, 1)) if indie else '<p>今天暂未抓到新的应用。</p>'}
</div>
<a id="trends" name="trends"></a>
<div style="padding:18px;background:#f8fafc;border-radius:10px;margin-bottom:18px">
<h2 style="color:#334155">七、原始趋势源·今日速览</h2>{back_to_toc()}
<p style="color:#64748b">保留最早的 TrendingAI 发现方式，只放 GitHub Trending、Hacker News、Product Hunt 和关注项目中当天最新的少量内容。</p>
{''.join(compact_link(x) for x in trend_latest) if trend_latest else '<p>本次没有未收录的今日趋势内容。</p>'}
</div>
<h2 style="margin-top:24px">来源状态</h2>{warning}
<p style="color:#9ca3af;font-size:12px">每日 AI 日报自动整理。</p>
</div></body></html>"""
    return f"每日 AI 日报｜{now:%m月%d日 %H:%M}", body


def main():
    sender = os.environ["QQ_EMAIL"].strip()
    auth_code = os.environ["QQ_SMTP_AUTH_CODE"].strip()
    configured = os.environ.get("DIGEST_RECIPIENTS") or os.environ.get("DIGEST_RECIPIENT", sender)
    recipients = [address.strip() for address in configured.split(",") if address.strip()]
    recipients = list(dict.fromkeys(recipients))
    items, errors = collect()
    items, history = filter_history(items)
    subject, body = render(items, errors)
    with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(sender, auth_code)
        for recipient in recipients:
            message = EmailMessage()
            message["Subject"], message["From"], message["To"] = subject, sender, recipient
            message.set_content("请使用支持 HTML 的邮件客户端查看每日 AI 日报。")
            message.add_alternative(body, subtype="html")
            smtp.send_message(message)
    save_history(items, history)
    print(f"Sent {len(items)} items to {len(recipients)} recipients: {subject}")


if __name__ == "__main__":
    main()
