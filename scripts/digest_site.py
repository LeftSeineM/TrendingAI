#!/usr/bin/env python3
"""Render the complete digest as a readable edition with a searchable library."""

import hashlib
import html
import json
import re
from pathlib import Path


SECTION_NAMES = (
    "权威媒体", "官方发布", "AI 产品与实用应用", "AI 人物与观点",
    "大模型与研究", "开源项目与开发工具", "产业、商业与创业",
    "具身智能与机器人", "更多资讯", "独立开发者新品",
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


def _fields(item, funcs):
    _, first_label, first_value, second_label, second_value = funcs["editorial_fields"](item)
    return first_label, first_value, second_label, second_value


def _first_sentence(value, limit=110):
    sentence = re.split(r"(?<=[。！？])", value, maxsplit=1)[0].strip()
    return (sentence or value)[:limit]


def feature_card(item, funcs, number=None):
    first_label, first_value, second_label, second_value = _fields(item, funcs)
    tags = funcs["item_tags"](item)
    prefix = f'<span class="number">0{number}</span>' if number else ""
    search = " ".join([item.get("title", ""), item.get("source", ""), *tags, first_value, second_value]).lower()
    return f'''<article id="{anchor(item)}" class="story feature" data-source="{html.escape(item.get("source", ""), quote=True)}" data-tags="{html.escape("|".join(tags), quote=True)}" data-search="{html.escape(search, quote=True)}">
<div class="eyebrow">{prefix}<span>{html.escape(item.get("source", ""))}</span><span>·</span><span>{html.escape(funcs["display_time"](item))}</span></div>
<h3><a href="{html.escape(item["url"], quote=True)}" target="_blank">{html.escape(item.get("title", ""))}</a></h3>
<p class="lead"><b>{html.escape(first_label)}：</b>{html.escape(first_value)}</p>
<p class="context"><b>{html.escape(second_label)}：</b>{html.escape(second_value)}</p>
<div class="story-foot"><div class="tags">{''.join(f'<button type="button" class="tag" data-tag="{html.escape(tag, quote=True)}">{html.escape(tag)}</button>' for tag in tags)}</div><a class="source-link" href="{html.escape(item["url"], quote=True)}" target="_blank">打开原文 ↗</a></div>
</article>'''


def library_card(item, funcs):
    _, first_value, _, second_value = _fields(item, funcs)
    tags = funcs["item_tags"](item)
    search = " ".join([item.get("title", ""), item.get("source", ""), *tags, first_value, second_value]).lower()
    return f'''<article id="{anchor(item)}" class="story library-card" data-source="{html.escape(item.get("source", ""), quote=True)}" data-tags="{html.escape("|".join(tags), quote=True)}" data-search="{html.escape(search, quote=True)}">
<div><div class="meta">{html.escape(item.get("source", ""))} · {html.escape(funcs["display_time"](item))}</div><h3><a href="{html.escape(item["url"], quote=True)}" target="_blank">{html.escape(item.get("title", ""))}</a></h3><p>{html.escape(_first_sentence(first_value, 150))}</p></div>
<a class="arrow" href="{html.escape(item["url"], quote=True)}" target="_blank" aria-label="打开原文">↗</a></article>'''


def render_site(items, errors, now, edition_slug, edition_label, output_dir, base_url, funcs):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_name = f"{now:%Y-%m-%d}-{edition_slug}.html"
    report_url = f"{base_url.rstrip('/')}/{report_name}"
    usable = [item for item in items if item.get("url") and not item.get("weak_summary")]
    ranked = sorted(usable, key=lambda x: (x.get("source_class") in ("官方", "专业媒体"), x.get("daily_scope", False), x.get("score", 0)), reverse=True)

    highlights, source_counts = [], {}
    for item in ranked:
        if source_counts.get(item.get("source", ""), 0):
            continue
        highlights.append(item)
        source_counts[item.get("source", "")] = 1
        if len(highlights) == 3:
            break
    chosen = {x["url"] for x in highlights}
    practical = [x for x in ranked if x["url"] not in chosen and funcs["editorial_type"](x) in ("product", "open_source")][:3]
    chosen.update(x["url"] for x in practical)
    thinking = [x for x in ranked if x["url"] not in chosen and (funcs["editorial_type"](x) == "research" or x.get("source") == "AI 人物与观点")][:3]
    chosen.update(x["url"] for x in thinking)
    library = [x for x in usable if x["url"] not in chosen]

    archive_path = output_dir / "archive.json"
    try:
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        archive = []
    archive = [row for row in archive if row.get("file") != report_name]
    archive.insert(0, {"date": f"{now:%Y-%m-%d}", "edition": edition_label, "file": report_name})
    archive = archive[:120]
    archive_path.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive_links = "".join(f'<a href="{html.escape(row["file"], quote=True)}">{html.escape(row["date"])} · {html.escape(row["edition"])}</a>' for row in archive[:18])

    sources = sorted({x.get("source", "") for x in usable if x.get("source")})
    tags = sorted({tag for x in usable for tag in funcs["item_tags"](x)})
    groups = {name: [] for name in SECTION_NAMES}
    for item in library:
        groups[section_for(item, funcs["editorial_type"])].append(item)
    library_sections = "".join(
        f'<details class="library-section"><summary><span>{html.escape(name)}</span><small>{len(groups[name])} 条</small></summary><div>{"".join(library_card(item, funcs) for item in groups[name])}</div></details>'
        for name in SECTION_NAMES if groups[name]
    ) or '<p class="empty">本期没有更多资料。</p>'

    other_edition = "evening" if edition_slug == "morning" else "morning"
    other_file = f"{now:%Y-%m-%d}-{other_edition}.html"
    other_label = "下午篇" if edition_slug == "morning" else "上午篇"
    other_exists = any(row.get("file") == other_file for row in archive)
    switch_html = f'<a href="{html.escape(other_file, quote=True)}">切换到{other_label}</a>' if other_exists else f'<span>{other_label}尚未生成</span>'
    status = "本次所有来源均正常。" if not errors else "部分来源暂时不可用：" + "；".join(errors)
    reading_minutes = max(4, round((len(highlights) * 270 + len(practical + thinking) * 220 + 500) / 500))
    guide = "".join(f'<a href="#{anchor(item)}"><span>0{i}</span><b>{html.escape(item.get("title", ""))}</b><small>{html.escape(_first_sentence(_fields(item, funcs)[1], 74))}</small></a>' for i, item in enumerate(highlights, 1))

    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>Trending AI · {edition_label}</title>
<style>
:root{{--paper:#fbfaf7;--ink:#1c1917;--soft:#57534e;--muted:#78716c;--line:#ddd8cf;--accent:#b45309;--green:#166534;--violet:#6d28d9;--blue:#1d4ed8}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth;overflow-x:hidden}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.75 ui-sans-serif,system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;overflow-x:hidden}}a{{color:inherit}}h1,h2,h3,p,b,small{{overflow-wrap:anywhere}}.shell{{width:100%;max-width:1080px;margin:auto;padding:28px 42px 70px}}header{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:28px;align-items:end;padding:20px 0 30px;border-bottom:1px solid var(--line)}}.brand{{color:var(--accent);font-size:12px;font-weight:850;letter-spacing:.16em}}h1{{font-family:Georgia,"Noto Serif SC",serif;font-size:clamp(36px,6vw,66px);line-height:1.05;letter-spacing:-.045em;margin:9px 0 12px;max-width:800px}}header p{{color:var(--muted);margin:0}}.edition-switch{{text-align:right;font-size:14px}}.edition-switch a{{color:var(--blue);font-weight:750}}.reading-guide{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;border-bottom:1px solid var(--line)}}.reading-guide>a{{display:flex;min-width:0;flex-direction:column;gap:5px;padding:22px 20px 24px;text-decoration:none;border-right:1px solid var(--line)}}.reading-guide>a:first-child{{padding-left:0}}.reading-guide>a:last-child{{border:0}}.reading-guide span,.section-label{{font-size:12px;font-weight:850;letter-spacing:.12em;color:var(--accent)}}.reading-guide b{{font-size:16px;line-height:1.45}}.reading-guide small{{font-size:13px;line-height:1.55;color:var(--muted)}}.content{{width:100%;min-width:0;max-width:760px;margin:0 auto}}section{{padding:54px 0 8px}}section>h2{{font-family:Georgia,"Noto Serif SC",serif;font-size:34px;line-height:1.2;letter-spacing:-.025em;margin:8px 0}}.section-dek{{margin:0 0 13px;color:var(--muted)}}.feature{{padding:30px 0;border-bottom:1px solid var(--line)}}.eyebrow{{display:flex;flex-wrap:wrap;align-items:center;gap:7px;color:var(--muted);font-size:12px}}.number{{color:var(--accent);font-weight:850;margin-right:4px}}.feature h3{{font-family:Georgia,"Noto Serif SC",serif;font-size:clamp(24px,4vw,34px);line-height:1.3;letter-spacing:-.02em;margin:9px 0 14px}}.feature h3 a,.library-card h3 a{{text-decoration:none}}.feature h3 a:hover,.library-card h3 a:hover{{color:var(--blue)}}.feature p{{margin:9px 0}}.lead{{font-size:18px;line-height:1.8}}.context{{color:var(--soft)}}.story-foot{{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-top:16px}}.tag{{border:0;border-radius:999px;padding:5px 9px;margin-right:5px;background:#eeeae2;color:#57534e;cursor:pointer}}.source-link{{color:var(--blue);font-weight:750;text-decoration:none;white-space:nowrap}}.practical .section-label{{color:var(--green)}}.thinking .section-label{{color:var(--violet)}}.library-head{{display:flex;justify-content:space-between;gap:24px;align-items:end}}.filters{{display:grid;grid-template-columns:2fr 1fr 1fr;gap:9px;margin:22px 0 16px}}input,select{{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);font:inherit;font-size:14px}}.library-section{{border-top:1px solid var(--line)}}.library-section summary{{display:flex;justify-content:space-between;align-items:center;padding:17px 0;cursor:pointer;font-weight:800;list-style:none}}.library-section summary::-webkit-details-marker{{display:none}}.library-section summary small{{color:var(--muted);font-weight:500}}.library-card{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:20px;padding:17px 0;border-top:1px solid #ebe7df}}.library-card h3{{font-size:17px;line-height:1.45;margin:3px 0}}.library-card p{{font-size:14px;line-height:1.6;color:var(--soft);margin:0}}.meta{{font-size:12px;color:var(--muted)}}.arrow{{font-size:18px;color:var(--blue);text-decoration:none}}.archive{{display:flex;flex-wrap:wrap;gap:8px}}.archive a{{font-size:13px;padding:6px 10px;background:#eeeae2;border-radius:999px;text-decoration:none}}footer{{max-width:760px;margin:48px auto 0;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}}.empty{{color:var(--muted)}}
@media(max-width:720px){{.shell{{padding:18px 18px 48px}}header{{grid-template-columns:minmax(0,1fr)}}h1{{font-size:34px;letter-spacing:-.035em}}.edition-switch{{text-align:left}}.reading-guide{{display:block}}.reading-guide>a{{border-right:0;border-bottom:1px solid var(--line);padding:17px 0}}.reading-guide>a:last-child{{border-bottom:0}}section{{padding-top:42px}}section>h2{{font-size:29px}}.feature h3{{font-size:25px}}.lead{{font-size:16px}}.story-foot{{align-items:flex-start;flex-direction:column}}.library-head{{display:block}}.filters{{grid-template-columns:1fr}}}}
</style></head><body><main class="shell" id="top">
<header><div><div class="brand">TRENDING AI · DAILY BRIEFING</div><h1>今天的 AI，先读这三件事</h1><p>{now:%Y-%m-%d %H:%M}（北京时间）· {edition_label} · 约 {reading_minutes} 分钟</p></div><div class="edition-switch">{switch_html}</div></header>
<nav class="reading-guide" aria-label="今日阅读路线">{guide}</nav>
<div class="content">
<section><div class="section-label">01 · TODAY'S SIGNAL</div><h2>今天最值得知道</h2><p class="section-dek">不是热度最高的三条，而是最值得占用你注意力的三条。</p>{''.join(feature_card(item, funcs, i) for i, item in enumerate(highlights, 1))}</section>
{f'<section class="practical"><div class="section-label">02 · USE IT</div><h2>可以马上试试</h2><p class="section-dek">把用途、门槛和限制说清楚，再决定要不要点开。</p>{"".join(feature_card(item, funcs) for item in practical)}</section>' if practical else ''}
{f'<section class="thinking"><div class="section-label">03 · THINK ABOUT IT</div><h2>值得想一想</h2><p class="section-dek">把研究结果、人物观点与已经可用的产品分开。</p>{"".join(feature_card(item, funcs) for item in thinking)}</section>' if thinking else ''}
<section class="library"><div class="library-head"><div><div class="section-label">04 · THE LIBRARY</div><h2>完整资料库</h2></div><p class="section-dek">想深挖时再来，不让它打断前面的阅读。</p></div>
<div class="filters"><input id="q" placeholder="搜索标题、摘要、来源或标签"><select id="source"><option value="">全部来源</option>{''.join(f'<option>{html.escape(x)}</option>' for x in sources)}</select><select id="tagFilter"><option value="">全部标签</option>{''.join(f'<option>{html.escape(x)}</option>' for x in tags)}</select></div>{library_sections}</section>
<section><div class="section-label">ARCHIVE</div><h2>历史日报</h2><div class="archive">{archive_links}</div></section>
</div><footer><p>{html.escape(status)}</p><p>本期从 {len(items)} 条候选中编辑出 {len(highlights) + len(practical) + len(thinking)} 条重点，其余 {len(library)} 条进入可搜索资料库。</p></footer>
<script>const stories=[...document.querySelectorAll('.story')],q=document.querySelector('#q'),source=document.querySelector('#source'),tag=document.querySelector('#tagFilter');function apply(){{const s=q.value.trim().toLowerCase();stories.forEach(c=>{{const miss=(s&&!c.dataset.search.includes(s))||(source.value&&c.dataset.source!==source.value)||(tag.value&&!c.dataset.tags.split('|').includes(tag.value));c.hidden=!!miss;if(!miss&&s)c.closest('details')?.setAttribute('open','')}})}}q.addEventListener('input',apply);source.addEventListener('change',apply);tag.addEventListener('change',apply);document.querySelectorAll('.tag').forEach(b=>b.onclick=()=>{{tag.value=b.dataset.tag;apply();document.querySelector('.library')?.scrollIntoView({{behavior:'smooth'}})}});</script></main></body></html>'''
    (output_dir / report_name).write_text(page, encoding="utf-8")
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    return report_url, {item["url"]: f"{report_url}#{anchor(item)}" for item in usable}
