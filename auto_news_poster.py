#!/usr/bin/env python3
"""
Veridus.space Auto News Poster
- Fetches global news from RSS feeds with strict network timeouts
- Fetches full article body from source URL
- AI Engine: Google Gemini 2.5 Flash (rotates across keys)
- Enforces strict 700+ word count minimum for all posts
- Saves posts as Hugo Page Bundles in content/{niche}/
"""

import os
import sys
import re
import json
import hashlib
import time
import socket
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from html.parser import HTMLParser

# Global socket timeout to prevent indefinite network hangs
socket.setdefaulttimeout(15)

# ─── CONFIG & MODEL ───────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 VeridusAutoPoster/1.0"
)
HTTP_HEADERS = {"User-Agent": USER_AGENT}

GEMINI_API_KEYS = [
    key for key in [
        os.environ.get("GEMINI_API_KEY_1", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
        os.environ.get("GEMINI_API_KEY_3", ""),
        os.environ.get("GEMINI_API_KEY_4", ""),
        os.environ.get("GEMINI_API_KEY_5", ""),
        os.environ.get("GEMINI_API_KEY_6", ""),
    ] if key
]

CONTENT_DIR = Path("content")
POSTED_LOG  = Path(".posted_articles.json")

DAILY_POST_LIMIT = 4

NICHE_LIMITS = {
    "politics":       2,
    "africa":         2,
    "sports":         2,
    "business":       1,
    "climate":        1,
    "law":            2,
    "curious":        2,
}

RECENCY_HOURS = {
    "sports":         3,
    "politics":       6,
    "africa":         6,
    "business":       6,
    "curious":        12,
    "climate":        24,
    "law":            48,
}

# ─── RSS FEEDS ────────────────────────────────────────────────────────────────

RSS_FEEDS = {
    "politics": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        "https://feeds.npr.org/1014/rss.xml",
        "https://www.theguardian.com/politics/rss",
        "https://www.dw.com/en/politics/rss",
        "https://www.euronews.com/rss?format=mrss&level=theme&name=news",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://foreignpolicy.com/feed/",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.dw.com/en/world/rss",
        "https://news.un.org/feed/subscribe/en/news/all/feed/rss.xml",
    ],
    "business": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.dw.com/en/economy/rss",
        "https://www.theafricareport.com/feed/",
        "https://www.premiumtimesng.com/feed",
        "https://cnbc.com/id/100003114/device/rss/rss.html",
    ],
    "sports": [
        "https://www.theguardian.com/football/premierleague/rss",
        "https://feeds.bbci.co.uk/sport/football/premier-league/rss.xml",
        "https://www.skysports.com/rss/12040",
        "https://www.fourfourtwo.com/rss",
        "https://supersport.com/rss",
        "https://www.bbc.co.uk/sport/africa/rss.xml",
        "https://www.goal.com/en-ke/rss",
        "https://www.cafonline.com/rss",
        "https://www.espn.com/espn/rss/soccer/news",
        "https://feeds.bbci.co.uk/sport/rss.xml",
        "https://www.theguardian.com/sport/rss",
    ],
    "climate": [
        "https://www.theguardian.com/environment/climate-crisis/rss",
        "https://www.dw.com/en/environment/rss",
        "https://insideclimatenews.org/feed/",
        "https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml",
    ],
    "africa": [
        "https://news.un.org/feed/subscribe/en/news/topic/africa/feed/rss.xml",
        "https://au.int/en/pressreleases/rss",
        "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf",
        "https://www.theafricareport.com/feed/",
        "https://www.africanews.com/feed/",
        "https://eastafrican.nation.africa/feed",
        "https://www.monitor.co.ug/rss",
        "https://www.theeastafrican.co.ke/rss",
        "https://www.standardmedia.co.ke/rss",
        "https://nation.africa/kenya/rss.xml",
        "https://www.premiumtimesng.com/feed",
        "https://www.dailymaverick.co.za/feed/",
        "https://www.news24.com/rss",
        "https://www.egyptindependent.com/feed/",
        "https://www.middleeasteye.net/rss",
    ],
    "curious": [
        "https://www.theguardian.com/news/series/weird/rss",
        "https://feeds.bbci.co.uk/news/have_your_say/rss.xml",
        "https://www.upi.com/RSS/Odd_News/",
        "https://ripleys.com/feed/",
        "https://www.odditycentral.com/feed",
        "https://www.atlasobscura.com/feeds/latest",
        "https://www.mentalfloss.com/rss.xml",
        "https://www.livescience.com/feeds/all",
        "https://www.iflscience.com/rss.xml",
    ],
}

