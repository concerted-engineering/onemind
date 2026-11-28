"""Narrow, injectable, origin-constrained HTTP transport and GitHub REST paginator.

SECURITY INVARIANTS:
- Zero third-party HTTP SDKs (Python stdlib urllib only).
- Constrained to a single explicitly configured trusted HTTPS GitHub API origin.
- Blocks cross-origin redirects, scheme downgrades, userinfo URLs, and arbitrary schemes.
- Validates Link headers strictly and detects pagination loops.
- Caps response byte size while reading to prevent memory exhaustion.
- Scrubs bearer tokens from all exceptions and diagnostics.
- Retries only bounded transient errors (429, 5xx, rate-limit 403, network timeouts) with
  injectable clock and sleep for deterministic offline testing.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from onemind.adapters.github.policy import scrub_token

DEFAULT_GITHUB_API_ORIGIN = "https://api.github.com"
DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MiB
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 30.0
DEFAULT_MAX_REDIRECTS = 3

_ALLOWED_JSON_CONTENT_TYPES = ("application/json", "application/vnd.github")
_LINK_PART_RE = re.compile(r'^\s*<([^>]+)>\s*(?:;\s*(.+))?$')
_LINK_REL_RE = re.compile(r'(?:^|;\s*)rel\s*=\s*(?:"([^"]+)"|([^;\s]+))', re.IGNORECASE)


class GitHubTransportError(Exception):
    """Base error for GitHub HTTP transport failures (always token-scrubbed)."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "transport_error",
        http_status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.http_status = http_status
        self.retryable = retryable


class UntrustedOriginError(GitHubTransportError):
    """Raised when a URL, redirect, or Link header targets a disallowed origin or scheme."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="untrusted_origin", retryable=False)


class ResponseTooLargeError(GitHubTransportError):
    """Raised when a response body exceeds max_response_bytes."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="response_too_large", retryable=False)


class UnexpectedContentTypeError(GitHubTransportError):
    """Raised when a JSON API response carries a non-JSON Content-Type."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(
            message,
            reason_code="unexpected_content_type",
            http_status=http_status,
            retryable=False,
        )


class MalformedJsonError(GitHubTransportError):
    """Raised when a response body cannot be decoded as valid JSON."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(
            message,
            reason_code="malformed_json",
            http_status=http_status,
            retryable=False,
        )


class NetworkTransportError(GitHubTransportError):
    """Raised on transient network/timeout failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="network_error", retryable=True)


class PaginationError(GitHubTransportError):
    """Raised on malformed Link headers or pagination loops."""

    def __init__(self, message: str, *, reason_code: str = "pagination_error") -> None:
        super().__init__(message, reason_code=reason_code, retryable=False)


@dataclass(frozen=True)
class HttpResponse:
    """Minimal immutable HTTP response returned by an injectable HttpTransport."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes
    url: str


@runtime_checkable
class HttpTransport(Protocol):
    """Narrow injectable read-only GET transport interface."""

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> HttpResponse: ...


def validate_trusted_origin(origin: str) -> str:
    """Validate and normalize an explicit HTTPS GitHub API origin (no path/query/userinfo)."""
    if not isinstance(origin, str) or not origin.strip():
        raise UntrustedOriginError("API origin must be a non-empty HTTPS URL.")
    if any(ch in origin for ch in ("\r", "\n", "\x00")):
        raise UntrustedOriginError("API origin contains illegal control characters.")
    parsed = urllib.parse.urlsplit(origin.strip())
    if parsed.scheme.lower() != "https":
        raise UntrustedOriginError(
            f"API origin must use 'https' scheme, got {parsed.scheme!r}."
        )
    if not parsed.hostname:
        raise UntrustedOriginError(f"API origin missing hostname: {origin!r}.")
    if parsed.username is not None or parsed.password is not None:
        raise UntrustedOriginError("API origin must not embed credentials/userinfo.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise UntrustedOriginError(
            f"API origin must contain only scheme and host[:port], got {origin!r}."
        )
    host = parsed.hostname.lower()
    if parsed.port is not None and parsed.port != 443:
        return f"https://{host}:{parsed.port}"
    return f"https://{host}"


