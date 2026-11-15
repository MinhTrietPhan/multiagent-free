from __future__ import annotations
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

USER_AGENT = "AgentHubResearch/0.1 (+local research client)"
MAX_BYTES = 2_000_000


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
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except OSError as exc:
        raise ValueError(f"Could not resolve host: {host}") from exc
    if not addresses:
        raise ValueError("Host did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise ValueError("Private, local or reserved network addresses are blocked")
    return url


def fetch_html(url: str, timeout: int = 12, max_redirects: int = 3) -> tuple[str, str]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Thiếu requests. Cài bằng: pip install -e '.[research]'") from exc
    current = url
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"}
    with requests.Session() as session:
        for _ in range(max_redirects + 1):
            validate_public_url(current)
            response = session.get(current, headers=headers, timeout=timeout, stream=True,
                                   allow_redirects=False)
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise ValueError("Redirect without Location header")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not any(x in content_type for x in ("text/html", "application/xhtml", "text/plain")):
                response.close()
                raise ValueError(f"Unsupported content type: {content_type}")
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    response.close()
                    raise ValueError("Page exceeds 2 MB limit")
                chunks.append(chunk)
            response.close()
            encoding = response.encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace"), current
    raise ValueError("Too many redirects")


def extract_text(html: str) -> str:
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=False,
                                   favor_precision=True)
        if text:
            return text.strip()
    except ImportError:
        pass
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "nav", "footer", "header", "aside"]):
        node.decompose()
    return "\n".join(s.strip() for s in soup.get_text("\n").splitlines() if s.strip())


def fetch_article(url: str) -> tuple[str, str]:
    html, final_url = fetch_html(url)
    return extract_text(html), final_url
