#!/usr/bin/env python3
"""
========================================================
pyPub — Mapper Core
========================================================
Local directory scanner.

Reads inclusion_filter.json from common_policy/ to
control which files and directories are included.

Returns a map_payload dict compatible with
sitemap_generator and site_chart_builder.

Remote walk: phase 2 (not implemented).
========================================================
"""

import os
import time
import json
from pathlib import Path

from pypub_utils.logging import log_cli


_DEFAULT_INCLUDE_EXTS = [".html", ".htm", ".php"]
_DEFAULT_EXCLUDE_DIRS = []
_DEFAULT_EXCLUDE_EXTS = []


def _load_inclusion_filter() -> dict:
    """
    Load inclusion_filter.json from common_policy/.
    Falls back to defaults silently if missing; warns if corrupt.
    """
    from pypub.instance_manager import get_active_instance_root
    filter_path = get_active_instance_root() / "common_policy" / "inclusion_filter.json"
    if filter_path.exists():
        try:
            with open(filter_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_cli(f"[WARN] inclusion_filter.json unreadable — using defaults ({e})")
    return {
        "include_exts": _DEFAULT_INCLUDE_EXTS,
        "exclude_dirs": _DEFAULT_EXCLUDE_DIRS,
        "exclude_exts": _DEFAULT_EXCLUDE_EXTS,
    }


def scan_local(target_dir: str) -> dict:
    """
    Walk target_dir and return a map_payload dict.

    Filters applied from common_policy/inclusion_filter.json:
      include_exts  — only files with these extensions are included
      exclude_dirs  — directory names to skip entirely
      exclude_exts  — file extensions to always exclude

    Returns:
        {
            "schema_version": 1,
            "root": str,
            "mapper_run": {"epoch": int, "date": str},
            "stats": {"directories": int, "files": int},
            "nodes": [{"path": str, "dirs": [...], "files": [...]}]
        }
    """
    target = Path(target_dir).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"Target directory not found: {target_dir}")

    filt = _load_inclusion_filter()
    include_exts = set(e.lower() for e in filt.get("include_exts", _DEFAULT_INCLUDE_EXTS))
    exclude_dirs = set(filt.get("exclude_dirs", _DEFAULT_EXCLUDE_DIRS))
    exclude_exts = set(e.lower() for e in filt.get("exclude_exts", _DEFAULT_EXCLUDE_EXTS))

    run_epoch = int(time.time())
    timestamp_hm = time.strftime("%Y-%m-%d %H:%M", time.gmtime(run_epoch))

    nodes = []
    dir_count = 0
    file_count = 0

    for root, dirs, files in os.walk(target):
        # Prune excluded dirs in-place so os.walk skips their subtrees
        dirs[:] = sorted(d for d in dirs if d not in exclude_dirs)

        rel_root = Path(root).relative_to(target)
        rel_path = str(rel_root) if str(rel_root) != "." else "."
        dir_count += 1

        file_entries = []
        for fname in sorted(files):
            ext = Path(fname).suffix.lower()
            if ext in exclude_exts:
                continue
            if include_exts and ext not in include_exts:
                continue
            fpath = Path(root) / fname
            try:
                size = fpath.stat().st_size
            except Exception:
                size = None
            file_entries.append({"name": fname, "ext": ext, "size": size})
            file_count += 1

        nodes.append({
            "path": rel_path,
            "dirs": sorted(dirs),
            "files": file_entries,
        })

    return {
        "schema_version": 1,
        "root": str(target),
        "mapper_run": {
            "epoch": run_epoch,
            "date": timestamp_hm,
        },
        "stats": {
            "directories": dir_count,
            "files": file_count,
        },
        "nodes": nodes,
    }
