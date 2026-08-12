#!/usr/bin/env python3
"""
GitHub Trending Daily -> Feishu Push (Webhook version)

Scrapes GitHub trending page, translates descriptions to Chinese,
and sends a card message to Feishu via custom bot webhook.

Runs on GitHub Actions - no PC required, completely free.
"""

import os
import sys
import json
import re
import time
import requests
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
REQUEST_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Step 1: Scrape GitHub Trending
# ---------------------------------------------------------------------------
def fetch_trending():
    """Fetch and parse GitHub trending page. Returns list of repo dicts."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for attempt in range(3):
        try:
            resp = requests.get(GITHUB_TRENDING_URL, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            print(f"  Attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(5)
            else:
                raise

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")

    repos = []
    articles = soup.select("article.Box-row")
    if not articles:
        articles = soup.select("article[data-hpc]")

    for article in articles:
        h2 = article.select_one("h2 a") or article.select_one("h3 a")
        if not h2:
            continue
        href = h2.get("href", "").strip("/")
        if not href:
            continue

        p = article.select_one("p")
        description = p.get_text(strip=True) if p else ""

        lang_span = article.select_one("[itemprop='programmingLanguage']")
        language = lang_span.get_text(strip=True) if lang_span else "N/A"

        total_stars = ""
        for a in article.select("a[href]"):
            href_val = a.get("href", "")
            if "/stargazers" in href_val:
                total_stars = a.get_text(strip=True).replace(",", "")
                break

        today_stars = ""
        for span in article.select("span"):
            text = span.get_text(strip=True)
            if "stars today" in text.lower() or "stars this week" in text.lower():
                today_stars = text
                break
        if not today_stars:
            float_span = article.select_one("span.d-inline-block.float-sm-right")
            if float_span:
                today_stars = float_span.get_text(strip=True)

        repos.append({
            "name": href,
            "description": description,
            "language": language,
            "total_stars": total_stars,
            "today_stars": today_stars,
        })

    return repos


def format_star_count(count_str):
    """Convert raw star count to compact format like '143.4k'."""
    if not count_str:
        return "?"
    try:
        num = int(count_str.replace(",", "").strip())
        if num >= 1000:
            return f"{num / 1000:.1f}k"
        return str(num)
    except (ValueError, TypeError):
        return count_str


def extract_today_star_num(today_str):
    """Extract numeric value from '958 stars today' -> '958'."""
    if not today_str:
        return "?"
    match = re.search(r"([\d,]+)", today_str)
    if match:
        return match.group(1).replace(",", "")
    return today_str


# ---------------------------------------------------------------------------
# Step 2: Translate descriptions to Chinese
# ---------------------------------------------------------------------------
def translate_to_chinese(text):
    """Translate English text to Chinese. Falls back to original on failure."""
    if not text:
        return "暂无描述"
    try:
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source="en", target="zh-CN")
        result = translator.translate(text)
        if result and len(result) > 1:
            return result
        return text
    except Exception as e:
        print(f"  Translation failed, using original: {e}")
        return text


# ---------------------------------------------------------------------------
# Step 3: Build Feishu card message
# ---------------------------------------------------------------------------
def build_card_message(repos, top_n=10):
    """Build Feishu interactive card JSON from repo list."""
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).strftime("%Y年%m月%d日")

    elements = []

    # Date header
    elements.append({
        "tag": "markdown",
        "content": f"**{today}**\n\n---"
    })

    # Top N repos
    for i, repo in enumerate(repos[:top_n], 1):
        desc_zh = translate_to_chinese(repo["description"])
        stars = format_star_count(repo["total_stars"])
        today_num = extract_today_star_num(repo["today_stars"])

        md = (
            f"**{i}. [{repo['name']}](https://github.com/{repo['name']})**  "
            f"⭐ {stars} (+{today_num}📈)\n"
            f"> {desc_zh}\n"
            f"语言: {repo['language']}"
        )
        elements.append({"tag": "markdown", "content": md})
        elements.append({"tag": "hr"})

    # Footer
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text",
             "content": "每日 8:00 自动推送 | 数据来源: GitHub Trending | GitHub Actions 驱动"}
        ]
    })

    card = {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "GitHub 每日热门 Top 10"},
            "template": "blue"
        },
        "elements": elements
    }
    return card


# ---------------------------------------------------------------------------
# Step 4: Send to Feishu via Webhook
# ---------------------------------------------------------------------------
def send_feishu_webhook(card):
    """Send interactive card message to Feishu via custom bot webhook."""
    payload = {
        "msg_type": "interactive",
        "card": card
    }

    resp = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    result = resp.json()

    if result.get("code") != 0 and result.get("StatusCode") != 0:
        # Some webhook responses use different field names
        if result.get("code") is None and result.get("StatusCode") is None:
            # If no error code at all, assume success
            print(f"  Response: {result}")
            return result
        raise RuntimeError(f"Feishu webhook send failed: {result}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 50)
    print("GitHub Trending -> Feishu Push (Webhook)")
    print("=" * 50)

    # Validate config
    if not FEISHU_WEBHOOK_URL:
        print("ERROR: FEISHU_WEBHOOK_URL is not set!")
        sys.exit(1)

    # Step 1: Fetch trending
    print("\n[1/4] Fetching GitHub Trending...")
    repos = fetch_trending()
    print(f"  Found {len(repos)} repos")
    if not repos:
        print("ERROR: No repos found. Trending page may have changed.")
        sys.exit(1)

    # Step 2: Translate
    print("\n[2/4] Translating descriptions to Chinese...")
    print(f"  Processing top {min(10, len(repos))} repos...")

    # Step 3: Build card
    print("\n[3/4] Building Feishu card message...")
    card = build_card_message(repos, top_n=10)
    print(f"  Card has {len(card['elements'])} elements")

    # Step 4: Send via webhook
    print("\n[4/4] Sending to Feishu via webhook...")
    result = send_feishu_webhook(card)
    print(f"  Response: {result}")

    print("\n" + "=" * 50)
    print("Done! Message sent successfully.")
    print("=" * 50)


if __name__ == "__main__":
    main()
