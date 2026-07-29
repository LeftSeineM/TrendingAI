#!/usr/bin/env python3
"""Send an editorial TrendingAI digest with GitHub, HN and Product Hunt items."""

import html
import json
import os
import re
import smtplib
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

UA = "Mozilla/5.0 (compatible; TrendingAI-Digest/2.0)"
AI_WORDS = ("ai", "agent", "llm", "gpt", "model", "machine learning",
            "transformer", "diffusion", "inference", "rag", "copilot",
            "multimodal", "人工智能", "大模型", "机器人")

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


def collect():
    items, errors = [], []
    for name, loader in (("GitHub Trending", github_trending),
                         ("Hacker News", hacker_news),
                         ("Product Hunt", product_hunt)):
        try:
            items.extend(loader())
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    if not items:
        raise RuntimeError("全部资讯源抓取失败：" + "；".join(errors))
    return items, errors


def rank(item):
    text = (item["title"] + " " + item["summary"]).lower()
    return sum(word in text for word in AI_WORDS), item["score"]


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
    if not insight:
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
    top10 = sorted(items, key=rank, reverse=True)[:10]
    all_sections = []
    for source in ("GitHub Trending", "Hacker News", "Product Hunt"):
        subset = [x for x in items if x["source"] == source]
        all_sections.append(f"<h2>{source}（{len(subset)}）</h2>")
        all_sections.extend(card(x) for x in subset)
    warning = ""
    if errors:
        warning = '<p style="background:#fff7ed;padding:10px">部分来源获取失败：' + html.escape("；".join(errors)) + "</p>"
    body = f"""<!doctype html><html><body style="margin:0;background:#f3f4f6;font-family:Arial,'Microsoft YaHei',sans-serif">
<div style="max-width:760px;margin:auto;background:white;padding:26px">
<h1>TrendingAI 每日资讯</h1>
<p style="color:#6b7280">{now:%Y-%m-%d %H:%M}（北京时间）· 共 {len(items)} 条</p>
<p style="color:#475569;line-height:1.7">这不是一份只有链接的目录：每条资讯都附有简短导读，帮你判断它是什么、有什么看点，以及是否值得继续点开。</p>
{warning}
<div style="padding:18px;background:#eff6ff;border-radius:10px">
<h2 style="color:#1d4ed8">AI 精选 Top 10</h2>
<p style="color:#475569;line-height:1.6">先读这 10 条：综合 AI 相关性与当天热度筛选，并说明每条的入选理由。</p>
{''.join(card(x, i, True) for i, x in enumerate(top10, 1))}
</div>
<h1>全部资讯与导读</h1>{''.join(all_sections)}
<p style="color:#9ca3af;font-size:12px">由 LeftSeineM/TrendingAI 自动整理。</p>
</div></body></html>"""
    return f"TrendingAI｜有解读的 AI Top 10 + 全部资讯｜{now:%m月%d日 %H:%M}", body


def main():
    sender = os.environ["QQ_EMAIL"].strip()
    auth_code = os.environ["QQ_SMTP_AUTH_CODE"].strip()
    recipient = os.environ.get("DIGEST_RECIPIENT", sender).strip()
    items, errors = collect()
    subject, body = render(items, errors)
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, sender, recipient
    message.set_content("请使用支持 HTML 的邮件客户端查看 TrendingAI 每日资讯。")
    message.add_alternative(body, subtype="html")
    with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(sender, auth_code)
        smtp.send_message(message)
    print(f"Sent {len(items)} items: {subject}")


if __name__ == "__main__":
    main()