def ensure_url_on_trusted_origin(url: str, trusted_origin: str) -> str:
    """Verify url uses https and matches the exact scheme/host/port of trusted_origin."""
    if not isinstance(url, str) or not url.strip():
        raise UntrustedOriginError("Empty URL rejected.")
    if any(ch in url for ch in ("\r", "\n", "\x00")):
        raise UntrustedOriginError("URL contains illegal control characters.")

    norm_origin = validate_trusted_origin(trusted_origin)
    origin_parts = urllib.parse.urlsplit(norm_origin)
    target = urllib.parse.urlsplit(url.strip())

    if target.scheme.lower() != "https":
        raise UntrustedOriginError(
            f"Blocked non-HTTPS URL scheme {target.scheme!r}; only {norm_origin} is permitted."
        )
    if target.username is not None or target.password is not None:
        raise UntrustedOriginError("Blocked URL containing embedded userinfo/credentials.")
    target_host = (target.hostname or "").lower()
    target_port = target.port if target.port is not None else 443
    origin_port = origin_parts.port if origin_parts.port is not None else 443
    if target_host != (origin_parts.hostname or "").lower() or target_port != origin_port:
        raise UntrustedOriginError(
            f"Blocked URL targeting untrusted host {target_host}:{target_port}; "
            f"only {norm_origin} is permitted."
        )
    return url.strip()


