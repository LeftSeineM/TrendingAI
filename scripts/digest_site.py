#!/usr/bin/env python3
"""Render the complete three-day digest as a static GitHub Pages site."""

import hashlib
import html
import json
import re
from pathlib import Path


SECTION_NAMES = (
    "今日重点", "权威媒体", "官方发布", "AI 产品与实用应用", "AI 人物与观点",
    "大模型与研究", "开源项目与开发工具", "产业、商业与创业",
    "具身智能与机器人", "更多资讯", "独立开发者新品", "来源运行状态",
)


def anchor(item):
    return "item-" + hashlib.sha1(item["url"].encode("utf-8")).hexdigest()[:12]


def section_for(item, editorial_type):
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    if item.get("source") == "独立开发者新品":
        return "独立开发者新品"
    if item.get("source") == "AI 人物与观点":
        return "AI 人物与观点"
    if item.get("source_class") == "官方":
        return "官方发布"
    if item.get("source_class") == "专业媒体":
        return "权威媒体"
    if any(word in text for word in ("robot", "robotics", "具身", "机器人", "vla")):
        return "具身智能与机器人"
    if editorial_type(item) == "research":
        return "大模型与研究"
    if item.get("source") in ("GitHub Trending", "关注项目 · Scrapling"):
        return "开源项目与开发工具"
    if item.get("source") == "Product Hunt" or editorial_type(item) == "product":
        return "AI 产品与实用应用"
    if any(word in text for word in ("startup", "business", "funding", "融资", "创业", "商业", "产业")):
        return "产业、商业与创业"
    if item.get("source_class") == "个人作者":
        return "AI 人物与观点"
    return "更多资讯"


def card(item, funcs):
    kind, first_label, first_value, second_label, second_value = funcs["editorial_fields"](item)
    tags = funcs["item_tags"](item)
    related = item.get("related_links", [])
    links = [f'<a href="{html.escape(item["url"], quote=True)}" target="_blank">阅读原文</a>']
    links += [f'<a href="{html.escape(link["url"], quote=True)}" target="_blank">{html.escape(link["source"])}原文</a>' for link in related]
    search = " ".join([item.get("title", ""), item.get("source", ""), *tags, first_value, second_value]).lower()
    return f'''<article id="{anchor(item)}" class="card" data-source="{html.escape(item.get("source", ""), quote=True)}" data-tags="{html.escape("|".join(tags), quote=True)}" data-search="{html.escape(search, quote=True)}">
<div class="meta">{html.escape(item.get("source", ""))} · {html.escape(funcs["display_time"](item))}</div>
<h3>{html.escape(item.get("title", ""))}</h3>
<div class="tags">{''.join(f'<button type="button" class="tag" data-tag="{html.escape(tag, quote=True)}">{html.escape(tag)}</button>' for tag in tags)}</div>
<p><b>{html.escape(first_label)}：</b>{html.escape(first_value)}</p>
<p><b>{html.escape(second_label)}：</b>{html.escape(second_value)}</p>
<div class="links">{' · '.join(links)}</div></article>'''


