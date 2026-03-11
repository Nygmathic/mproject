#!/usr/bin/env python3
"""
Veridus.space Auto News Poster
- Fetches global news from RSS (Europe, Africa, Americas, Russia, China)
- Rewrites in Guardian-style journalistic English (800-1200 words)
- Generates full SEO metadata: title tag, meta description, focus keyword, slug
- Primary AI: Google Gemini 2.0 Flash (free)
- Fallback AI: Groq / Llama 3.3 70B (free)
- Skips article entirely if neither AI produces usable content
- Never posts raw RSS content or source attributions
"""

import os
import re
import json
import hashlib
import feedparser
import requests
from datetime import datetime, timezone
from pathlib import Path

# ─── CONFIG ───────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")

CONTENT_DIR = Path("content/news")
POSTED_LOG  = Path(".posted_articles.json")

NICHE_LIMITS = {
    "politics":       2,
    "global-affairs": 2,
    "africa":         2,
    "sports":         2,
    "business":       1,
    "climate":        1,
}

# ─── RSS FEEDS ────────────────────────────────────────────────────────────────
# Global coverage: Europe, Africa, Americas, Russia, China, Middle East

RSS_FEEDS = {
    "politics": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",       # Americas
        "https://feeds.npr.org/1014/rss.xml",                              # Americas
        "https://www.theguardian.com/politics/rss",                        # Europe
        "https://www.dw.com/en/politics/rss",                              # Europe
        "https://www.euronews.com/rss?format=mrss&level=theme&name=news",  # Europe
        "https://meduza.io/en/rss/all",                                    # Russia
        "https://www.themoscowtimes.com/rss",                              # Russia
        "https://www.scmp.com/rss/91/feed",                                # China
        "https://www.sixthtone.com/feed",                                  # China
        "https://www.aljazeera.com/xml/rss/all.xml",                       # Middle East
        "https://feeds.reuters.com/Reuters/PoliticsNews",                  # Global
    ],
    "business": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",       # Americas
        "https://feeds.bbci.co.uk/news/business/rss.xml",                  # Europe
        "https://www.dw.com/en/economy/rss",                               # Europe
        "https://www.scmp.com/rss/92/feed",                                # China
        "https://www.caixinglobal.com/rss/",                               # China
        "https://www.theafricareport.com/feed/",                           # Africa
        "https://www.premiumtimesng.com/feed",                             # Africa
        "https://feeds.reuters.com/reuters/businessNews",                  # Global
    ],
    "global-affairs": [
        "https://foreignpolicy.com/feed/",                                 # Americas
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",          # Americas
        "https://www.theguardian.com/world/rss",                           # Europe
        "https://www.dw.com/en/world/rss",                                 # Europe
        "https://www.euronews.com/rss?format=mrss&level=theme&name=news",  # Europe
        "https://www.themoscowtimes.com/rss",                              # Russia
        "https://www.scmp.com/rss/91/feed",                                # China
        "https://www.aljazeera.com/xml/rss/all.xml",                       # Middle East
        "https://feeds.reuters.com/Reuters/worldNews",                     # Global
    ],
    "sports": [
        "https://feeds.bbci.co.uk/sport/rss.xml",                          # Europe / Global
        "https://www.theguardian.com/sport/rss",                           # Europe
        "https://www.dw.com/en/sports/rss",                                # Europe
        "https://www.espn.com/espn/rss/news",                              # Americas
        "https://supersport.com/rss",                                      # Africa
        "https://www.scmp.com/rss/95/feed",                                # China / Asia
    ],
    "climate": [
        "https://www.theguardian.com/environment/climate-crisis/rss",      # Europe
        "https://www.dw.com/en/environment/rss",                           # Europe
        "https://insideclimatenews.org/feed/",                             # Americas
        "https://www.climatecentral.org/feed",                             # Americas
        "https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml",        # Americas
        "https://www.aljazeera.com/xml/rss/all.xml",                       # Global South
    ],
    "africa": [
        "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf",  # Pan-African
        "https://www.theafricareport.com/feed/",                           # Pan-African
        "https://www.premiumtimesng.com/feed",                             # West Africa
        "https://www.ghanaweb.com/GhanaHomePage/NewsArchive/rss.php",      # West Africa
        "https://eastafrican.nation.africa/feed",                          # East Africa
        "https://www.monitor.co.ug/rss",                                   # East Africa
        "https://www.dailymaverick.co.za/feed/",                           # Southern Africa
        "https://www.egyptindependent.com/feed/",                          # North Africa
    ],
}