def parse_next_link(link_header: str | None, trusted_origin: str) -> str | None:
    """Strictly parse RFC 8288 Link header for rel='next', constraining to trusted_origin."""
    if not link_header or not link_header.strip():
        return None
    if len(link_header) > 8192:
        raise PaginationError("Link header exceeds safe length limit.", reason_code="malformed_link")

    found_next: str | None = None
    parts = link_header.split(",")
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            continue
        m = _LINK_PART_RE.match(part)
        if not m:
            raise PaginationError(
                f"Malformed Link header segment: {part[:80]!r}",
                reason_code="malformed_link",
            )
        href, params = m.group(1), m.group(2) or ""
        rel_match = _LINK_REL_RE.search(params)
        if not rel_match:
            continue
        rel_val = (rel_match.group(1) or rel_match.group(2) or "").strip().lower()
        if rel_val == "next":
            if found_next is not None:
                raise PaginationError(
                    "Link header contains multiple rel='next' entries.",
                    reason_code="malformed_link",
                )
            found_next = ensure_url_on_trusted_origin(href, trusted_origin)
    return found_next


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that only follows safe redirects strictly within trusted_origin."""

    def __init__(self, trusted_origin: str, max_redirects: int = DEFAULT_MAX_REDIRECTS) -> None:
        self._trusted_origin = validate_trusted_origin(trusted_origin)
        self._max_redirects = max_redirects
        self._redirect_count = 0

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        self._redirect_count += 1
        if self._redirect_count > self._max_redirects:
            raise UntrustedOriginError(
                f"Exceeded maximum safe redirect hops ({self._max_redirects})."
            )
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        ensure_url_on_trusted_origin(resolved, self._trusted_origin)
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _read_bounded(stream: Any, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    chunk_size = 65536
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(
                f"Response payload exceeded maximum allowed size ({max_bytes} bytes)."
            )
        chunks.append(chunk)
    return b"".join(chunks)


class UrllibGitHubTransport(HttpTransport):
    """Production read-only HTTPS GET transport using Python stdlib urllib."""

    def __init__(self, trusted_origin: str = DEFAULT_GITHUB_API_ORIGIN) -> None:
        self.trusted_origin = validate_trusted_origin(trusted_origin)

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> HttpResponse:
        safe_url = ensure_url_on_trusted_origin(url, self.trusted_origin)
        req = urllib.request.Request(safe_url, headers=dict(headers), method="GET")
        opener = urllib.request.build_opener(_SameOriginRedirectHandler(self.trusted_origin))
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = _read_bounded(resp, max_bytes)
                lower_headers = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponse(
                    status_code=int(resp.status),
                    headers=lower_headers,
                    body=body,
                    url=resp.geturl(),
                )
        except urllib.error.HTTPError as exc:
            try:
                err_body = _read_bounded(exc, min(max_bytes, 262_144))
            except ResponseTooLargeError:
                err_body = b""
            lower_headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            return HttpResponse(
                status_code=int(exc.code),
                headers=lower_headers,
                body=err_body,
                url=safe_url,
            )
        except TimeoutError as exc:
            raise NetworkTransportError(f"Request timed out after {timeout}s: {exc}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, GitHubTransportError):
                raise exc.reason from exc
            raise NetworkTransportError(f"Network error contacting GitHub API: {exc.reason}") from exc


class FixtureHttpTransport(HttpTransport):
    """Offline deterministic HTTP transport backed by an in-memory map or JSON fixture file."""

    def __init__(
        self,
        responses: Mapping[str, Any],
        *,
        trusted_origin: str = DEFAULT_GITHUB_API_ORIGIN,
    ) -> None:
        self.trusted_origin = validate_trusted_origin(trusted_origin)
        self._responses: dict[str, list[Any]] = {}
        for key, val in responses.items():
            # Support single response dict/HttpResponse or sequence of responses (for retry tests)
            if isinstance(val, list):
                self._responses[key] = list(val)
            else:
                self._responses[key] = [val]
        self.requests_made: list[tuple[str, dict[str, str]]] = []

    @classmethod
    def from_json_file(
        cls,
        fixture_path: Path | str,
        *,
        trusted_origin: str = DEFAULT_GITHUB_API_ORIGIN,
    ) -> FixtureHttpTransport:
        raw = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Fixture file must contain a JSON object mapping URLs/paths to responses.")
        mapping = raw.get("responses", raw)
        if not isinstance(mapping, dict):
            raise ValueError("Fixture 'responses' must be a JSON object.")
        return cls(mapping, trusted_origin=trusted_origin)

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> HttpResponse:
        safe_url = ensure_url_on_trusted_origin(url, self.trusted_origin)
        self.requests_made.append((safe_url, dict(headers)))

        parsed = urllib.parse.urlsplit(safe_url)
        path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")

        entry_list = self._responses.get(safe_url) or self._responses.get(path_and_query)
        if not entry_list:
            return HttpResponse(
                status_code=404,
                headers={"content-type": "application/json"},
                body=json.dumps({"message": "Not Found"}).encode("utf-8"),
                url=safe_url,
            )

        entry = entry_list.pop(0) if len(entry_list) > 1 else entry_list[0]
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, HttpResponse):
            if len(entry.body) > max_bytes:
                raise ResponseTooLargeError(
                    f"Response payload exceeded maximum allowed size ({max_bytes} bytes)."
                )
            return entry

        status = int(entry.get("status", 200))
        hdrs = {str(k).lower(): str(v) for k, v in (entry.get("headers") or {}).items()}
        hdrs.setdefault("content-type", "application/json; charset=utf-8")
        raw_body = entry.get("body", [])
        if isinstance(raw_body, bytes):
            body_bytes = raw_body
        elif isinstance(raw_body, str):
            body_bytes = raw_body.encode("utf-8")
        else:
            body_bytes = json.dumps(raw_body).encode("utf-8")

        if len(body_bytes) > max_bytes:
            raise ResponseTooLargeError(
                f"Response payload exceeded maximum allowed size ({max_bytes} bytes)."
            )

        # Simulate safe same-origin redirect check if fixture specifies 3xx with Location
        if status in (301, 302, 307, 308) and "location" in hdrs:
            resolved = urllib.parse.urljoin(safe_url, hdrs["location"])
            ensure_url_on_trusted_origin(resolved, self.trusted_origin)
            return self.get(resolved, headers, timeout=timeout, max_bytes=max_bytes)

        return HttpResponse(status_code=status, headers=hdrs, body=body_bytes, url=safe_url)


@dataclass
class PageSequenceResult:
    """Result of paginating a GitHub REST list endpoint with explicit completeness tracking."""

    items: list[Any] = field(default_factory=list)
    pages_attempted: int = 0
    pages_fetched: int = 0
    retries_performed: int = 0
    complete: bool = True
    error_code: str | None = None
    error_message: str | None = None
    http_status: int | None = None


def _parse_retry_after_seconds(
    headers: Mapping[str, str],
    *,
    now: float,
    max_backoff: float,
) -> float | None:
    ra = headers.get("retry-after")
    if ra:
        ra_s = ra.strip()
        if ra_s.isdigit():
            return min(float(int(ra_s)), max_backoff)
        try:
            dt = parsedate_to_datetime(ra_s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            delta = (dt.timestamp() - now)
            return min(max(delta, 0.0), max_backoff)
        except (TypeError, ValueError):
            pass

    reset = headers.get("x-ratelimit-reset")
    if reset and reset.strip().isdigit():
        delta = float(int(reset.strip())) - now
        if delta > 0:
            return min(delta, max_backoff)
    return None


def _is_rate_limit_403(headers: Mapping[str, str], body_text: str) -> bool:
    if headers.get("x-ratelimit-remaining") == "0":
        return True
    if "retry-after" in headers:
        return True
    lower = body_text.lower()
    return "rate limit" in lower or "secondary rate limit" in lower or "abuse detection" in lower


class GitHubRestClient:
    """Bounded, read-only, retry-capable GitHub REST client over an injectable HttpTransport."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        api_origin: str = DEFAULT_GITHUB_API_ORIGIN,
        token: str | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_origin = validate_trusted_origin(api_origin)
        self.transport: HttpTransport = transport or UrllibGitHubTransport(self.api_origin)
        self._token = token
        self.max_retries = max(0, max_retries)
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._clock = clock
        self._sleep = sleep

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "onemind-oss/0.1-draft",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get_json(self, url: str) -> tuple[Any, dict[str, str], int]:
        """Fetch a single JSON endpoint with bounded retries on transient/rate-limit errors.

        Returns (parsed_json, lower_headers, retries_performed).
        Raises GitHubTransportError (always scrubbed of tokens) on unrecoverable failure.
        """
        safe_url = ensure_url_on_trusted_origin(url, self.api_origin)
        headers = self._build_headers()
        retries = 0

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.transport.get(
                    safe_url,
                    headers,
                    timeout=self.timeout_seconds,
                    max_bytes=self.max_response_bytes,
                )
            except GitHubTransportError as exc:
                safe_msg = scrub_token(str(exc), self._token)
                if exc.retryable and attempt < self.max_retries:
                    delay = min(self.base_backoff_seconds * (2**attempt), self.max_backoff_seconds)
                    self._sleep(delay)
                    retries += 1
                    continue
                raise GitHubTransportError(
                    safe_msg,
                    reason_code="retry_exhausted" if exc.retryable and retries > 0 else exc.reason_code,
                    http_status=exc.http_status,
                    retryable=False,
                ) from None

            status = resp.status_code
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            body_preview = scrub_token(
                resp.body[:512].decode("utf-8", errors="replace"),
                self._token,
            )

            # Handle non-retryable auth / permission / not-found statuses explicitly
            if status == 401:
                raise GitHubTransportError(
                    "GitHub API authentication failed (HTTP 401 Unauthorized).",
                    reason_code="auth_unauthorized",
                    http_status=401,
                )
            if status == 403 and not _is_rate_limit_403(resp_headers, body_preview):
                raise GitHubTransportError(
                    "GitHub API access forbidden (HTTP 403 insufficient permissions or repository inaccessible).",
                    reason_code="permission_denied",
                    http_status=403,
                )
            if status in (404, 410):
                raise GitHubTransportError(
                    f"GitHub resource unavailable or not found (HTTP {status}).",
                    reason_code="not_found" if status == 404 else "gone",
                    http_status=status,
                )

            # Handle retryable rate-limit (429, rate-limit 403) and transient 5xx
            if status in (429, 500, 502, 503, 504) or (
                status == 403 and _is_rate_limit_403(resp_headers, body_preview)
            ):
                if attempt < self.max_retries:
                    ra = _parse_retry_after_seconds(
                        resp_headers,
                        now=self._clock(),
                        max_backoff=self.max_backoff_seconds,
                    )
                    delay = (
                        ra
                        if ra is not None
                        else min(self.base_backoff_seconds * (2**attempt), self.max_backoff_seconds)
                    )
                    self._sleep(delay)
                    retries += 1
                    continue
                code = "rate_limited" if status in (403, 429) else "server_error_exhausted"
                raise GitHubTransportError(
                    f"GitHub API request failed after {attempt + 1} attempt(s) (HTTP {status}).",
                    reason_code=code,
                    http_status=status,
                )

            if status < 200 or status >= 300:
                raise GitHubTransportError(
                    f"Unexpected GitHub API HTTP status {status}.",
                    reason_code="unexpected_http_status",
                    http_status=status,
                )

            # Validate Content-Type before decoding JSON
            ctype = resp_headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if not any(ctype.startswith(allowed) for allowed in _ALLOWED_JSON_CONTENT_TYPES):
                raise UnexpectedContentTypeError(
                    f"Unexpected Content-Type {ctype!r} from GitHub API (expected JSON).",
                    http_status=status,
                )

            try:
                parsed = json.loads(resp.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MalformedJsonError(
                    f"Malformed JSON payload from GitHub API: {scrub_token(str(exc), self._token)}",
                    http_status=status,
                ) from None

            return parsed, resp_headers, retries

        raise GitHubTransportError(
            "GitHub API request exhausted all retries.",
            reason_code="retry_exhausted",
        )

    def paginate_json_list(
        self,
        initial_url: str,
        *,
        max_pages: int,
        stop_predicate: Callable[[list[Any]], bool] | None = None,
    ) -> PageSequenceResult:
        """Paginate a GitHub REST list endpoint via Link headers with loop & limit detection."""
        result = PageSequenceResult()
        if max_pages < 1:
            result.complete = False
            result.error_code = "invalid_max_pages"
            result.error_message = "max_pages must be >= 1."
            return result

        visited: set[str] = set()
        next_url: str | None = ensure_url_on_trusted_origin(initial_url, self.api_origin)

        while next_url is not None:
            if next_url in visited:
                result.complete = False
                result.error_code = "pagination_loop"
                result.error_message = f"Detected pagination loop revisiting URL {next_url!r}."
                break
            if result.pages_fetched >= max_pages:
                result.complete = False
                result.error_code = "max_pages_truncated"
                result.error_message = (
                    f"Pagination stopped at max_pages={max_pages} with additional pages remaining."
                )
                break

            visited.add(next_url)
            result.pages_attempted += 1
            try:
                payload, headers, retries = self.get_json(next_url)
            except GitHubTransportError as exc:
                result.complete = False
                result.error_code = exc.reason_code
                result.error_message = scrub_token(str(exc), self._token)
                result.http_status = exc.http_status
                break

            result.retries_performed += retries
            if not isinstance(payload, list):
                result.complete = False
                result.error_code = "malformed_response"
                result.error_message = (
                    f"Expected JSON array from paginated endpoint, got {type(payload).__name__}."
                )
                break

            result.pages_fetched += 1
            result.items.extend(payload)

            # Optional early stop (e.g. sorted updated_at desc passed --since cutoff cleanly)
            if stop_predicate is not None and stop_predicate(payload):
                break

            try:
                next_url = parse_next_link(headers.get("link"), self.api_origin)
            except GitHubTransportError as exc:
                result.complete = False
                result.error_code = exc.reason_code
                result.error_message = scrub_token(str(exc), self._token)
                break

        return result