NICHE_META = {
    "politics":       ("Politics",      '["Politics", "News"]',       '["politics", "world news", "global politics", "geopolitics", "diplomacy"]'),
    "business":       ("Business",      '["Business", "News"]',       '["business", "economy", "markets", "trade"]'),
    "climate":        ("Climate",       '["Climate", "News"]',        '["climate change", "environment", "sustainability", "global warming"]'),
    "sports":         ("Sports",        '["Sports"]',                 '["sports", "football", "premier league", "african football", "athletics"]'),
    "africa":         ("Africa",        '["Africa", "News"]',         '["africa", "african politics", "african business", "world news"]'),
    "law":            ("Law",           '["Law", "News"]',            '["kenya law", "court ruling", "supreme court", "high court", "court of appeal"]'),
    "curious":        ("Curious",       '["Curious", "News"]',        '["bizarre", "unusual", "strange", "odd news", "weird science"]'),
}

NON_ENGLISH_PATTERN = re.compile(
    r"[\u0400-\u04FF\u4E00-\u9FFF\u3040-\u30FF\u0600-\u06FF\u0900-\u097F\uAC00-\uD7AF\u0370-\u03FF\u0590-\u05FF]"
)

BANNED_IMAGE_KEYWORDS = {
    "logo", "emblem", "crest", "badge", "icon", "banner", "symbol",
    "coat_of_arms", "coat-of-arms", "flag", "screenshot", "watermark",
    "poster", "map", "diagram", "chart", "vector", "svg"
}

BRAND_TERMS = {
    "sky", "skysports", "bbc", "espn", "goal", "guardian", "reuters",
    "afp", "ap", "cnn", "nytimes", "aljazeera", "dw", "supersport", "fourfourtwo"
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def is_english(text):
    if not text:
        return False
    if NON_ENGLISH_PATTERN.search(text):
        return False
    return sum(1 for c in text if ord(c) < 128) / max(len(text), 1) >= 0.60

def load_posted_log():
    MAX_LOG_AGE_DAYS = 14
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_LOG_AGE_DAYS)

    if POSTED_LOG.exists():
        try:
            raw = json.loads(POSTED_LOG.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                raw = {aid: "2000-01-01T00:00:00Z" for aid in raw}
            pruned = {
                aid: ts for aid, ts in raw.items()
                if datetime.fromisoformat(ts.replace("Z", "+00:00")) >= cutoff
            }
            return pruned
        except Exception:
            return {}
    return {}

def save_posted_log(posted):
    POSTED_LOG.write_text(json.dumps(posted, indent=2), encoding="utf-8")

def article_id(url):
    return hashlib.md5(url.encode()).hexdigest()

def slugify(text):
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:70]

def count_today_posts(niche):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    niche_dir = CONTENT_DIR / niche
    if not niche_dir.exists():
        return 0
    return sum(1 for d in niche_dir.iterdir() if d.is_dir() and d.name.startswith(today))

