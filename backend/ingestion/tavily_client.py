import os
import re
import httpx
from typing import List, Dict


class TavilyClient:
    """Minimal Tavily search client + simple public-profile fetch helpers.

    Expects an API that returns JSON like: {"results": [{"url":...,"title":...,"snippet":...}, ...]}
    Configure TAVILY_API_URL and TAVILY_API_KEY in environment for real service.
    """

    def __init__(self, api_url: str | None = None, api_key: str | None = None):
        self.api_url = api_url or os.getenv("TAVILY_API_URL", "https://api.tavily.example")
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")

    async def search_profiles(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        params = {"q": query, "max": max_results}
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{self.api_url}/search", params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        results = []
        for item in data.get("results", [])[:max_results]:
            results.append({
                "url": item.get("url"),
                "title": item.get("title"),
                "snippet": item.get("snippet"),
            })
        return results

    async def fetch_public_profile_html(self, url: str) -> str:
        """Fetch a public profile. Use httpx GET; if page looks minimal or blocked, return the httpx result.
        For richer extraction use fetch_and_extract_linkedin which renders and extracts text via Playwright.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def fetch_and_extract_linkedin(self, url: str, limit: int = 10) -> List[str]:
        """Use Playwright to render the LinkedIn profile page, scroll to load posts, and extract text blocks from common selectors.
        Returns list of extracted strings (may be empty).
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        }
        # Try Playwright first for robust rendering
        try:
            from playwright.async_api import async_playwright
        except Exception:
            async_text = await self.fetch_public_profile_html(url)
            return self.extract_paragraphs(async_text, url=url, limit=limit)

        out: List[str] = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=headers["User-Agent"], java_script_enabled=True)
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle")
                # scroll gradually to load posts
                for _ in range(6):
                    await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
                    try:
                        await page.wait_for_timeout(800)
                    except Exception:
                        pass
                # selectors likely to contain post text
                selectors = [
                    '.feed-shared-update-v2__description',
                    '.feed-shared-text',
                    '.feed-shared-actor__sub-description',
                    '.break-words',
                    '.update-components-text',
                    '.occludable-update',
                    '[data-urn]',
                    '.feed-shared-inline-show-more-text__text--more',
                ]
                seen = set()
                for sel in selectors:
                    try:
                        handles = await page.query_selector_all(sel)
                    except Exception:
                        handles = []
                    for h in handles:
                        try:
                            txt = (await h.inner_text()).strip()
                        except Exception:
                            try:
                                txt = (await h.text_content()) or ""
                                txt = txt.strip()
                            except Exception:
                                txt = ""
                        if txt:
                            # normalize whitespace
                            t2 = re.sub(r"\s+", " ", txt).strip()
                            if t2 and t2 not in seen:
                                seen.add(t2)
                                out.append(t2)
                                if len(out) >= limit:
                                    await browser.close()
                                    return out
                # final fallback: page content parsed by BeautifulSoup extractor
                content = await page.content()
                await browser.close()
                more = self.extract_paragraphs(content, url=url, limit=limit - len(out))
                for m in more:
                    if m not in seen:
                        out.append(m)
                        if len(out) >= limit:
                            break
        except Exception as exc:
            # If Playwright fails, fallback to httpx extraction
            try:
                text = await self.fetch_public_profile_html(url)
                return self.extract_paragraphs(text, url=url, limit=limit)
            except Exception:
                return []
        return out

    def extract_paragraphs(self, html: str, limit: int = 5) -> List[str]:
        """Use BeautifulSoup if available for robust DOM parsing, otherwise fall back to heuristics.
        Returns up to `limit` distinct text blocks.
        """
        import json as _json
        import html as _html

        cleaned: List[str] = []

        # Prefer BeautifulSoup when available for more robust parsing
        try:
            from bs4 import BeautifulSoup
        except Exception:
            BeautifulSoup = None

        if BeautifulSoup:
            soup = BeautifulSoup(html, "html.parser")

            # 1) JSON-LD structured data
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = _json.loads(script.string or "")
                except Exception:
                    continue

                texts: List[str] = []

                def _walk(obj):
                    if isinstance(obj, str):
                        texts.append(obj)
                    elif isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("articleBody", "description", "headline", "text") and isinstance(v, str):
                                texts.append(v)
                            else:
                                _walk(v)
                    elif isinstance(obj, list):
                        for it in obj:
                            _walk(it)

                _walk(data)
                for t in texts:
                    t2 = re.sub(r"<[^>]+>", "", t).strip()
                    t2 = _html.unescape(t2)
                    if t2:
                        cleaned.append(t2)
                    if len(cleaned) >= limit:
                        return cleaned

            # 2) meta description
            meta = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                v = _html.unescape(meta.get("content").strip())
                if v:
                    cleaned.append(v)
                    if len(cleaned) >= limit:
                        return cleaned

            # 3) article elements
            for article in soup.find_all("article"):
                text = article.get_text(separator="\n").strip()
                if text:
                    for part in [s.strip() for s in re.split(r"\n+", text) if s.strip()]:
                        cleaned.append(part)
                        if len(cleaned) >= limit:
                            return cleaned

            # 4) elements with likely post classes
            keywords = ["update", "feed", "post", "share", "comment", "activity", "article"]
            for el in soup.find_all(True, class_=lambda c: c and any(k in c.lower() for k in keywords)):
                t = el.get_text(" ", strip=True)
                if t:
                    cleaned.append(t)
                    if len(cleaned) >= limit:
                        return cleaned

            # 5) fallback: <p> tags
            for p in soup.find_all("p"):
                t = p.get_text(" ", strip=True)
                if t:
                    cleaned.append(t)
                if len(cleaned) >= limit:
                    break

        else:
            # Fallback to original heuristics if BeautifulSoup not available
            # 1) structured data (JSON-LD)
            for m in re.finditer(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", html, re.I | re.S):
                try:
                    data = _json.loads(m.group(1))
                except Exception:
                    continue

                texts: List[str] = []

                def _walk(obj):
                    if isinstance(obj, str):
                        texts.append(obj)
                    elif isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("articleBody", "description", "headline", "text") and isinstance(v, str):
                                texts.append(v)
                            else:
                                _walk(v)
                    elif isinstance(obj, list):
                        for it in obj:
                            _walk(it)

                _walk(data)

                for t in texts:
                    t2 = re.sub(r"<[^>]+>", "", t).strip()
                    t2 = _html.unescape(t2)
                    if t2:
                        cleaned.append(t2)
                    if len(cleaned) >= limit:
                        return cleaned

            # 2) meta description (og:description)
            m = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', html, re.I | re.S)
            if m:
                v = _html.unescape(m.group(1).strip())
                if v:
                    cleaned.append(v)
                    if len(cleaned) >= limit:
                        return cleaned

            # 3) role="article" blocks
            for m in re.finditer(r'<(article|div)[^>]*role=["\']article["\'][^>]*>(.*?)</\1>', html, re.I | re.S):
                content = m.group(2)
                t = re.sub(r"<[^>]+>", "", content).strip()
                t = _html.unescape(t)
                if t:
                    for part in [s.strip() for s in re.split(r"\n+", t) if s.strip()]:
                        cleaned.append(part)
                        if len(cleaned) >= limit:
                            return cleaned

            # 4) divs with likely post classes
            for m in re.finditer(r'<div[^>]*class=["\']([^"\']*(?:update|feed|post|share)[^"\']*)["\'][^>]*>(.*?)</div>', html, re.I | re.S):
                content = m.group(2)
                t = re.sub(r"<[^>]+>", "", content).strip()
                t = _html.unescape(t)
                if t:
                    cleaned.append(t)
                    if len(cleaned) >= limit:
                        return cleaned

            # 5) fallback: <p> tags
            ps = re.findall(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)
            for p in ps:
                t = re.sub(r"<[^>]+>", "", p).strip()
                t = _html.unescape(t)
                t = re.sub(r"\s+", " ", t)
                if t:
                    cleaned.append(t)
                if len(cleaned) >= limit:
                    break

        # Deduplicate while preserving order
        seen = set()
        out: List[str] = []
        for t in cleaned:
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
            if len(out) >= limit:
                break
        return out
