#!/usr/bin/env python3
"""
GitHub Trending Daily -> Feishu Push (Webhook + OpenRouter AI version)

Scrapes GitHub trending page, picks top 5 repos, uses OpenRouter AI (Gemini)
to generate easy-to-understand Chinese explanations with use cases, and sends
a rich card message to Feishu via custom bot webhook.

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
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
GITHUB_API = "https://api.github.com"
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS = [
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
    "openai/gpt-oss-20b:free",
]
REQUEST_TIMEOUT = 30
AI_TIMEOUT = 45  # shorter timeout for AI calls
TOP_N = 5


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
# Step 2: Fetch README for each repo
# ---------------------------------------------------------------------------
def fetch_readme(repo_name):
    """Fetch README content for a repo via GitHub API."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "github-trending-daily",
    }
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo_name}/readme",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            import base64
            content = resp.json().get("content", "")
            if content:
                decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                # Truncate to first 3000 chars to keep prompt size reasonable
                return decoded[:3000]
    except Exception as e:
        print(f"  README fetch failed for {repo_name}: {e}")
    return ""


# ---------------------------------------------------------------------------
# Step 3: Use OpenRouter AI to generate Chinese explanation
# ---------------------------------------------------------------------------
def generate_explanation(repo):
    """Use OpenRouter AI to generate easy-to-understand Chinese explanation.
    Tries multiple free models with fallback."""
    readme = fetch_readme(repo["name"])

    prompt = f"""你是一个技术科普作者，擅长用大白话解释技术项目。请分析以下GitHub项目，用通俗易懂的中文生成解读。

项目名: {repo['name']}
描述: {repo['description']}
编程语言: {repo['language']}
README摘要:
{readme[:2000]}

请严格按照以下JSON格式输出（不要输出其他内容，不要用markdown代码块包裹）:
{{
  "一句话简介": "用15-25个字概括这个项目是干什么的，让非技术人员也能听懂",
  "详细解释": "用2-3句话解释这个项目的核心功能和价值，用大白话，不要用专业术语",
  "应用场景": "列出2-3个具体的使用场景，每个场景一行，说明什么人会在什么情况下用它",
  "怎么用": "用1-2句话说明怎么上手使用，比如安装方式或访问方式",
  "适合人群": "用一句话说明这个项目适合什么人"
}}"""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/zhiguangzhang948-eng/github-trending-daily",
        "X-Title": "GitHub Trending Daily",
    }

    for model in OPENROUTER_MODELS:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一个技术科普作者，擅长用通俗易懂的中文解释技术项目。只输出JSON，不输出其他内容。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 2000,  # increased for reasoning models
        }

        try:
            resp = requests.post(
                OPENROUTER_API,
                headers=headers,
                json=payload,
                timeout=AI_TIMEOUT,
            )
            if resp.status_code == 200:
                msg = resp.json()["choices"][0]["message"]
                text = msg.get("content") or msg.get("reasoning") or ""
                if not text:
                    print(f"  {model} returned empty content, trying next model...")
                    continue
                # Clean up: remove markdown code block if present
                text = text.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```(?:json)?\s*", "", text)
                    text = re.sub(r"\s*```$", "", text)
                # Try to extract JSON from the text (in case reasoning mixed in)
                json_match = re.search(r'\{[\s\S]*\}', text)
                if json_match:
                    return json.loads(json_match.group())
                return json.loads(text)
            elif resp.status_code == 429:
                print(f"  {model} rate-limited, trying next model...")
                continue  # Try next model
            else:
                print(f"  {model} error {resp.status_code}: {resp.text[:150]}")
                continue  # Try next model
        except json.JSONDecodeError as e:
            print(f"  {model} JSON parse failed: {e}")
            continue  # Try next model
        except Exception as e:
            print(f"  {model} failed: {e}")
            continue  # Try next model

    # Fallback: return basic info
    return {
        "一句话简介": repo["description"] or "暂无描述",
        "详细解释": "无法获取AI解读，请访问项目页面了解更多。",
        "应用场景": "请访问项目主页查看。",
        "怎么用": "请访问项目主页查看使用文档。",
        "适合人群": "对相关技术感兴趣的开发者。",
    }


