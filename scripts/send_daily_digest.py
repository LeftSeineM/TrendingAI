#!/usr/bin/env python3
"""Send an editorial TrendingAI digest with GitHub, HN and Product Hunt items."""

import html
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


def select_builder_items(items, now):
    candidates = [item for item in items if item["source"] == "AI 人物与观点"]
    useful_words = AI_WORDS + ("product", "build", "startup", "codex", "claude",
                               "openai", "anthropic", "tool", "workflow")
    candidates = [
        item for item in candidates
        if any(word in (item["summary"] + " " + item["title"]).lower() for word in useful_words)
        or item["kind"] in ("播客", "官方博客")
    ]
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
    return (new_items + morning_items)[:5]


def builder_card(item):
    said = translate_zh(item["summary"])
    text = (item["summary"] + " " + item["title"]).lower()
    if any(word in text for word in ("product", "build", "startup", "launch", "用户", "产品")):
        why = "它反映了一线建设者如何把 AI 变成真实产品，而不是停留在概念讨论。"
        impact = "普通用户可以发现新工具；产品经理和创业者可以参考需求、定位与发布方式。"
    elif any(word in text for word in ("code", "codex", "developer", "agent", "workflow")):
        why = "它来自正在实际构建 AI 工具的人，能帮助判断开发方式和工作流正在怎样变化。"
        impact = "开发者可用于改进工具链；产品经理和创业者可据此评估 AI 自动化的实际边界。"
    else:
        why = "这条内容有明确观点或较高讨论度，能够补充产品新闻背后的行业判断。"
        impact = "可以把它当作决策参考，而不是简单追逐热点。"
    repeat = '<div style="margin-top:6px;color:#b45309;font-weight:600">上午已收录</div>' if item.get("morning_repeat") else ""
    return (
        '<div style="padding:15px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;margin:10px 0">'
        f'<div style="font-size:16px;font-weight:700">{html.escape(item["title"])} '
        f'<span style="font-size:12px;color:#7c3aed">· {html.escape(item["kind"])}</span></div>'
        f'<div style="margin-top:8px;line-height:1.65"><b>最近说了什么：</b>{html.escape(said[:700])}</div>'
        f'<div style="margin-top:8px;line-height:1.65"><b>为什么值得关注：</b>{html.escape(why)}</div>'
        f'<div style="margin-top:8px;line-height:1.65"><b>实际意义：</b>{html.escape(impact)}</div>'
        f'{repeat}<div style="margin-top:9px"><a href="{html.escape(item["url"], quote=True)}" '
        'style="color:#2563eb">查看原始内容 →</a></div></div>'
    )


def explain(item, featured=False):
    text = (item["title"] + " " + item["summary"]).lower()
    topic = "新技术与产品"
    insight = ""
    audience = "喜欢发现新工具、关注技术趋势的人"
    for words, candidate_topic, candidate_insight, candidate_audience in TOPICS:
        if any(word in text for word in words):
            topic = candidate_topic
            insight = candidate_insight
            audience = candidate_audience
            break
    if item["source"] == "独立开发者新品":
        topic = "可直接使用的新应用"
        insight = "这是中国独立开发者近期新增或上线的产品，优先看它解决的具体问题，以及是否支持你的设备和使用场景。"
        audience = "想发现实用新应用、效率工具和有趣产品的人"
    elif item["source"] == "关注项目 · Scrapling":
        topic = "网页采集工具更新"
        insight = "Scrapling 用于抓取普通或动态网页，并尽量适应网页结构变化；这段内容说明它最近发布了什么新变化。"
        audience = "需要监测网页、收集公开资料或为 AI 提供网页数据的开发者"
    elif not insight:
        if item["source"] == "GitHub Trending":
            insight = "这是今天增长较快的开源项目。建议先看 README、最近提交和 Issue，再判断它是短期热度还是值得长期采用。"
        elif item["source"] == "Hacker News":
            insight = "它正在技术社区引发讨论。除了文章本身，也值得打开评论区看看开发者提出的反例、质疑和补充资料。"
        else:
            insight = "这是近期发布的新产品。可以重点比较它解决的问题、目标用户，以及免费版是否足够实际使用。"
    if item["score"]:
        insight += f" 当前热度信号约为 {item['score']}，说明它已经获得一定关注。"
    reason = ""
    if featured:
        hits = [word.upper() if len(word) <= 4 else word for word in AI_WORDS if word in text]
        if hits:
            reason = "内容与 " + "、".join(hits[:3]) + " 直接相关，同时在当天榜单中有较高热度。"
        else:
            reason = "在当天资讯中热度和讨论度较高，适合优先快速了解。"
    return topic, insight, audience, reason


