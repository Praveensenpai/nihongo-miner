import asyncio
from rich import print
import hashlib
import json
import pathlib
import re
import urllib.parse
from typing import Any, Dict, List

import httpx
from selectolax.parser import HTMLParser as SelectolaxParser

_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _parse_jpdb_page(html: str) -> List[Dict[str, Any]]:
    """Parse one JPDB vocabulary-list page using Selectolax CSS selectors."""
    tree = SelectolaxParser(html)
    words: List[Dict[str, Any]] = []

    for entry in tree.css("div.entry"):
        a = entry.css_first("div.vocabulary-spelling a")
        if not a:
            continue
        href = a.attributes.get("href", "")
        match = re.match(r"^/vocabulary/\d+/([^#]+)#a$", href)
        if not match:
            continue
        word = urllib.parse.unquote(match.group(1))

        tags = [t.text(strip=True) for t in entry.css("div.tag.tooltip")]

        spelling_div = entry.css_first("div.vocabulary-spelling")
        definition = ""
        if spelling_div and spelling_div.next:
            definition = spelling_div.next.text(strip=True)

        rank_in_deck: int | None = None
        for div in entry.css("div[style]"):
            if "opacity: 0.5" in (div.attributes.get("style") or ""):
                txt = div.text(strip=True)
                if txt.isdigit():
                    rank_in_deck = int(txt)
                break

        words.append({
            "word": word,
            "definition": definition,
            "tags": tags,
            "rank_in_deck": rank_in_deck,
        })

    return words


def _extract_title(html: str) -> str:
    """Extract a human-readable title from JPDB page HTML."""
    tree = SelectolaxParser(html)
    title_node = tree.css_first("title")
    if not title_node:
        return ""
    # Title format: "Higurashi no Naku Koro ni Kira - Episode 1 – Vocabulary list – jpdb"
    raw = title_node.text(strip=True)
    # Strip the " – Vocabulary list – jpdb" suffix
    raw = re.sub(r"\s*–\s*Vocabulary list\s*–\s*jpdb\s*$", "", raw)
    return raw.strip()


async def _fetch_page(client: httpx.AsyncClient, url: str, offset: int, sem: asyncio.Semaphore) -> str | None:
    """Fetch a single page with offset using httpx.AsyncClient."""
    async with sem:
        for attempt in range(3):
            try:
                r = await client.get(url, params={"offset": offset}, timeout=30.0)
                r.raise_for_status()
                return r.text
            except Exception as e:
                if attempt == 2:
                    print(f"[bold yellow]Warning:[/bold yellow] Error fetching JPDB offset {offset} after 3 attempts: [dim]{type(e).__name__}[/dim]: {e}")
                    return None
                await asyncio.sleep(0.5)


async def _fetch_remaining_pages(base_url: str, offsets: List[int]) -> List[str | None]:
    """Fetch remaining offsets concurrently in a single connection pool."""
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(headers=_HEADERS) as client:
        tasks = [_fetch_page(client, base_url, off, sem) for off in offsets]
        return await asyncio.gather(*tasks)