def score_by_virality(articles):
    now = datetime.now(timezone.utc)
    stop = {"this","that","with","from","have","will","been","were","they",
            "their","more","than","over","after","into","about","says","said"}
    
    def sig_words(title):
        return {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", title) if w.lower() not in stop}

    word_sets = [sig_words(a["title"]) for a in articles]
    scores = []
    
    for i, ws_i in enumerate(word_sets):
        if not ws_i:
            scores.append(0)
            continue
        score = sum(
            1 for j, ws_j in enumerate(word_sets)
            if i != j and len(ws_i & ws_j) >= 3
        )
        scores.append(score)

    def recency_bucket(article):
        pub = article.get("pub_date")
        if not pub:
            return 0
        age_hours = max(0, (now - pub).total_seconds() / 3600)
        return max(0, 12 - int(age_hours / 2))

    scored = sorted(
        zip(scores, articles),
        key=lambda x: (recency_bucket(x[1]), x[0]),
        reverse=True,
    )
    if any(s > 0 for s, _ in scored):
        top = [(s, a["title"][:60]) for s, a in scored[:5] if s > 0]
        print(f"   🔥 Top viral stories: {top}")
    return [a for _, a in scored]

def parse_entry_date(entry):
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None

def fetch_rss_articles(niche, already_posted):
    max_age_hours = RECENCY_HOURS.get(niche, 24)
    cutoff        = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    raw_entries   = []

    for feed_url in RSS_FEEDS.get(niche, []):
        try:
            resp = requests.get(feed_url, headers=HTTP_HEADERS, timeout=10)
            if not resp.ok:
                continue

            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url:
                    continue
                aid = article_id(url)
                if aid in already_posted:
                    continue

                pub_date = parse_entry_date(entry)
                if pub_date and pub_date < cutoff:
                    continue

                title   = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                summary = re.sub(r"<[^>]+>", "", summary).strip()

                if not title or not is_english(title):
                    continue

                raw_entries.append({
                    "id":       aid,
                    "title":    title,
                    "summary":  summary,
                    "url":      url,
                    "niche":    niche,
                    "pub_date": pub_date,
                })
        except Exception as e:
            print(f"  ⚠️ Feed error {feed_url}: {e}")

    raw_entries.sort(
        key=lambda x: x["pub_date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )

    NICHE_REQUIRED = {
        "sports":  ["football", "soccer", "match", "league", "cup", "goal", "player",
                    "club", "sport", "game", "tournament", "champion", "coach", "team",
                    "afcon", "premier league", "caf", "fifa", "rugby", "athletics",
                    "cricket", "tennis", "basketball", "racing", "olympic", "score"],
        "climate": ["climate", "environment", "carbon", "emission", "warming", "fossil",
                    "renewable", "drought", "flood", "weather", "temperature", "glacier",
                    "deforestation", "pollution", "net zero", "wildfire"],
        "law":     ["court", "ruling", "judgment", "judge", "appeal", "tribunal",
                    "supreme court", "high court", "verdict", "sentenced", "convicted",
                    "acquitted", "judicial", "lawsuit", "prosecution", "petition"],
        "business":["economy", "market", "trade", "gdp", "inflation", "investment",
                    "stock", "bank", "currency", "company", "revenue", "profit",
                    "financial", "debt", "tax", "export", "import"],
    }

    NICHE_BANNED = {
        "sports":  ["climate", "court ruling", "stock market", "inflation", "election"],
        "climate": ["football", "premier league", "match result", "goal", "transfer"],
        "law":     ["law society", "bar association", "lawyer appointed", "attorney general appointed"],
    }

    articles = []
    for entry in raw_entries:
        if len(entry.get("summary", "")) < 60:
            continue

        text = (entry["title"] + " " + entry["summary"]).lower()

        if any(kw in text for kw in NICHE_BANNED.get(niche, [])):
            continue

        required = NICHE_REQUIRED.get(niche, [])
        if required and not any(kw in text for kw in required):
            continue

        articles.append(entry)

    print(f"   {len(articles)} fresh articles found (recency: {max_age_hours}h)")
    return articles

# ─── FULL ARTICLE FETCHER ─────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside",
                 "noscript", "form", "button", "iframe", "figure", "figcaption"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in ("p", "h1", "h2", "h3", "h4", "li", "br", "div"):
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self):
        raw = "".join(self._chunks)
        lines = [" ".join(ln.split()) for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)

def fetch_full_article(url, min_chars=400, max_chars=8000):
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=10, allow_redirects=True)
        if not resp.ok:
            return None

        content = resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")
        extractor = _TextExtractor()
        extractor.feed(content)
        raw_text = extractor.get_text()

        paragraphs = [
            ln for ln in raw_text.splitlines()
            if len(ln) >= 40 and not ln.strip().startswith(("©", "Cookie", "Subscribe", "Sign in"))
        ]
        body = "\n\n".join(paragraphs)

        if len(body) < min_chars:
            return None

        return body[:max_chars]
    except Exception as e:
        print(f"  ⚠️️ Full-fetch failed: {e}")
        return None

# ─── PROMPTS ──────────────────────────────────────────────────────────────────

def build_article_prompt(article):
    niche = article["niche"]
    guidance_map = {
        "politics":       "Cover political developments and geopolitics with precision.",
        "business":       "Cover business and economic developments with global perspective.",
        "sports":         "Cover sports with energy, accuracy, and clear match/event context.",
        "climate":        "Emphasise human and economic impact. Ground all claims in science.",
        "africa":         "Write from an African-centred perspective with respectful authority.",
        "law":            "Cover formal court decisions and judgments strictly.",
        "curious":        "Cover strange, true news with an engaging and intelligent tone.",
    }
    guidance = guidance_map.get(niche, "Cover this story with balance and global context.")

    full_text = article.get("full_text", "")
    source_block = (
        f"SOURCE TEXT:\n{full_text}" if full_text
        else f"SUMMARY:\n{article['summary']}"
    )

    return f"""You are a senior international correspondent writing for Veridus.
Write a comprehensive, highly detailed original news article based on the facts provided below.
Do not invent or extrapolate details.

STRICT REQUIREMENTS:
- MANDATORY LENGTH: AT LEAST 700 WORDS (Target: 750 to 900 words).
- 6 to 9 structured, in-depth paragraphs with 3 to 4 H2 subheadings (## Heading).
- Flowing, analytical journalism only (NO bullet points).
- English language only.

EDITORIAL FOCUS: {guidance}
NICHE: {niche.upper()}
HEADLINE: {article['title']}
{source_block}

Write the full, detailed news article body (minimum 700 words) now:"""

def build_seo_prompt(article, body):
    return f"""Given the headline and body below, generate SEO metadata.
Return ONLY a valid JSON object with these exact keys:
{{
  "seo_title": "SEO title (50-60 chars max)",
  "meta_description": "Meta description (150-160 chars max)",
  "focus_keyword": "main keyword phrase",
  "secondary_keywords": ["kw1", "kw2", "kw3"],
  "seo_slug": "url-friendly-slug"
}}

HEADLINE: {article['title']}
BODY: {' '.join(body.split()[:300])}
"""

# ─── WIKIMEDIA IMAGES ─────────────────────────────────────────────────────────

ACCEPTED_LICENSES = {"cc0", "cc-by", "cc-by-sa", "public domain", "pd", "cc-pd"}
ACCEPTED_MIMES = {"image/jpeg", "image/png", "image/webp"}

def clean_image_query(query):
    words = re.findall(r"\w+", query.lower())
    filtered = [w for w in words if w not in BRAND_TERMS and w not in BANNED_IMAGE_KEYWORDS]
    return " ".join(filtered) if filtered else query

def fetch_wikimedia_image(search_query):
    clean_query = clean_image_query(search_query)
    try:
        resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action":      "query",
                "generator":   "search",
                "gsrnamespace": 6,
                "gsrsearch":   f"File:{clean_query}",
                "gsrlimit":    10,
                "prop":        "imageinfo",
                "iiprop":      "url|mime|extmetadata|size",
                "iiurlwidth":  1200,
                "format":      "json",
            },
            headers=HTTP_HEADERS,
            timeout=10,
        )
        if not resp.ok:
            return None

        pages = resp.json().get("query", {}).get("pages", {})
        candidates = []
        for page in pages.values():
            title = page.get("title", "").lower()
            if any(banned in title for banned in BANNED_IMAGE_KEYWORDS):
                continue

            info = page.get("imageinfo", [{}])[0]
            mime = info.get("mime", "")
            if mime not in ACCEPTED_MIMES or info.get("width", 0) < 400:
                continue

            meta = info.get("extmetadata", {})
            desc = (meta.get("ObjectName", {}).get("value", "") + " " + meta.get("ImageDescription", {}).get("value", "")).lower()
            if any(banned in desc for banned in BANNED_IMAGE_KEYWORDS):
                continue

            license_short = meta.get("LicenseShortName", {}).get("value", "").lower()
            artist = meta.get("Artist", {}).get("value", "")
            artist = re.sub(r"<[^>]+>", "", artist).strip()[:100]

            lic_norm = re.sub(r'[\s\.]+', '-', license_short)
            if not any(lic in lic_norm for lic in ACCEPTED_LICENSES) and "creative commons" not in license_short:
                continue

            img_url = info.get("thumburl") or info.get("url", "")
            if img_url:
                candidates.append({
                    "url": img_url,
                    "mime": mime,
                    "license": license_short,
                    "license_url": meta.get("LicenseUrl", {}).get("value", ""),
                    "attribution": artist or "Wikimedia Commons",
                    "width": info.get("width", 0),
                })

        if candidates:
            candidates.sort(key=lambda x: x["width"], reverse=True)
            return candidates[0]
    except Exception as e:
        print(f"  ⚠️ Wikimedia API error: {e}")
    return None

