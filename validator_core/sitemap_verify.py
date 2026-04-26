"""
Sitemap verify — check if a remote URL (sitemap.xml, robots.txt, etc.) is accessible and has content.
Exit codes:
  0 = PASS (http 200 and body >= min_bytes)
  2 = FAIL (not 200 or too small)
  3 = network/config error
"""

import sys
import urllib.request


def verify_sitemap(url: str, min_bytes: int = 50) -> tuple[int, dict]:
    """
    Verify sitemap or robots.txt accessibility.
    Returns: (exit_code, status_dict)
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pypub-validator/1.0.2"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            data = resp.read()

        if code != 200:
            print(f"FAIL: http={code} url={url}")
            return 2, {"http_code": code, "bytes": len(data)}

        if len(data) < min_bytes:
            print(f"FAIL: http=200 but too small ({len(data)} bytes) url={url}")
            return 2, {"http_code": 200, "bytes": len(data)}

        print(f"PASS: http=200 bytes={len(data)} url={url}")
        return 0, {"http_code": 200, "bytes": len(data)}

    except Exception as e:
        print(f"ERROR: {e}")
        return 3, {"error": str(e)}