def card(item, number=None, featured=False):
    prefix = f"{number}. " if number else ""
    topic, insight, audience, reason = explain(item, featured)
    reason_html = (
        f'<div style="margin-top:8px;color:#1d4ed8;font-weight:600">✨ 入选理由：{html.escape(reason)}</div>'
        if reason else ""
    )
    return (
        '<div style="padding:16px 0;border-bottom:1px solid #e5e7eb">'
        f'<div style="font-size:16px;font-weight:700">{prefix}<a style="color:#2563eb;text-decoration:none" '
        f'href="{html.escape(item["url"], quote=True)}">{html.escape(item["title"])}</a></div>'
        f'<div style="margin-top:5px;color:#6b7280;font-size:12px">{html.escape(item["source"])} · {html.escape(topic)}</div>'
        f'<div style="margin-top:8px;color:#374151;line-height:1.6"><b>它是什么：</b>'
        f'{html.escape(item["summary"][:420] or "原始来源暂未提供简介，建议打开链接查看项目演示与说明。")}</div>'
        f'<div style="margin-top:8px;color:#374151;line-height:1.6"><b>为什么值得看：</b>{html.escape(insight)}</div>'
        f'<div style="margin-top:8px;color:#374151;line-height:1.6"><b>适合谁：</b>{html.escape(audience)}</div>'
        f'{reason_html}'
        "</div>"
    )


def render(items, errors):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    indie = [x for x in items if x["source"] == "独立开发者新品"]
    builders = select_builder_items(items, now)
    discovered = [x for x in items if x["source"] not in
                  ("独立开发者新品", "AI 人物与观点")]
    top10 = sorted(discovered, key=rank, reverse=True)[:10]
    top_urls = {item["url"] for item in top10}
    all_sections = []
    for source in ("GitHub Trending", "Hacker News", "Product Hunt"):
        subset = [x for x in items if x["source"] == source and x["url"] not in top_urls]
        all_sections.append(f"<h2>{source}（{len(subset)}）</h2>")
        all_sections.extend(card(x) for x in subset)
    warning = ""
    if errors:
        warning = '<p style="background:#fff7ed;padding:10px">部分来源获取失败：' + html.escape("；".join(errors)) + "</p>"
    body = f"""<!doctype html><html><body style="margin:0;background:#f3f4f6;font-family:Arial,'Microsoft YaHei',sans-serif">
<div style="max-width:760px;margin:auto;background:white;padding:26px">
<h1>每日 AI 日报</h1>
<p style="color:#6b7280">{now:%Y-%m-%d %H:%M}（北京时间）· 共 {len(items)} 条</p>
<p style="color:#475569;line-height:1.7">先看近期实用应用与重点关注项目，再看 AI 精选和技术社区资讯。每条内容都附有简短导读。</p>
{warning}
<div style="padding:18px;background:#f0fdf4;border-radius:10px;margin-bottom:18px">
<h2 style="color:#166534">一、今日实用新应用</h2>
<p style="color:#475569;line-height:1.6">来自 Chinese Independent Developer 主榜，优先展示普通用户可以直接使用的新应用。</p>
{''.join(card(x, i) for i, x in enumerate(indie, 1)) if indie else '<p>今天暂未抓到新的主榜应用。</p>'}
</div>
<div style="padding:18px;background:#f5f3ff;border-radius:10px;margin-bottom:18px">
<h2 style="color:#6d28d9">二、AI 人物与观点</h2>
<p style="color:#475569;line-height:1.6">来自 follow-builders 的公开中央 Feed，精选 AI Builder 的 X 动态、播客与官方博客。英文内容自动转为通俗中文。</p>
{''.join(builder_card(x) for x in builders) if builders else '<p>本次中央 Feed 暂无足够有信息量的新内容。</p>'}
</div>
<div style="padding:18px;background:#eff6ff;border-radius:10px">
<h2 style="color:#1d4ed8">三、AI 精选 Top 10</h2>
<p style="color:#475569;line-height:1.6">从原有资讯源中综合 AI 相关性、实用性与当天热度筛选，并说明每条的入选理由。</p>
{''.join(card(x, i, True) for i, x in enumerate(top10, 1))}
</div>
<h1>四、更多技术资讯与导读</h1>{''.join(all_sections)}
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
    subject, body = render(items, errors)
    with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(sender, auth_code)
        for recipient in recipients:
            message = EmailMessage()
            message["Subject"], message["From"], message["To"] = subject, sender, recipient
            message.set_content("请使用支持 HTML 的邮件客户端查看每日 AI 日报。")
            message.add_alternative(body, subtype="html")
            smtp.send_message(message)
    print(f"Sent {len(items)} items to {len(recipients)} recipients: {subject}")


if __name__ == "__main__":
    main()