def download_wikimedia_image(image_info, dest_dir):
    try:
        ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
        ext = ext_map.get(image_info["mime"], "jpg")
        dest = dest_dir / f"cover.{ext}"

        resp = requests.get(image_info["url"], headers=HTTP_HEADERS, timeout=15, stream=True)
        if not resp.ok:
            return None

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)

        return f"cover.{ext}"
    except Exception as e:
        print(f"  ⚠️ Image download failed: {e}")
        return None

def get_feature_image(article, seo, dest_dir):
    query = (seo.get("focus_keyword") if seo else "") or article["title"][:50]
    query = re.sub(r"[^\w\s]", "", query).strip()

    image_info = fetch_wikimedia_image(query)
    if not image_info:
        fallback = article["niche"] + " news"
        image_info = fetch_wikimedia_image(fallback)

    if image_info:
        filename = download_wikimedia_image(image_info, dest_dir)
        return filename, image_info
    return None, None

# ─── GEMINI API CALLS ─────────────────────────────────────────────────────────

def call_gemini_with_key(prompt, api_key, max_tokens=3000):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        resp = requests.post(
            url,
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens},
            },
            headers={"Content-Type": "application/json"},
            timeout=35,
        )
        if resp.status_code == 429:
            print("  ⚠️ Gemini key rate limited (429) — trying next key...")
            return None, True
        if not resp.ok:
            print(f"  ❌ Gemini error {resp.status_code}: {resp.text[:250]}")
            return None, False

        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates and "content" in candidates[0]:
            parts = candidates[0]["content"].get("parts", [])
            if parts and "text" in parts[0]:
                return parts[0]["text"].strip(), False
        return None, False
    except Exception as e:
        print(f"  ❌ Gemini exception: {e}")
        return None, False