# ─── NICHE METADATA ───────────────────────────────────────────────────────────

NICHE_META = {
    "politics":       ("Politics",      '["Politics", "News"]',       '["politics", "world news", "global politics"]'),
    "business":       ("Business",      '["Business", "News"]',       '["business", "economy", "markets", "trade"]'),
    "global-affairs": ("Global Affairs",'["Global Affairs", "News"]', '["global affairs", "geopolitics", "diplomacy", "international relations"]'),
    "climate":        ("Climate",       '["Climate", "News"]',        '["climate change", "environment", "sustainability", "global warming"]'),
    "sports":         ("Sports",        '["Sports"]',                 '["sports", "football", "athletics", "world sport"]'),
    "africa":         ("Africa",        '["Africa", "News"]',         '["africa", "african politics", "african business", "world news"]'),
}

# ─── ENGLISH DETECTION ────────────────────────────────────────────────────────

NON_ENGLISH_PATTERN = re.compile(
    r"[\u0400-\u04FF"
    r"\u4E00-\u9FFF"
    r"\u3040-\u30FF"
    r"\u0600-\u06FF"
    r"\u0900-\u097F"
    r"\uAC00-\uD7AF"
    r"\u0370-\u03FF"
    r"\u0590-\u05FF]"
)

def is_english(text):
    if not text:
        return False
    if NON_ENGLISH_PATTERN.search(text):
        return False
    return sum(1 for c in text if ord(c) < 128) / max(len(text), 1) >= 0.60

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def load_posted_log():
    if POSTED_LOG.exists():
        try:
            return json.loads(POSTED_LOG.read_text())
        except Exception:
            return []
    return []

def save_posted_log(posted):
    POSTED_LOG.write_text(json.dumps(posted, indent=2))

def article_id(url):
    return hashlib.md5(url.encode()).hexdigest()

def slugify(text):
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:70]

# ─── RSS FETCHING ─────────────────────────────────────────────────────────────

def fetch_rss_articles(niche, already_posted):
    articles = []
    for feed_url in RSS_FEEDS.get(niche, []):
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                url = entry.get("link", "")
                if not url:
                    continue
                aid = article_id(url)
                if aid in already_posted:
                    continue
                title   = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                summary = re.sub(r"<[^>]+>", "", summary).strip()
                if not title or not summary or len(summary) < 80:
                    continue
                if not is_english(title) or not is_english(summary):
                    print(f"  ⏭️  Non-English: {title[:50]}")
                    continue
                articles.append({
                    "id":      aid,
                    "title":   title,
                    "summary": summary,
                    "url":     url,
                    "niche":   niche,
                })
        except Exception as e:
            print(f"  ⚠️  Feed error {feed_url}: {e}")
    return articles

# ─── PROMPT: ARTICLE BODY ─────────────────────────────────────────────────────

def build_article_prompt(article):
    niche = article["niche"]

    niche_guidance = {
        "politics":       "Cover political developments with global context. Represent multiple regional perspectives including voices from the Global South, Europe, Russia, China, and Africa.",
        "business":       "Cover business and economic developments with global impact. Include emerging market perspectives from Africa, Asia, and Latin America alongside Western economies.",
        "global-affairs": "Cover geopolitics from a balanced, multi-polar perspective. Give equal weight to European, African, Asian, Russian, and Chinese perspectives — not only Western ones.",
        "sports":         "Cover sport globally — African football, European leagues, athletics, and other disciplines. Do not centre only American or British sport.",
        "climate":        "Emphasise human and economic impact of climate change, especially on the most vulnerable regions. Ground all claims in science. Avoid alarmism.",
        "africa":         "Write from an African-centred perspective. Treat African nations and people as full agents of their own story. Avoid patronising or 'Western saviour' framing entirely.",
    }

    guidance = niche_guidance.get(niche, "Cover this story with global context and balance.")

    return f"""You are a senior international correspondent writing in the style of The Guardian — authoritative, precise, deeply reported, and globally minded.

Write a complete, original news article based on the headline and summary below.

STRICT REQUIREMENTS:
- MINIMUM 800 words. MAXIMUM 1,200 words. Non-negotiable.
- 6 to 8 substantial paragraphs — no thin or short paragraphs
- Opening paragraph: Compelling and immediate — draws the reader in without starting with "In a" or "The"
- Second paragraph: Expand on the key facts and the stakes of the story
- Middle paragraphs: Context, background, analysis, multiple perspectives, historical parallels where relevant
- Penultimate paragraph: Reactions, implications, what different stakeholders are doing or saying
- Final paragraph: Forward-looking — what happens next and what readers should watch
- Use two or three descriptive H2 subheadings (## Heading) to break the article into sections — this aids readability and SEO
- Tone: Authoritative, measured, internationally minded
- Vocabulary: Precise journalistic English. No clichés. No sensationalism.
- Do NOT fabricate quotes, statistics, or facts
- Do NOT mention or reference any news outlet, wire service, or publication anywhere in the article
- Do NOT include the main headline — body text and subheadings only
- Do NOT use bullet points or numbered lists — flowing prose only
- Write in English only

EDITORIAL FOCUS: {guidance}

NICHE: {niche.upper()}
HEADLINE: {article['title']}
SUMMARY: {article['summary']}

Write the full article now:"""

