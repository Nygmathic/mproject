#!/usr/bin/env python3
"""
Veridus.space Auto News Poster
Fetches political & business news from RSS feeds,
rewrites with Gemini AI, and posts as Hugo markdown files.
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
CONTENT_DIR = Path("content/news")  # Hugo content directory
POSTED_LOG = Path(".posted_articles.json")  # tracks already-posted articles

# Articles per niche per run — tuned to how much news each topic generates
NICHE_LIMITS = {
    "politics":      2,  # high volume
    "global-affairs": 2,  # high volume
    "africa":        2,  # high volume
    "sports":        2,  # high volume
    "business":      1,  # moderate volume
    "climate":       1,  # lower volume
}

# ─── RSS FEED SOURCES ─────────────────────────────────────────────────────────
# Deliberately diverse — Africa, Asia, Europe, Americas, Middle East all represented.

RSS_FEEDS = {
    "politics": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",  # NYT — USA
        "https://feeds.npr.org/1014/rss.xml",                         # NPR — USA
        "https://www.theguardian.com/politics/rss",                   # Guardian — UK
        "https://www.dw.com/en/politics/rss",                         # DW — Germany
        "https://www.aljazeera.com/xml/rss/all.xml",                  # Al Jazeera — Middle East
        "https://feeds.feedburner.com/ndtvnews-latest",               # NDTV — India
        "https://feeds.reuters.com/Reuters/PoliticsNews",             # Reuters — Global
    ],

    "business": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",  # NYT — USA
        "https://feeds.bbci.co.uk/news/business/rss.xml",             # BBC — UK
        "https://www.scmp.com/rss/92/feed",                           # SCMP — Hong Kong
        "https://feeds.feedburner.com/business-standard",             # Business Standard — India
        "https://www.theafricareport.com/feed/",                      # Africa Report — Africa
        "https://feeds.reuters.com/reuters/businessNews",             # Reuters — Global
    ],

    "global-affairs": [
        "https://foreignpolicy.com/feed/",                            # Foreign Policy — USA
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",     # NYT — USA
        "https://www.theguardian.com/world/rss",                      # Guardian — UK
        "https://www.dw.com/en/world/rss",                            # DW — Germany
        "https://www.aljazeera.com/xml/rss/all.xml",                  # Al Jazeera — Middle East
        "https://www3.nhk.or.jp/nhkworld/en/news/feeds/",             # NHK — Japan
        "https://feeds.reuters.com/Reuters/worldNews",                # Reuters — Global
    ],

    "sports": [
        "https://feeds.bbci.co.uk/sport/rss.xml",                     # BBC Sport — UK/Global
        "https://www.theguardian.com/sport/rss",                      # Guardian — UK
        "https://www.espn.com/espn/rss/news",                         # ESPN — Americas
        "https://supersport.com/rss",                                 # SuperSport — Africa
        "https://www.scmp.com/rss/95/feed",                           # SCMP Sport — Asia
        "https://www.dw.com/en/sports/rss",                           # DW Sport — Europe
    ],

    "climate": [
        "https://www.theguardian.com/environment/climate-crisis/rss", # Guardian — UK
        "https://insideclimatenews.org/feed/",                        # Inside Climate News — USA
        "https://www.climatecentral.org/feed",                        # Climate Central — USA
        "https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml",   # NYT — USA
        "https://www.dw.com/en/environment/rss",                      # DW — Germany/Europe
        "https://www.aljazeera.com/xml/rss/all.xml",                  # Al Jazeera — Global South
    ],

    "africa": [
        "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", # AllAfrica — Pan-African
        "https://www.theafricareport.com/feed/",                      # Africa Report — Pan-African
        "https://www.premiumtimesng.com/feed",                        # Premium Times — Nigeria
        "https://www.ghanaweb.com/GhanaHomePage/NewsArchive/rss.php", # GhanaWeb — Ghana
        "https://eastafrican.nation.africa/feed",                     # East African — Kenya/EA
        "https://www.dailymaverick.co.za/feed/",                      # Daily Maverick — South Africa
        "https://www.monitor.co.ug/rss",                              # Daily Monitor — Uganda
        "https://www.egyptindependent.com/feed/",                     # Egypt Independent — North Africa
    ],
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

# Common non-English characters — if a title or summary contains these,
# the article is not in English and should be skipped.
NON_ENGLISH_PATTERN = re.compile(
    r"[\u0400-\u04FF"   # Cyrillic (Russian)
    r"\u4E00-\u9FFF"    # CJK Unified Ideographs (Chinese, Japanese, Korean)
    r"\u3040-\u30FF"    # Hiragana / Katakana (Japanese)
    r"\u0600-\u06FF"    # Arabic
    r"\u0900-\u097F"    # Devanagari (Hindi)
    r"\uAC00-\uD7AF"    # Hangul (Korean)
    r"\u0370-\u03FF"    # Greek
    r"\u0590-\u05FF]"   # Hebrew
)

def is_english(text):
    """Return True if the text appears to be in English."""
    if not text:
        return False
    # Reject if non-Latin characters detected
    if NON_ENGLISH_PATTERN.search(text):
        return False
    # Reject if less than 60% of characters are ASCII (catches most non-English Latin scripts too)
    ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
    if ascii_ratio < 0.60:
        return False
    return True

def load_posted_log():
    if POSTED_LOG.exists():
        return json.loads(POSTED_LOG.read_text())
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
    return text[:60]

def fetch_rss_articles(niche, already_posted):
    """Fetch fresh articles from RSS feeds for a given niche."""
    articles = []
    for feed_url in RSS_FEEDS[niche]:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:  # take top 5 per feed
                url = entry.get("link", "")
                aid = article_id(url)
                if aid in already_posted:
                    continue
                title = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                # Strip HTML tags from summary
                summary = re.sub(r"<[^>]+>", "", summary).strip()
                if not title or not summary or len(summary) < 80:
                    continue
                # Skip any non-English articles
                if not is_english(title) or not is_english(summary):
                    print(f"  ⏭️  Skipping non-English article: {title[:50]}")
                    continue
                articles.append({
                    "id": aid,
                    "title": title,
                    "summary": summary,
                    "url": url,
                    "niche": niche,
                    "source": feed.feed.get("title", feed_url),
                })
        except Exception as e:
            print(f"  ⚠️  Failed to fetch {feed_url}: {e}")
    return articles

def rewrite_with_gemini(article):
    """Send article to Gemini API for rewriting."""
    if not GEMINI_API_KEY:
        print("  ⚠️  No GEMINI_API_KEY set, using original summary.")
        return None

    niche = article["niche"]
    regional_note = ""
    if niche == "global-affairs":
        regional_note = "- Cover international relations and geopolitics from a balanced, multi-polar perspective. Represent voices from the Global South, not just Western powers.\n"
    elif niche == "sports":
        regional_note = "- Cover sport globally. Include African sports, football, athletics and other disciplines — not just American or European leagues.\n"
    elif niche == "climate":
        regional_note = "- Emphasise the human and economic impact of climate change, especially on vulnerable regions like Africa. Use clear, non-alarmist language grounded in science.\n"
    elif niche == "africa":
        regional_note = "- Write from an African-centred perspective. Avoid patronising or 'Western saviour' framing. Treat African nations and leaders as full actors with agency, not as subjects.\n"

    prompt = f"""You are a professional news editor for Veridus.space, a credible international news website committed to fair, balanced global coverage.

