"""Trusted client identity extraction for public API abuse controls."""

from __future__ import annotations

from ipaddress import ip_address

from fastapi import Request


def _normalized_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return ip_address(value.strip()).compressed
    except ValueError:
        return None


def client_key(request: Request, *, trust_proxy_headers: bool) -> str:
    """Return a bounded, normalized client identity for an internal proxy chain.

    Proxy headers are considered only when the deployment has explicitly
    established that requests reach the API exclusively through trusted
    proxies. Invalid values fall back to the actual connected peer.
    """
    if trust_proxy_headers:
        cloudflare_ip = _normalized_ip(request.headers.get("CF-Connecting-IP"))
        if cloudflare_ip:
            return cloudflare_ip
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            forwarded_ip = _normalized_ip(forwarded_for.split(",", 1)[0])
            if forwarded_ip:
                return forwarded_ip
    return _normalized_ip(request.client.host if request.client else None) or "unknown"
