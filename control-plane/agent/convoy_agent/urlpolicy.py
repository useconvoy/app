"""Approved artifact sources (§8, row 18): public Hugging Face GGUF resolve URLs pinned to a commit,
Convoy's own fixture/artifact endpoints, nothing else. Rejects private/local destinations and traversal."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

HF_HOST = "huggingface.co"
HF_REDIRECT_SUFFIXES = (".huggingface.co", ".hf.co")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SEG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class UrlPolicyError(ValueError):
    pass


def check_segment(seg: str) -> str:
    if not _SEG.match(seg) or seg in (".", "..") or seg.endswith("."):
        raise UrlPolicyError(f"unsafe path segment {seg!r}")
    return seg


def hf_resolve_url(repo: str, commit: str, filename: str) -> str:
    owner, _, name = repo.partition("/")
    if not owner or not name or "/" in name:
        raise UrlPolicyError("repo must be owner/name")
    check_segment(owner)
    check_segment(name)
    if not _COMMIT.match(commit):
        raise UrlPolicyError("revision must be a 40-hex commit sha")
    check_segment(filename)
    if not filename.lower().endswith(".gguf"):
        raise UrlPolicyError("only .gguf files are approved")
    return f"https://{HF_HOST}/{owner}/{name}/resolve/{commit}/{filename}"


def _host_is_private(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host in ("localhost",) or host.endswith(".local") or host.endswith(".internal")
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


def validate_download_url(url: str, *, server_base: str | None = None) -> str:
    """Return the kind of approved source: 'hf' or 'convoy'. Raise otherwise."""
    p = urlsplit(url)
    if p.username or p.password or p.fragment:
        raise UrlPolicyError("credentials/fragments in URLs are not allowed")
    host = (p.hostname or "").lower()
    if server_base and same_origin(url, server_base) and p.path.startswith("/api/"):
        return "convoy"  # the agent's own control plane (scheme decided by the operator's server URL)
    if p.scheme != "https":
        raise UrlPolicyError("only https sources are approved")
    if host == HF_HOST:
        parts = p.path.split("/")
        # /owner/name/resolve/<commit>/<file>
        if len(parts) == 6 and parts[3] == "resolve" and _COMMIT.match(parts[4]):
            for seg in (parts[1], parts[2], parts[5]):
                check_segment(seg)
            if parts[5].lower().endswith(".gguf"):
                return "hf"
        raise UrlPolicyError("HF URL must be /owner/name/resolve/<40-hex commit>/<file>.gguf")
    if _host_is_private(host):
        raise UrlPolicyError("private/local destinations are not approved")
    raise UrlPolicyError(f"host {host!r} is not an approved source")


def redirect_allowed(from_url: str, to_url: str) -> bool:
    p = urlsplit(to_url)
    if p.scheme != "https":
        return False
    host = (p.hostname or "").lower()
    if host == HF_HOST or any(host.endswith(s) for s in HF_REDIRECT_SUFFIXES):
        return not _host_is_private(host)
    return False


def same_origin(url: str, base: str) -> bool:
    """Exact origin match (scheme, host, port). Device credentials are attached only to this origin."""
    a, b = urlsplit(url), urlsplit(base)
    return (
        a.scheme == b.scheme
        and (a.hostname or "").lower() == (b.hostname or "").lower()
        and (a.port or _default_port(a.scheme)) == (b.port or _default_port(b.scheme))
    )


def _default_port(scheme: str) -> int | None:
    return {"https": 443, "http": 80}.get(scheme)