# ─── PROMPT: SEO METADATA ─────────────────────────────────────────────────────

def build_seo_prompt(article, body):
    return f"""You are an SEO specialist for an international news website.

Given the article headline, summary, and body below, generate SEO metadata to maximise Google search traffic and ad revenue.

Return ONLY a valid JSON object with these exact keys — no extra text, no markdown, no explanation:

{{
  "seo_title": "A compelling, keyword-rich title for Google (50-60 characters max). Different from the original headline — optimised for search clicks.",
  "meta_description": "An engaging meta description for Google (150-160 characters max). Include the main keyword naturally. Make it compelling enough to click.",
  "focus_keyword": "The single most important search keyword or phrase for this article (2-5 words).",
  "secondary_keywords": ["keyword 2", "keyword 3", "keyword 4", "keyword 5"],
  "seo_slug": "url-friendly-slug-with-main-keyword-no-stopwords-max-8-words"
}}

ORIGINAL HEADLINE: {article['title']}
NICHE: {article['niche'].upper()}
ARTICLE BODY (first 400 words): {' '.join(body.split()[:400])}

Return only the JSON object:"""

# ─── AI CALL (SHARED) ─────────────────────────────────────────────────────────

def call_gemini(prompt, max_tokens=2048):
    if not GEMINI_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens, "topP": 0.9},
            },
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        if not resp.ok:
            print(f"  ❌ Gemini error {resp.status_code}: {resp.text[:250]}")
            return None
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"  ❌ Gemini exception: {e}")
        return None

def call_groq(prompt, max_tokens=2048):
    if not GROQ_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model":       "llama-3.3-70b-versatile",
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens":  max_tokens,
            },
            timeout=60,
        )
        if not resp.ok:
            print(f"  ❌ Groq error {resp.status_code}: {resp.text[:250]}")
            return None
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  ❌ Groq exception: {e}")
        return None

# ─── ARTICLE REWRITE ──────────────────────────────────────────────────────────

def rewrite_article(article):
    prompt = build_article_prompt(article)

    # Try Gemini first
    print(f"  🤖 Trying Gemini...")
    text = call_gemini(prompt, max_tokens=2048)
    if text:
        words = len(text.split())
        print(f"  ✅ Gemini: {words} words")
        if words >= 300:
            return text
        print(f"  ⚠️  Too short ({words} words)")

    # Fallback to Groq
    print(f"  🔄 Trying Groq fallback...")
    text = call_groq(prompt, max_tokens=2048)
    if text:
        words = len(text.split())
        print(f"  ✅ Groq: {words} words")
        if words >= 300:
            return text
        print(f"  ⚠️  Too short ({words} words)")

    print("  ❌ Both AIs failed — skipping article")
    return None

# ─── SEO METADATA GENERATION ──────────────────────────────────────────────────

def generate_seo(article, body):
    """Generate SEO metadata via AI. Returns dict with seo fields."""
    prompt = build_seo_prompt(article, body)

    raw = call_gemini(prompt, max_tokens=400)
    if not raw:
        raw = call_groq(prompt, max_tokens=400)
    if not raw:
        return None

    # Strip markdown code fences if present
    raw = re.sub(r"```json|```", "", raw).strip()

    try:
        seo = json.loads(raw)
        # Validate required keys
        required = ["seo_title", "meta_description", "focus_keyword", "secondary_keywords", "seo_slug"]
        if all(k in seo for k in required):
            return seo
    except Exception as e:
        print(f"  ⚠️  SEO JSON parse failed: {e}")

    return None

