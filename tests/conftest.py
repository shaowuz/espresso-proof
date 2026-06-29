from __future__ import annotations

import os


_LOCALHOST_BYPASS = "127.0.0.1,localhost,::1"


def pytest_configure() -> None:
    for key in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(key)
        if current:
            parts = [part.strip() for part in current.split(",") if part.strip()]
            for host in _LOCALHOST_BYPASS.split(","):
                if host not in parts:
                    parts.append(host)
            os.environ[key] = ",".join(parts)
        else:
            os.environ[key] = _LOCALHOST_BYPASS