def call_gemini(prompt, max_tokens=3000):
    if not GEMINI_API_KEYS:
        return None
    for i, key in enumerate(GEMINI_API_KEYS):
        print(f"  🤖 Trying Gemini key {i + 1}/{len(GEMINI_API_KEYS)} ({GEMINI_MODEL})...")
        result, _ = call_gemini_with_key(prompt, key, max_tokens)
        if result:
            return result
    print(f"  ❌ All {len(GEMINI_API_KEYS)} Gemini keys failed/exhausted")
    return None

def rewrite_article(article):
    if not article.get("full_text") and article.get("url"):
        print(f"  🌐 Fetching full article from source...")
        full_text = fetch_full_article(article["url"])
        if full_text:
            article = {**article, "full_text": full_text}

    prompt = build_article_prompt(article)
    text = call_gemini(prompt, max_tokens=3000)

    if text:
        words = len(text.split())
        if words >= 700:
            print(f"  ✅ Generated article: {words} words")
            return text
        else:
            print(f"  ⚠️ Output short ({words} words < 700 target) — skipping article")
            return None

    print("  ❌ Gemini failed to generate valid text")
    return None

def generate_seo(article, body):
    prompt = build_seo_prompt(article, body)
    raw = call_gemini(prompt, max_tokens=400)
    if not raw:
        return None

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return None

# ─── HUGO BUILD & SAVE ────────────────────────────────────────────────────────

