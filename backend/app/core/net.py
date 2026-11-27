from fastapi import Request


def client_ip(request: Request, *, trusted_proxy: bool) -> str:
    """The caller's IP, for rate limiting.

    Behind our Caddy (the only supported production topology) the proxy overwrites
    `X-Forwarded-For` with the real peer, so the last entry is trustworthy. When the API is reached
    directly (local dev, tests) the header is attacker-controlled and ignored.
    """
    if trusted_proxy and (forwarded := request.headers.get("x-forwarded-for")):
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