def render_site(items, errors, now, edition_slug, edition_label, output_dir, base_url, funcs):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_name = f"{now:%Y-%m-%d}-{edition_slug}.html"
    report_url = f"{base_url.rstrip('/')}/{report_name}"
    usable = [item for item in items if item.get("url")]
    ranked = sorted(usable, key=lambda x: (not x.get("weak_summary", False), x.get("score", 0)), reverse=True)
    highlights = [x for x in ranked if not x.get("weak_summary")][:6]
    highlight_urls = {x["url"] for x in highlights}
    groups = {name: [] for name in SECTION_NAMES}
    groups["今日重点"] = highlights
    for item in usable:
        if item["url"] in highlight_urls:
            continue
        groups[section_for(item, funcs["editorial_type"])].append(item)

    archive_path = output_dir / "archive.json"
    try:
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        archive = []
    archive = [row for row in archive if row.get("file") != report_name]
    archive.insert(0, {"date": f"{now:%Y-%m-%d}", "edition": edition_label, "file": report_name})
    archive = archive[:120]
    archive_path.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive_links = "".join(f'<a href="{html.escape(row["file"], quote=True)}">{html.escape(row["date"])} {html.escape(row["edition"])}</a>' for row in archive)
    sources = sorted({x.get("source", "") for x in usable if x.get("source")})
    tags = sorted({tag for x in usable for tag in funcs["item_tags"](x)})
    nav = "".join(f'<a href="#{index + 1}">{index + 1}. {name}</a>' for index, name in enumerate(SECTION_NAMES))
    sections = []
    for index, name in enumerate(SECTION_NAMES[:-1], 1):
        content = "".join(card(item, funcs) for item in groups[name]) or '<p class="empty">本期暂无有效内容。</p>'
        sections.append(f'<section id="{index}"><h2>{index}. {name}</h2>{content}<a class="top" href="#top">返回顶部 ↑</a></section>')
    status = "本次所有来源均正常。" if not errors else "部分来源暂时不可用：" + "；".join(errors)
    sections.append(f'<section id="12"><h2>12. 来源运行状态</h2><p>{html.escape(status)}</p><a class="top" href="#top">返回顶部 ↑</a></section>')
    other_edition = "evening" if edition_slug == "morning" else "morning"
    other_file = f"{now:%Y-%m-%d}-{other_edition}.html"
    other_label = "下午篇" if edition_slug == "morning" else "上午篇"
    other_exists = any(row.get("file") == other_file for row in archive)
    switch_html = (f'<a href="{html.escape(other_file, quote=True)}">切换到{other_label}</a>'
                   if other_exists else f'<span style="color:#637083">{other_label}尚未生成</span>')
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>每日 AI 日报 · {edition_label}</title>
<style>:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#637083;--blue:#275efe}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:920px;margin:auto;padding:22px}}header,.panel,section{{background:#fff;border:1px solid #e5e9f0;border-radius:14px;padding:18px;margin-bottom:14px}}h1{{font-size:28px;margin:0 0 6px}}h2{{font-size:21px}}h3{{font-size:17px;margin:5px 0}}a{{color:var(--blue);text-decoration:none}}nav,.archive{{display:flex;gap:9px;flex-wrap:wrap}}nav a,.archive a{{padding:5px 9px;background:#eef3ff;border-radius:8px}}.filters{{display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px;margin-top:12px}}input,select{{width:100%;padding:9px;border:1px solid #ccd4e0;border-radius:8px;background:white}}.card{{padding:14px 0;border-top:1px solid #edf0f4}}.card:first-of-type{{border-top:0}}.meta{{color:var(--muted);font-size:12px}}.tag{{border:0;border-radius:999px;padding:3px 8px;margin:0 5px 5px 0;color:#334155;background:#e8eefc;cursor:pointer}}.links{{font-weight:700}}.top{{display:block;text-align:right;margin-top:8px}}.empty{{color:var(--muted)}}@media(max-width:650px){{main{{padding:10px}}header,.panel,section{{padding:14px}}.filters{{grid-template-columns:1fr}}h1{{font-size:23px}}}}</style></head>
<body><main id="top"><header><h1>每日 AI 日报 · {edition_label}</h1><p>{now:%Y-%m-%d %H:%M}（北京时间）· 最近三个自然日 · {len(usable)} 条有效资讯</p><p>{switch_html}</p></header>
<div class="panel"><b>目录</b><nav>{nav}</nav><div class="filters"><input id="q" placeholder="搜索标题、摘要、来源或 Tag"><select id="source"><option value="">全部来源</option>{''.join(f'<option>{html.escape(x)}</option>' for x in sources)}</select><select id="tagFilter"><option value="">全部 Tag</option>{''.join(f'<option>{html.escape(x)}</option>' for x in tags)}</select></div></div>
{''.join(sections)}<section><h2>历史日报归档</h2><div class="archive">{archive_links}</div></section>
<script>const cards=[...document.querySelectorAll('.card')],q=document.querySelector('#q'),source=document.querySelector('#source'),tag=document.querySelector('#tagFilter');function apply(){{const s=q.value.trim().toLowerCase();cards.forEach(c=>c.hidden=!!((s&&!c.dataset.search.includes(s))||(source.value&&c.dataset.source!==source.value)||(tag.value&&!c.dataset.tags.split('|').includes(tag.value))))}}q.addEventListener('input',apply);source.addEventListener('change',apply);tag.addEventListener('change',apply);document.querySelectorAll('.tag').forEach(b=>b.onclick=()=>{{tag.value=b.dataset.tag;apply();scrollTo({{top:0,behavior:'smooth'}})}});</script></main></body></html>'''
    (output_dir / report_name).write_text(page, encoding="utf-8")
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    return report_url, {item["url"]: f"{report_url}#{anchor(item)}" for item in usable}
