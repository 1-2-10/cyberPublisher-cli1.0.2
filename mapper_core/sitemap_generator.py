#!/usr/bin/env python3
"""
========================================================
pyPub — Sitemap Generator
========================================================
Generates sitemap.xml body from a map_payload dict.

Rules:
- Emits ONLY <loc> and <lastmod>
- Includes ONLY: .html, .htm, .php
- No XML declaration (caller adds it)
- No timestamp block (caller adds it)
- No filesystem access
- PURE generator — no side effects
========================================================
"""

from xml.etree.ElementTree import Element, SubElement, tostring

VALID_EXTS = (".html", ".htm", ".php")


def generate_sitemap(map_payload: dict, base_url: str) -> str:
    """
    Generate sitemap XML body (no header, no timestamp stamp).

    Args:
        map_payload: map_index payload from map_core.scan_local()
        base_url:    site base URL (e.g. https://example.com)

    Returns:
        <urlset> XML string only — caller wraps with declaration + stamp.
    """
    base_url = (base_url or "").rstrip("/")

    mapper_run = map_payload.get("mapper_run", {})
    date = mapper_run.get("date")

    urlset = Element(
        "urlset",
        attrib={"xmlns": "http://www.sitemaps.org/schemas/sitemap/0.9"},
    )

    for node in map_payload.get("nodes", []):
        rel_path = (node.get("path") or ".").strip("/")

        for f in node.get("files", []):
            ext = (f.get("ext") or "").lower()
            if ext not in VALID_EXTS:
                continue

            url_el = SubElement(urlset, "url")

            if rel_path in ("", "."):
                loc_text = f"{base_url}/{f['name']}"
            else:
                loc_text = f"{base_url}/{rel_path}/{f['name']}"

            SubElement(url_el, "loc").text = loc_text.strip()
            SubElement(url_el, "lastmod").text = date

    return tostring(urlset, encoding="unicode")
