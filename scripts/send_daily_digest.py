#!/usr/bin/env python3
"""Send a TrendingAI-style digest with GitHub, HN and Product Hunt items."""

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

UA = "Mozilla/5.0 (compatible; TrendingAI-Digest/1.0)"
AI_WORDS = ("ai", "agent", "llm", "gpt", "model", "machine learning",
            "transformer", "diffusion", "inference", "rag", "copilot",
            "multimodal", "人工智能", "大模型", "机器人")


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
        "summary": f"{hit.get('num_comments', 0)} 条评论",
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


def card(item, number=None):
    prefix = f"{number}. " if number else ""
    return (
        '<div style="padding:14px 0;border-bottom:1px solid #e5e7eb">'
        f'<b>{prefix}<a style="color:#2563eb;text-decoration:none" href="{html.escape(item["url"], quote=True)}">'
        f'{html.escape(item["title"])}</a></b>'
        f'<div style="margin-top:4px;color:#6b7280;font-size:12px">{html.escape(item["source"])}</div>'
        f'<div style="margin-top:6px;color:#374151;line-height:1.55">{html.escape(item["summary"][:360] or "暂无摘要")}</div>'
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
<h1>TrendingAI 每日资讯</h1><p style="color:#6b7280">{now:%Y-%m-%d %H:%M}（北京时间）· 共 {len(items)} 条</p>
{warning}
<div style="padding:18px;background:#eff6ff;border-radius:10px"><h2 style="color:#1d4ed8">AI 精选 Top 10</h2>
{''.join(card(x, i) for i, x in enumerate(top10, 1))}</div>
<h1>全部资讯</h1>{''.join(all_sections)}
<p style="color:#9ca3af;font-size:12px">由 LeftSeineM/TrendingAI 自动整理。</p>
</div></body></html>"""
    return f"TrendingAI｜AI 精选 Top 10 + 全部资讯｜{now:%m月%d日 %H:%M}", body


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
