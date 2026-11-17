from __future__ import annotations

import ipaddress
import os
import socket
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from urllib.request import getproxies

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_BYTES = 2_000_000
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
CHALLENGE_MARKERS = (
    "captcha",
    "verify you are human",
    "checking your browser",
    "enable javascript and cookies to continue",
    "cloudflare ray id",
    "attention required! | cloudflare",
)


class FetchError(RuntimeError):
    """A fetch failure with a stable category and optional HTTP status."""

    def __init__(self, message: str, *, category: str, status_code: int | None = None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("Localhost URLs are blocked")
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                host, parsed.port or (443 if parsed.scheme == "https" else 80)
            )
        }
    except OSError as exc:
        # With an explicit proxy, DNS may be resolved by the proxy itself.
        if getproxies():
            return url
        raise ValueError(f"Could not resolve host: {host}") from exc
    if not addresses:
        raise ValueError("Host did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise ValueError("Private, local or reserved network addresses are blocked")
    return url


def _retry_delay(response, attempt: int) -> float:
    value = response.headers.get("Retry-After")
    if value:
        try:
            return max(0.0, min(float(value), 15.0))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, min((retry_at - datetime.now(timezone.utc)).total_seconds(), 15.0))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(0.75 * (2**attempt), 3.0)


def fetch_html(
    url: str,
    timeout: int = 12,
    max_redirects: int = 3,
    max_attempts: int = 2,
) -> tuple[str, str]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Thiếu requests. Cài bằng: pip install -e '.[research]'") from exc

    current = url
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.8,vi;q=0.6",
    }
    with requests.Session() as session:
        for _ in range(max_redirects + 1):
            validate_public_url(current)
            response = None
            for attempt in range(max(1, max_attempts)):
                try:
                    response = session.get(
                        current,
                        headers=headers,
                        timeout=timeout,
                        stream=True,
                        allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    if attempt + 1 >= max_attempts:
                        raise FetchError(
                            f"Network request failed: {exc}", category="network_error"
                        ) from exc
                    time.sleep(min(0.75 * (2**attempt), 3.0))
                    continue

                if response.status_code in RETRYABLE_STATUS_CODES:
                    status = response.status_code
                    delay = _retry_delay(response, attempt)
                    response.close()
                    if attempt + 1 < max_attempts:
                        time.sleep(delay)
                        continue
                    category = "rate_limited" if status == 429 else "temporary_http_error"
                    raise FetchError(
                        f"HTTP {status} after {max_attempts} attempts",
                        category=category,
                        status_code=status,
                    )
                if response.status_code in {401, 403}:
                    status = response.status_code
                    response.close()
                    raise FetchError(
                        f"HTTP {status}: access denied; will not bypass site restrictions",
                        category="access_denied",
                        status_code=status,
                    )
                if response.status_code == 404 or response.status_code == 410:
                    status = response.status_code
                    response.close()
                    raise FetchError(
                        f"HTTP {status}: page not found or removed",
                        category="not_found",
                        status_code=status,
                    )
                try:
                    response.raise_for_status()
                except requests.HTTPError as exc:
                    status = response.status_code
                    response.close()
                    raise FetchError(
                        f"HTTP {status}: {exc}", category="http_error", status_code=status
                    ) from exc
                break

            if response is None:
                raise FetchError("No HTTP response received", category="network_error")

            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise FetchError("Redirect without Location header", category="redirect_error")
                current = urljoin(current, location)
                continue

            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not any(
                item in content_type
                for item in ("text/html", "application/xhtml", "text/plain")
            ):
                response.close()
                raise FetchError(
                    f"Unsupported content type: {content_type}", category="unsupported_content"
                )

            chunks, size = [], 0
            try:
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchError("Page exceeds 2 MB limit", category="size_limit")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                return b"".join(chunks).decode(encoding, errors="replace"), current
            finally:
                response.close()

    raise FetchError("Too many redirects", category="redirect_error")


def extract_text(html: str) -> str:
    lowered = html.lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise FetchError(
            "Access challenge detected (CAPTCHA/anti-bot page); skipping this source",
            category="access_challenge",
        )
    try:
        import trafilatura

        text = trafilatura.extract(
            html, include_comments=False, include_tables=False, favor_precision=True
        )
        if text:
            return text.strip()
    except ImportError:
        pass
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "aside"]):
        node.decompose()
    return "\n".join(s.strip() for s in soup.get_text("\n").splitlines() if s.strip())


def _fetch_with_browser(url: str, timeout: int = 15) -> tuple[str, str]:
    """Render a public JS page; this is not a CAPTCHA or access-control bypass."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install with: pip install -e '.[research-browser]' "
            "and run: playwright install chromium"
        ) from exc

    validate_public_url(url)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT, locale="en-US")

            def guard_route(route):
                try:
                    validate_public_url(route.request.url)
                except (ValueError, OSError):
                    route.abort()
                    return
                route.continue_()

            page.route("**/*", guard_route)
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            if response and response.status in {401, 403, 429}:
                raise FetchError(
                    f"Browser received HTTP {response.status}; access restriction respected",
                    category="access_denied" if response.status in {401, 403} else "rate_limited",
                    status_code=response.status,
                )
            page.wait_for_timeout(1200)
            html = page.content()
            if len(html.encode("utf-8", errors="ignore")) > MAX_BYTES:
                raise FetchError("Rendered page exceeds 2 MB limit", category="size_limit")
            text = extract_text(html)
            if len(text) < 200:
                raise FetchError("Browser rendered page still has insufficient text", category="thin_content")
            return text, page.url
        finally:
            browser.close()


def fetch_article(url: str) -> tuple[str, str]:
    html, final_url = fetch_html(url)
    text = extract_text(html)
    if len(text) >= 200 or os.getenv("HUB_RESEARCH_BROWSER_FALLBACK", "true").lower() not in {
        "1", "true", "yes", "on"
    }:
        return text, final_url

    # Use a real browser only for a successful but JS-rendered/thin page.
    # If Playwright is unavailable or rendering fails, preserve the HTTP result
    # so the pipeline can still use the search snippet.
    try:
        browser_text, browser_url = _fetch_with_browser(final_url)
        return browser_text, browser_url
    except FetchError as exc:
        if exc.category in {"access_denied", "rate_limited", "access_challenge"}:
            raise
        return text, final_url
    except Exception:
        return text, final_url