# ─── HUGO MARKDOWN ────────────────────────────────────────────────────────────

def build_hugo_markdown(article, body, seo):
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    niche = article["niche"]
    _, categories, base_tags = NICHE_META.get(niche, ("World", '["News"]', '["world news"]'))

    # Use AI-generated SEO title if available, else original headline
    display_title = article["title"].replace('"', '\\"')
    seo_title     = seo["seo_title"].replace('"', '\\"')           if seo else display_title
    meta_desc     = seo["meta_description"].replace('"', '\\"')    if seo else article["summary"][:155].replace('"', '\\"')
    focus_kw      = seo["focus_keyword"].replace('"', '\\"')       if seo else ""
    sec_kws       = json.dumps(seo["secondary_keywords"])          if seo else "[]"

    # Merge base tags with secondary keywords for richer tagging
    try:
        base_list = json.loads(base_tags)
        sec_list  = seo["secondary_keywords"] if seo else []
        all_tags  = list(dict.fromkeys(base_list + sec_list))[:8]  # dedupe, max 8
        tags_str  = json.dumps(all_tags)
    except Exception:
        tags_str = base_tags

    return f"""---
title: "{display_title}"
seo_title: "{seo_title}"
date: {now}
lastmod: {now}
draft: false
categories: {categories}
tags: {tags_str}
description: "{meta_desc}"
focus_keyword: "{focus_kw}"
keywords: {sec_kws}
---

{body}
"""

def save_hugo_post(article, content, seo):
    niche_dir = CONTENT_DIR / article["niche"]
    niche_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Use SEO slug if available, else fall back to slugified title
    slug = slugify(seo["seo_slug"]) if seo and seo.get("seo_slug") else slugify(article["title"])
    filename = niche_dir / f"{date_str}-{slug}.md"

    counter = 1
    while filename.exists():
        filename = niche_dir / f"{date_str}-{slug}-{counter}.md"
        counter += 1

    filename.write_text(content, encoding="utf-8")
    print(f"  💾 Saved: {filename}")
    if seo:
        print(f"  🔑 Focus keyword: {seo.get('focus_keyword', 'n/a')}")
        print(f"  📄 Meta: {seo.get('meta_description', '')[:80]}...")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'=' * 65}")
    print(f"🚀 Veridus.space Auto News Poster")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 65}")

    if not GEMINI_API_KEY and not GROQ_API_KEY:
        print("❌ No AI API keys set. Add GEMINI_API_KEY or GROQ_API_KEY to GitHub Secrets.")
        return

    if GEMINI_API_KEY:
        print(f"✅ Gemini key loaded (length: {len(GEMINI_API_KEY)})")
    if GROQ_API_KEY:
        print(f"✅ Groq key loaded (length: {len(GROQ_API_KEY)})")

    posted_ids  = set(load_posted_log())
    total_saved = 0

    for niche in ["politics", "global-affairs", "africa", "sports", "business", "climate"]:
        print(f"\n📰 [{niche.upper()}]")
        articles = fetch_rss_articles(niche, posted_ids)
        print(f"   {len(articles)} new articles available")

        limit       = NICHE_LIMITS.get(niche, 1)
        saved_count = 0

        for article in articles:
            if saved_count >= limit:
                break

            print(f"\n  📝 {article['title'][:75]}")

            # Step 1: Rewrite article body
            body = rewrite_article(article)
            if not body:
                continue

            # Step 2: Generate SEO metadata
            print(f"  🔍 Generating SEO metadata...")
            seo = generate_seo(article, body)
            if seo:
                print(f"  ✅ SEO generated")
            else:
                print(f"  ⚠️  SEO generation failed — using defaults")

            # Step 3: Build and save
            content = build_hugo_markdown(article, body, seo)
            save_hugo_post(article, content, seo)

            posted_ids.add(article["id"])
            saved_count += 1
            total_saved += 1

    save_posted_log(list(posted_ids)[-500:])
    print(f"\n{'=' * 65}")
    print(f"✨ Done — {total_saved} articles posted.")
    print(f"{'=' * 65}\n")

if __name__ == "__main__":
    main()