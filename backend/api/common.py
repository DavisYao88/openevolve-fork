"""本地请求校验，供各 blueprint 共享。

依赖 flask.request，只允许本机回环来源访问写接口。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from flask import request


def _is_local_request() -> bool:
    remote_addr = request.remote_addr
    try:
        if remote_addr is None or not ipaddress.ip_address(remote_addr).is_loopback:
            return False
    except ValueError:
        return False

    host = urlsplit(request.host_url).hostname
    try:
        if host != "localhost" and (host is None or not ipaddress.ip_address(host).is_loopback):
            return False
    except ValueError:
        return False

    origin = request.headers.get("Origin")
    return not origin or origin.rstrip("/") == request.host_url.rstrip("/")