def build_hugo_markdown(article, body, seo, image_file=None, image_info=None):
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    niche = article["niche"]
    _, categories_raw, base_tags_raw = NICHE_META.get(niche, ("News", '["News"]', '["news"]'))

    display_title = article["title"]
    seo_title     = seo.get("seo_title", display_title) if seo else display_title
    meta_desc     = seo.get("meta_description", article["summary"][:155]) if seo else article["summary"][:155]
    focus_kw      = seo.get("focus_keyword", "") if seo else ""

    try:
        base_tags = json.loads(base_tags_raw)
        sec_tags  = seo.get("secondary_keywords", []) if seo else []
        all_tags  = list(dict.fromkeys(base_tags + sec_tags))[:8]
    except Exception:
        all_tags = ["news"]

    image_fm = ""
    if image_file:
        attr = (image_info.get("attribution") if image_info else "Wikimedia Commons") or ""
        lic  = (image_info.get("license") if image_info else "") or ""
        image_fm = f"""image: "{image_file}"
featured_image: "{image_file}"
feature_image: "{image_file}"
images: ["{image_file}"]
image_attribution: {json.dumps(attr)}
image_license: {json.dumps(lic)}
"""

    return f"""---
title: {json.dumps(display_title)}
seo_title: {json.dumps(seo_title)}
date: {now}
lastmod: {now}
draft: false
featured: false
categories: {categories_raw}
tags: {json.dumps(all_tags)}
description: {json.dumps(meta_desc)}
focus_keyword: {json.dumps(focus_kw)}
{image_fm}---

{body}
"""

def save_hugo_post(article, body, seo):
    niche_dir = CONTENT_DIR / article["niche"]
    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug      = slugify(seo["seo_slug"]) if seo and seo.get("seo_slug") else slugify(article["title"])

    bundle_dir = niche_dir / f"{date_str}-{slug}"
    counter = 1
    while bundle_dir.exists():
        bundle_dir = niche_dir / f"{date_str}-{slug}-{counter}"
        counter += 1
    bundle_dir.mkdir(parents=True, exist_ok=True)

    image_file, image_info = get_feature_image(article, seo, bundle_dir)
    md_content = build_hugo_markdown(article, body, seo, image_file, image_info)

    index_file = bundle_dir / "index.md"
    index_file.write_text(md_content, encoding="utf-8")
    print(f"  💾 Saved Hugo Page Bundle: {index_file}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    sports_only = "--sports-only" in sys.argv

    print(f"\n{'=' * 65}")
    print(f"🚀 Veridus Auto News Poster ({GEMINI_MODEL}){' [SPORTS ONLY]' if sports_only else ''}")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'=' * 65}")

    if not GEMINI_API_KEYS:
        print("❌ No Gemini API keys found. Please define GEMINI_API_KEY_1 in Secrets.")
        return

    posted_log = load_posted_log()
    posted_ids = set(posted_log.keys())
    total_saved = 0

    all_niches    = ["politics", "africa", "business", "sports", "climate", "law", "curious"]
    active_niches = ["sports"] if sports_only else all_niches

    for niche in active_niches:
        print(f"\n📰 [{niche.upper()}]")
        already_today = count_today_posts(niche)
        remaining_today = max(0, DAILY_POST_LIMIT - already_today)
        if remaining_today == 0:
            print(f"   📊 Daily limit reached ({DAILY_POST_LIMIT}/day) — skipping {niche}")
            continue

        articles = fetch_rss_articles(niche, posted_ids)
        articles = score_by_virality(articles)

        limit = min(NICHE_LIMITS.get(niche, 1), remaining_today)
        saved_count = 0

        for article in articles:
            if saved_count >= limit:
                break

            print(f"\n  📝 {article['title'][:70]}")
            body = rewrite_article(article)
            if not body:
                continue

            seo = generate_seo(article, body)
            save_hugo_post(article, body, seo)

            posted_log[article["id"]] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            posted_ids.add(article["id"])
            saved_count += 1
            total_saved += 1
            time.sleep(3)

    save_posted_log(posted_log)
    print(f"\n{'=' * 65}")
    print(f"✨ Complete — {total_saved} articles posted.")
    print(f"{'=' * 65}\n")

if __name__ == "__main__":
    main()