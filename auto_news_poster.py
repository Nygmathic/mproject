import os
import sys
import time
import json
import re
import requests
import feedparser
from datetime import datetime, timezone

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

# Active Gemini models (Gemini 1.5 and 2.5 deprecated)
GEMINI_MODELS = ["gemini-3.6-flash"]

# Collect all available Gemini API keys from environment (GEMINI_API_KEY_1 to 6)
GEMINI_API_KEYS = [
    os.environ.get(f"GEMINI_API_KEY_{i}")
    for i in range(1, 7)
    if os.environ.get(f"GEMINI_API_KEY_{i}")
]

POSTED_LOG_FILE = ".posted_articles.json"
DAILY_POST_LIMIT_PER_NICHE = 1

RSS_FEEDS = {
    "politics": [
        "https://news.google.com/rss/search?q=politics&hl=en-US&gl=US&ceid=US:en"
    ],
    "africa": [
        "https://news.google.com/rss/search?q=africa+news&hl=en-US&gl=US&ceid=US:en"
    ],
    "business": [
        "https://news.google.com/rss/search?q=business+finance&hl=en-US&gl=US&ceid=US:en"
    ],
    "sports": [
        "https://news.google.com/rss/search?q=sports+news&hl=en-US&gl=US&ceid=US:en"
    ],
    "climate": [
        "https://news.google.com/rss/search?q=climate+change&hl=en-US&gl=US&ceid=US:en"
    ],
    "law": [
        "https://news.google.com/rss/search?q=legal+court+news&hl=en-US&gl=US&ceid=US:en"
    ],
    "curious": [
        "https://news.google.com/rss/search?q=science+technology+discovery&hl=en-US&gl=US&ceid=US:en"
    ],
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def load_posted_log():
    if os.path.exists(POSTED_LOG_FILE):
        try:
            with open(POSTED_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Warning loading log: {e}")
    return {}

def save_posted_log(posted_log):
    try:
        with open(POSTED_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(posted_log, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving log: {e}")

def slugify(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    return text[:60] if text else "news-update"

def clean_json_string(text):
    if not text:
        return "{}"
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()

# ─── RSS FETCHING ─────────────────────────────────────────────────────────────

def fetch_rss_articles(niche, posted_ids):
    articles = []
    urls = RSS_FEEDS.get(niche, [])
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                article_id = entry.get("id", entry.get("link", entry.get("title")))
                if article_id in posted_ids:
                    continue
                articles.append({
                    "id": article_id,
                    "title": entry.get("title", "Untitled"),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", entry.get("description", "")),
                    "niche": niche
                })
        except Exception as e:
            print(f"⚠️ RSS fetch error ({niche}): {e}")
    return articles

# ─── GEMINI LLM CALLS ─────────────────────────────────────────────────────────

def call_gemini(prompt, max_tokens=2500):
    if not GEMINI_API_KEYS:
        print("❌ No GEMINI_API_KEY variables set.")
        return None

    for model in GEMINI_MODELS:
        for idx, key in enumerate(GEMINI_API_KEYS):
            url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model}:generateContent"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": max_tokens
                }
            }
            try:
                resp = requests.post(
                    url,
                    params={"key": key},
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates and "content" in candidates[0]:
                        parts = candidates[0]["content"].get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"].strip()
                elif resp.status_code in (429, 503):
                    print(f"  ⚠️ Key {idx+1} on {model} rate limited/busy ({resp.status_code}). Trying next...")
                    time.sleep(2)
                else:
                    print(f"  ⚠️ Gemini API error {resp.status_code}: {resp.text[:150]}")
            except Exception as e:
                print(f"  ⚠️ Request error: {e}")

    return None

# ─── CONTENT GENERATION ───────────────────────────────────────────────────────

def rewrite_article(article):
    prompt = (
        f"You are an investigative news journalist.\n"
        f"Rewrite the following news snippet into an original, engaging news article (300 to 450 words).\n"
        f"Do NOT include a title/heading inside your response text.\n\n"
        f"Original Title: {article['title']}\n"
        f"Context/Summary: {article['summary']}\n"
        f"Source: {article['link']}\n"
    )
    return call_gemini(prompt, max_tokens=2500)

def generate_metadata(article, body):
    prompt = (
        f"Generate JSON metadata for a news post. Return ONLY valid JSON with keys: 'title', 'description', 'keywords'.\n"
        f"'keywords' must be an array of string tags.\n\n"
        f"Original Title: {article['title']}\n"
        f"Body Excerpt: {body[:300]}\n"
    )
    res = call_gemini(prompt, max_tokens=400)
    if res:
        try:
            return json.loads(clean_json_string(res))
        except Exception:
            pass
    
    return {
        "title": article["title"],
        "description": article["title"],
        "keywords": [article["niche"], "news"]
    }

def save_hugo_post(article, body, metadata):
    niche_dir = f"content/posts/{article['niche']}"
    os.makedirs(niche_dir, exist_ok=True)

    title = metadata.get("title", article["title"]).replace('"', '\\"')
    description = metadata.get("description", article["title"]).replace('"', '\\"')
    keywords = metadata.get("keywords", [article["niche"]])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]

    slug = slugify(metadata.get("title", article["title"]))
    filepath = f"{niche_dir}/{slug}.md"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    content = f"""---
title: "{title}"
date: {now_utc}
draft: false
description: "{description}"
categories: ["{article['niche'].capitalize()}"]
tags: {json.dumps(keywords)}
---

{body}
"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  💾 Saved post: {filepath}")

# ─── MAIN ROUTINE ─────────────────────────────────────────────────────────────

def main():
    sports_only = "--sports-only" in sys.argv

    print(f"\n==================================================")
    print(f"🚀 Auto News Poster starting...")
    print(f"   Model: {GEMINI_MODELS[0]}")
    print(f"   Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Mode: {'Sports Only' if sports_only else 'All Niches'}")
    print(f"==================================================\n")

    if not GEMINI_API_KEYS:
        print("⚠️ No Gemini API keys found in environment. Exiting safely.")
        sys.exit(0)

    posted_log = load_posted_log()
    posted_ids = set(posted_log.keys())

    all_niches = ["politics", "africa", "business", "sports", "climate", "law", "curious"]
    target_niches = ["sports"] if sports_only else all_niches
    total_posted = 0

    for niche in target_niches:
        print(f"📰 Processing niche: [{niche.upper()}]")
        articles = fetch_rss_articles(niche, posted_ids)

        if not articles:
            print(f"   No new unposted articles found for {niche}.")
            continue

        posted_in_niche = 0
        for article in articles:
            if posted_in_niche >= DAILY_POST_LIMIT_PER_NICHE:
                break

            print(f"  📝 Processing: {article['title'][:65]}...")
            body = rewrite_article(article)

            if not body:
                print(f"  ⚠️ Could not generate content. Skipping.")
                continue

            metadata = generate_metadata(article, body)
            save_hugo_post(article, body, metadata)

            posted_log[article["id"]] = {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "niche": niche
            }
            posted_ids.add(article["id"])
            posted_in_niche += 1
            total_posted += 1
            time.sleep(2)

    save_posted_log(posted_log)
    print(f"\n==================================================")
    print(f"✨ Run complete. Posted {total_posted} articles.")
    print(f"==================================================\n")

if __name__ == "__main__":
    main()