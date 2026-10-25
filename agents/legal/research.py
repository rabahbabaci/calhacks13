"""Web research for the legal agent.

search(query)  -> Google search + page scrape through Apify's RAG Web Browser actor
read(url)      -> one page through the same actor (PDFs are downloaded directly)
opinions(q)    -> CourtListener opinion search (only when COURTLISTENER_TOKEN_Legal is set)

Every page fetched is kept in `pages` (url -> text) so the validator can check
that a quoted excerpt really appears in what was read.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

RAG_ACTOR = "apify/rag-web-browser"
MAX_PAGE_CHARS = 8000
MAX_PDF_BYTES = 4 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (habitability legal research)"


class Research:
    def __init__(self, apify_token, courtlistener_token=None):
        from apify_client import ApifyClient

        self.apify = ApifyClient(apify_token)
        self.courtlistener_token = courtlistener_token
        self.pages = {}  # url -> full text (None for PDFs, which go to the model as documents)
        self._cache = {}

    def _rag(self, query, max_results, scraping_tool="raw-http"):
        key = (query, max_results, scraping_tool)
        if key in self._cache:
            return self._cache[key]
        run = self.apify.actor(RAG_ACTOR).call(
            run_input={
                "query": query,
                "maxResults": max_results,
                "outputFormats": ["markdown"],
                "scrapingTool": scraping_tool,
                "requestTimeoutSecs": 60,
            },
            wait_duration=timedelta(seconds=180),
            logger=None,
        )
        if run is None or "SUCCEEDED" not in str(run.status):
            raise RuntimeError(f"Apify run ended {run.status if run else 'without a run'}")
        items = list(self.apify.dataset(run.default_dataset_id).iterate_items())
        pages = []
        for item in items:
            text = item.get("markdown") or ""
            status = (item.get("crawl") or {}).get("httpStatusCode") or 200
            if not text or status >= 400:
                continue
            url = (item.get("metadata") or {}).get("url") or (item.get("searchResult") or {}).get("url") or ""
            title = (item.get("metadata") or {}).get("title") or (item.get("searchResult") or {}).get("title") or ""
            self.pages[url] = text
            pages.append({"url": url, "title": title, "text": text[:MAX_PAGE_CHARS]})
        self._cache[key] = pages
        return pages

    def search(self, query, max_results=3):
        return self._rag(query, max_results)

    def read(self, url):
        """Returns ("pages", [...]) or ("pdf", base64_string)."""
        pdf = self._fetch_pdf(url)
        if pdf is not None:
            self.pages[url] = None
            return "pdf", pdf
        pages = self._rag(url, 1)
        if not pages:
            pages = self._rag(url, 1, "browser-playwright")
        return "pages", pages

    def _fetch_pdf(self, url):
        import base64

        looks_pdf = bool(re.search(r"\.pdf($|[?#])", url, re.I))
        req = urllib.request.Request(url, method="GET" if looks_pdf else "HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if "application/pdf" not in (resp.headers.get("Content-Type") or ""):
                    return None
                if not looks_pdf:
                    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as full:
                        data = full.read(MAX_PDF_BYTES + 1)
                else:
                    data = resp.read(MAX_PDF_BYTES + 1)
        except (urllib.error.URLError, ValueError, TimeoutError):
            return None
        if len(data) > MAX_PDF_BYTES:
            raise RuntimeError("PDF is over 4 MB; find an HTML version or a smaller excerpt")
        return base64.b64encode(data).decode("ascii")

    @property
    def has_courtlistener(self):
        return bool(self.courtlistener_token)

    def opinions(self, query, court=None):
        params = {"q": query, "type": "o", "order_by": "score desc"}
        if court:
            params["court"] = court
        req = urllib.request.Request(
            "https://www.courtlistener.com/api/rest/v4/search/?" + urllib.parse.urlencode(params),
            headers={"Authorization": f"Token {self.courtlistener_token}", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        out = []
        for r in body.get("results", [])[:8]:
            url = "https://www.courtlistener.com" + (r.get("absolute_url") or "")
            text = "\n".join(
                [f"Citations: {'; '.join(r.get('citation') or [])}"] + [o.get("snippet") or "" for o in r.get("opinions") or []]
            )
            self.pages[url] = text
            out.append({
                "url": url,
                "title": f"{r.get('caseName', 'Unknown case')} ({r.get('court', '?')}, {(r.get('dateFiled') or '?')[:4]})",
                "text": text,
            })
        return out


def format_pages(pages):
    """Wrap scraped pages so the model treats them as quoted material, not instructions."""
    if not pages:
        return "No pages returned (blocked or empty). Try another copy, e.g. the official site or law.justia.com."
    return "\n\n".join(
        f'<page url="{p["url"]}" title="{p["title"].replace(chr(34), chr(39))}">\n{p["text"]}\n</page>' for p in pages
    )