def get_jpdb_cache_path(url: str) -> pathlib.Path:
    cache_dir = pathlib.Path(".jpdb_cache")
    cache_dir.mkdir(exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    base_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    url_hash = hashlib.md5(base_url.encode("utf-8")).hexdigest()
    return cache_dir / f"{url_hash}.json"


def scrape_jpdb(url: str) -> List[Dict[str, Any]]:
    # Normalize base URL (strip query/fragment)
    parsed = urllib.parse.urlparse(url)
    base_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    # Check cache first
    cache_path = get_jpdb_cache_path(base_url)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
                # Support both old bare-list format and new {url, words} format
                words = cached.get("words", cached) if isinstance(cached, dict) else cached
                if isinstance(words, list) and words:
                    print(f" [bold green]->[/bold green] Found cached JPDB list ([bold cyan]{len(words)}[/bold cyan] words). Using cached data.")
                    return words
        except Exception as e:
            print(f"[bold yellow]Warning:[/bold yellow] Failed to read JPDB cache: {e}. Re-downloading...")

    print(f"[bold cyan]Fetching JPDB vocabulary list from {base_url}...[/bold cyan]")

    # Define a temporary helper to run async fetch for page 0
    async def fetch_first_page():
        sem = asyncio.Semaphore(1)
        async with httpx.AsyncClient(headers=_HEADERS) as client:
            return await _fetch_page(client, base_url, 0, sem)

    first_html = asyncio.run(fetch_first_page())
    if not first_html:
        print("[bold red]Error: Failed to fetch first page from JPDB.[/bold red]")
        return []

    title = _extract_title(first_html)
    if title:
        print(f" [bold cyan]->[/bold cyan] [bold]{title}[/bold]")

    total_match = re.search(r"from\s+(\d+)\s+entries", first_html)
    if not total_match:
        print("[bold yellow]Warning:[/bold yellow] Could not parse total entry count from JPDB page.")
        total_entries = 50
    else:
        total_entries = int(total_match.group(1))

    offsets = list(range(0, total_entries, 50))
    total_pages = len(offsets)
    print(f" [bold cyan]->[/bold cyan] Expecting [bold magenta]{total_entries}[/bold magenta] words across [bold magenta]{total_pages}[/bold magenta] pages.")

    all_htmls: List[str | None] = [None] * total_pages
    all_htmls[0] = first_html

    # Fetch remaining pages concurrently
    if total_pages > 1:
        remaining_offsets = offsets[1:]
        remaining_htmls = asyncio.run(_fetch_remaining_pages(base_url, remaining_offsets))
        for i, html in enumerate(remaining_htmls, 1):
            all_htmls[i] = html

    any_failed = any(h is None for h in all_htmls)
    failed_count = sum(1 for h in all_htmls if h is None)

    all_words: List[Dict[str, Any]] = []
    for html in all_htmls:
        if html:
            all_words.extend(_parse_jpdb_page(html))

    parsed_count = len(all_words)

    if any_failed:
        print(f"[bold red]Error: Failed to download {failed_count}/{total_pages} pages. Will NOT cache.[/bold red]")
    else:
        if parsed_count >= total_entries - 5:
            print(f" [bold green]-> Success:[/bold green] parsed [bold green]{parsed_count}/{total_entries}[/bold green] words.")
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"url": base_url, "title": title, "words": all_words}, f, ensure_ascii=False, indent=2)
                print(f" [bold blue]->[/bold blue] Cached to [dim]{cache_path}[/dim]")
            except Exception as e:
                print(f"[bold yellow]Warning:[/bold yellow] Failed to write JPDB cache: {e}")
        else:
            print(f"[bold yellow]Warning:[/bold yellow] Word count mismatch (parsed {parsed_count}/{total_entries}). Will NOT cache.")

    return all_words


def get_jpdb_global_rank(tags: List[str]) -> int:
    for tag in tags:
        if tag.startswith("Top "):
            try:
                return int(tag.replace("Top ", "").replace(",", "").strip())
            except ValueError:
                pass
    return 100000


def list_cached_jpdb() -> List[Dict[str, Any]]:
    """Return metadata for all valid cached JPDB lists."""
    cache_dir = pathlib.Path(".jpdb_cache")
    if not cache_dir.exists():
        return []
    entries = []
    for path in sorted(cache_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Support both old bare-list and new {url, words} format
            if isinstance(data, dict):
                words = data.get("words", [])
                url = data.get("url", str(path))
                title = data.get("title", "")
            else:
                words = data
                url = str(path)
                title = ""
            if isinstance(words, list) and words:
                entries.append({"url": url, "title": title, "count": len(words), "path": path})
        except Exception:
            continue
    return entries