Rewrite the following news article summary into a complete, engaging, original news article.

Rules:
- Write 3–5 paragraphs
- Use your own words entirely — do NOT copy the original
- Add brief context or background where helpful
- Maintain a neutral, professional tone
- Do NOT make up facts or quotes
- Present all sides fairly — avoid ideological or geographic bias
- End with a forward-looking sentence about what to watch next
- Write in English ONLY — do not use any other language under any circumstances
- Do NOT mention, reference, or credit any external news source, outlet, or publication anywhere in the article
- Write as if this is original Veridus reporting
{regional_note}
Article Niche: {niche.upper()}
Original Title: {article['title']}
Original Summary: {article['summary']}
"""

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"
    headers = {"Content-Type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024},
    }

    try:
        resp = requests.post(url, headers=headers, params=params, json=body, timeout=30)
        print(f"  🔍 Gemini status: {resp.status_code}")
        if not resp.ok:
            print(f"  ⚠️  Gemini error body: {resp.text[:300]}")
            return None
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        print(f"  ✅ Gemini rewrote: {len(text)} chars")
        return text
    except Exception as e:
        print(f"  ⚠️  Gemini exception: {e}")
        return None

def build_hugo_markdown(article, body):
    """Create Hugo-compatible markdown with frontmatter."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    niche = article["niche"]
    title = article["title"].replace('"', '\\"')
    description = article["summary"][:150].replace('"', '\\"') + "..."

    # Tags and categories based on niche
    niche_meta = {
        "politics":       ("Politics",      '["Politics", "News"]',       '["politics", "world news"]'),
        "business":       ("Business",      '["Business", "News"]',       '["business", "economy", "markets"]'),
        "global-affairs": ("Global Affairs",'["Global Affairs", "News"]', '["global affairs", "geopolitics", "diplomacy"]'),
        "climate":        ("Climate",       '["Climate", "News"]',        '["climate", "environment", "sustainability"]'),
        "sports":         ("Sports",        '["Sports"]',                 '["sports", "football", "athletics"]'),
        "africa":         ("Africa",        '["Africa", "News"]',         '["africa", "world news"]'),
        "africa":     ("Africa",     '["Africa", "News"]',      '["africa", "world news"]'),
    }
    category, categories, tags = niche_meta.get(niche, ("World", '["News"]', '["world news"]'))

    frontmatter = f"""---
title: "{title}"
date: {now}
draft: false
categories: {categories}
tags: {tags}
description: "{description}"
---

"""
    return frontmatter + body