# ---------------------------------------------------------------------------
# Step 4: Build Feishu card message
# ---------------------------------------------------------------------------
def build_card_message(repos):
    """Build Feishu interactive card JSON with rich AI-generated content."""
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).strftime("%Y年%m月%d日")

    elements = []

    # Date header
    elements.append({
        "tag": "markdown",
        "content": f"**{today}** | 精选 {TOP_N} 个最值得关注的项目\n\n---"
    })

    # Top N repos with AI explanations
    for i, repo in enumerate(repos[:TOP_N], 1):
        stars = format_star_count(repo["total_stars"])
        today_num = extract_today_star_num(repo["today_stars"])
        info = repo.get("ai_explanation", {})

        # Project title line
        md = f"**{i}. [{repo['name']}](https://github.com/{repo['name']})**"
        md += f"  |  ⭐ {stars} (+{today_num}今日)\n"

        # One-line summary (highlighted)
        one_line = info.get("一句话简介", "")
        md += f"\n📌 **{one_line}**\n"

        # Detailed explanation
        detail = info.get("详细解释", "")
        if detail:
            md += f"\n{detail}\n"

        # Application scenarios
        scenarios = info.get("应用场景", "")
        if scenarios:
            md += f"\n🎯 **应用场景**\n{scenarios}\n"

        # How to use
        how_to = info.get("怎么用", "")
        if how_to:
            md += f"\n🚀 **怎么用**\n{how_to}\n"

        # Target audience
        audience = info.get("适合人群", "")
        if audience:
            md += f"\n👤 适合: {audience}\n"

        # Language
        md += f"\n🔧 语言: {repo['language']}"

        elements.append({"tag": "markdown", "content": md})
        elements.append({"tag": "hr"})

    # Footer
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text",
             "content": "GitHub 每日精选 | AI 深度解读 | 每天推送 5 个优质项目 | 数据来源: GitHub Trending"}
        ]
    })

    card = {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "GitHub 每日精选 Top 5 | AI 深度解读"},
            "template": "blue"
        },
        "elements": elements
    }
    return card


# ---------------------------------------------------------------------------
# Step 5: Send to Feishu via Webhook
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
        if result.get("code") is None and result.get("StatusCode") is None:
            print(f"  Response: {result}")
            return result
        raise RuntimeError(f"Feishu webhook send failed: {result}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def already_sent_today():
    """Check if a scheduled run already succeeded or is in progress today.
    Prevents duplicate sends when multiple cron triggers fire.
    Uses public API (no auth needed for public repos, 60 req/hr limit is enough)."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-trending-daily",
    }
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(
            "https://api.github.com/repos/zhiguangzhang948-eng/github-trending-daily/actions/runs?per_page=20",
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            return False
        runs = resp.json().get("workflow_runs", [])
        tz = timezone(timedelta(hours=8))
        today_str = datetime.now(tz).strftime("%Y-%m-%d")
        current_run_id = os.environ.get("GITHUB_RUN_ID", "")
        for run in runs:
            # Skip the current run - it's always in_progress while we're executing
            run_id = str(run.get("id", ""))
            if current_run_id and run_id == current_run_id:
                continue
            event = run.get("event")
            if event not in ("schedule", "workflow_dispatch"):
                continue
            created = run.get("created_at", "")
            if not created:
                continue
            run_time = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(tz)
            if run_time.strftime("%Y-%m-%d") != today_str:
                continue
            status = run.get("status")
            conclusion = run.get("conclusion")
            if status == "completed" and conclusion == "success":
                print("  Already sent successfully today via previous run, skipping.")
                return True
        return False
    except Exception as e:
        print(f"  Dedup check failed (non-fatal): {e}")
        return False


def main():
    print("=" * 50)
    print("GitHub Trending -> Feishu Push (AI Enhanced)")
    print("=" * 50)

    if not FEISHU_WEBHOOK_URL:
        print("ERROR: FEISHU_WEBHOOK_URL is not set!")
        sys.exit(1)
    if not OPENROUTER_API_KEY:
        print("ERROR: OPENROUTER_API_KEY is not set!")
        sys.exit(1)

    # Dedup: skip if already sent today via scheduled run
    if already_sent_today():
        print("Exiting: already handled today.")
        sys.exit(0)

    # Step 1: Fetch trending
    print(f"\n[1/4] Fetching GitHub Trending...")
    repos = fetch_trending()
    print(f"  Found {len(repos)} repos")
    if not repos:
        print("ERROR: No repos found. Trending page may have changed.")
        sys.exit(1)

    # Step 2: Select top 5 and generate AI explanations
    print(f"\n[2/4] Generating AI explanations for top {TOP_N} repos...")
    for i, repo in enumerate(repos[:TOP_N]):
        print(f"  [{i+1}/{TOP_N}] {repo['name']}...")
        repo["ai_explanation"] = generate_explanation(repo)
        # Small delay to avoid rate limiting
        if i < TOP_N - 1:
            time.sleep(1)

    # Step 3: Build Feishu card
    print(f"\n[3/4] Building Feishu card message...")
    card = build_card_message(repos)
    print(f"  Card has {len(card['elements'])} elements")

    # Step 4: Send via webhook
    print(f"\n[4/4] Sending to Feishu via webhook...")
    result = send_feishu_webhook(card)
    print(f"  Response: {result}")

    print("\n" + "=" * 50)
    print("Done! Message sent successfully.")
    print("=" * 50)


if __name__ == "__main__":
    main()