def save_hugo_post(article, content):
    """Save article as a Hugo markdown file."""
    niche_dir = CONTENT_DIR / article["niche"]
    niche_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(article["title"])
    filename = niche_dir / f"{date_str}-{slug}.md"

    # Avoid overwriting if file exists (e.g. same slug same day)
    counter = 1
    while filename.exists():
        filename = niche_dir / f"{date_str}-{slug}-{counter}.md"
        counter += 1

    filename.write_text(content, encoding="utf-8")
    print(f"  ✅ Saved: {filename}")
    return str(filename)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🚀 Veridus.space Auto News Poster — {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 60)

    posted_log = load_posted_log()
    posted_ids = set(posted_log)
    new_posted = []
    total_saved = 0

    for niche in ["politics", "global-affairs", "africa", "sports", "business", "climate"]:
        print(f"\n📰 Fetching {niche.upper()} news...")
        articles = fetch_rss_articles(niche, posted_ids)
        print(f"  Found {len(articles)} new articles")

        limit = NICHE_LIMITS.get(niche, 1)
        saved_count = 0
        for article in articles:
            if saved_count >= limit:
                break
            print(f"\n  📝 Processing: {article['title'][:70]}...")

            # Rewrite with Gemini
            body = rewrite_with_gemini(article)
            if not body:
                # Fallback: use original summary with a note
                body = article["summary"] + "\n\n*Read the full story at the source link below.*"

            content = build_hugo_markdown(article, body)
            save_hugo_post(article, content)

            new_posted.append(article["id"])
            posted_ids.add(article["id"])
            saved_count += 1
            total_saved += 1

    # Update log
    # Keep only last 500 IDs to prevent file growing too large
    updated_log = list(posted_ids)[-500:]
    save_posted_log(updated_log)

    print(f"\n✨ Done! Posted {total_saved} new articles.")
    print("=" * 60)

if __name__ == "__main__":
    main()