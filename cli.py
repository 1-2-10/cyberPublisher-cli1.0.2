#!/usr/bin/env python3
"""
========================================================
pyPub 1.0.2 - CLI Publisher
========================================================
Standalone CLI tool for template-based static site publishing.

Commands:
  cyberpublisher -t TEMPLATE -d DATA -o OUTPUT [OPTIONS]
  cyberpublisher -t TEMPLATE -d DATA -o OUTPUT --ship DESTINATION
  cyberpublisher dest add <name>
  cyberpublisher dest list
  cyberpublisher dest remove <name>

Examples:
  cyberpublisher -t ./template.html -d ./data.json -o ./build/
  cyberpublisher -t ./template.html -d ./data.json -o ./build/ --ship moonunit
  cyberpublisher dest list
========================================================
"""

import sys
import os
import argparse
import json
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Bootstrap instance — must run before any pypub.* import that
# touches destinations, crypto, or safehouse paths.
from pypub.instance_manager import bootstrap_instance
bootstrap_instance()

from pypub.orchestrator import (
    publish_and_ship,
    load_destinations,
    add_destination,
    remove_destination,
    list_destination_names,
)
from pypub_utils.logging import (
    RunlogEvent,
    emit_run_event,
    new_run_id,
    new_job_id,
    now_ts,
)
from mapper_core.map_core import scan_local
from mapper_core.sitemap_generator import generate_sitemap
from pypub_utils.site_chart_builder import build_site_chart
from validator_core.local_audit import audit_local_links
from validator_core.remote_verify import verify_remote
from validator_core.sitemap_verify import verify_sitemap
from validator_core.log_receipt import log_receipt
from validator_core.verify_manifest import verify_manifest


# ========================================================
# INPUT VALIDATION
# ========================================================

def validate_template(template_path: str) -> tuple:
    """Returns (ok: bool, error: str)"""
    if not template_path:
        return False, "Template path is required (-t)"

    path = Path(template_path)

    if not path.exists():
        return False, (
            f"Template not found: {template_path}\n"
            f"   Current directory: {os.getcwd()}"
        )

    if not path.is_file():
        return False, f"Template path is a directory, not a file: {template_path}"

    try:
        path.read_text(encoding="utf-8")
    except PermissionError:
        return False, f"Cannot read template (permission denied): {template_path}"
    except Exception as e:
        return False, f"Cannot read template: {e}"

    return True, ""


def validate_data(data_path: str) -> tuple:
    """Returns (ok: bool, error: str)"""
    if not data_path:
        return False, "Data path is required (-d)"

    path = Path(data_path)

    if not path.exists():
        return False, (
            f"Data file not found: {data_path}\n"
            f"   Current directory: {os.getcwd()}"
        )

    ext = path.suffix.lower()
    if ext not in [".json", ".xml", ".csv", ".md"]:
        return False, (
            f"Unsupported data format: {ext}\n"
            f"   Supported: .json, .xml, .csv, .md"
        )

    if ext == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return False, (
                f"Invalid JSON in {data_path}\n"
                f"   {e}\n"
                f"   Expected: {{\"records\": [...]}}"
            )

    return True, ""


def validate_output(output_path: str) -> tuple:
    """Returns (ok: bool, error: str)"""
    if not output_path:
        return False, "Output path is required (-o)"

    path = Path(output_path)

    if path.exists() and path.is_file():
        return False, f"Output path is a file, not a directory: {output_path}"

    check_dir = path if path.exists() else path.parent
    if not os.access(check_dir, os.W_OK):
        return False, f"No write permission: {check_dir}"

    return True, ""


def validate_destination(name: str) -> tuple:
    """Returns (ok: bool, error: str, dest: dict)"""
    destinations = load_destinations()

    if name not in destinations:
        available = list(destinations.keys())
        if available:
            return False, (
                f"Destination '{name}' not found\n"
                f"   Available: {', '.join(available)}\n"
                f"   Run: cyberpublisher dest list"
            ), {}
        else:
            return False, (
                f"No destinations configured\n"
                f"   Run: cyberpublisher dest add {name}"
            ), {}

    dest = destinations[name]
    missing = [f for f in ["host", "user", "path"] if not dest.get(f)]
    if missing:
        return False, (
            f"Destination '{name}' is incomplete. Missing: {', '.join(missing)}\n"
            f"   Run: cyberpublisher dest add {name}  (to reconfigure)"
        ), {}

    has_auth = dest.get("key_filename") or dest.get("password")
    if not has_auth:
        return False, (
            f"Destination '{name}' has no auth configured (need key_filename or password)\n"
            f"   Run: cyberpublisher dest add {name}  (to reconfigure)"
        ), {}

    return True, "", dest


# ========================================================
# QUICK REFERENCE GUIDE
# ========================================================

def show_guide():
    print("""
pyPub Quick Guide
═════════════════

LOCATIONS:
  Destinations:  ~/.pypub/instances/<id>/common_policy/destinations.json
  Policy:        ~/.pypub/instances/<id>/common_policy/

COMMANDS:
  Publish local:     cyberpublisher -t TEMPLATE -d DATA -o OUTPUT
  Publish + ship:    cyberpublisher -t TEMPLATE -d DATA -o OUTPUT --ship DEST
  Manage dests:      cyberpublisher dest [add|list|remove] NAME

REQUIRED FLAGS:
  -t, --template     Path to Jinja2 template (.html, .txt, etc.)
  -d, --data         Path to data file (.json, .xml, .csv, .md)
  -o, --output       Output directory (auto-created if missing)

OPTIONAL FLAGS:
  --mode             docs = one file per record (default)
                     index = single aggregate file
  --ext              Output file extension (default: .html)
  --overwrite        Replace existing output files
  --ship NAME        Ship to remote destination after publishing
  --remote-path      Override destination's default remote path

EXAMPLES:
  cyberpublisher -t ./template.html -d ./posts.json -o ./build/
  cyberpublisher -t ./template.html -d ./posts.json -o ./build/ --ship moonunit
  cyberpublisher -t ./t.html -d ./d.json -o ./build/ --mode index --ext .php
  cyberpublisher -t ./t.html -d ./d.json -o ./build/ --ship moonunit --overwrite

DESTINATIONS:
  cyberpublisher dest add moonunit      (interactive setup)
  cyberpublisher dest list              (show all configured)
  cyberpublisher dest remove staging    (confirm + delete)

TROUBLESHOOTING:
  Template not found  → check path, use absolute or ./relative/path
  Data parse error    → validate JSON/XML/CSV format
  Destination missing → run: cyberpublisher dest list
  SFTP auth failed    → check SSH key: ls ~/.ssh/id_rsa
  Files not uploaded  → check overwrite flag; local files are preserved

DOCS:
  docs/FEATURES.md       Feature reference
  docs/EXAMPLES.md       Usage patterns and automation
  docs/TROUBLESHOOTING.md  Common errors and fixes
""")


# ========================================================
# PUBLISH COMMAND
# ========================================================

def cmd_publish(args):
    """Execute publish workflow (local only or local + ship)."""

    # ============================================
    # PRE-FLIGHT: validate ALL inputs before work
    # ============================================
    errors = []

    ok, msg = validate_template(args.template)
    if not ok:
        errors.append(msg)

    ok, msg = validate_data(args.data)
    if not ok:
        errors.append(msg)

    ok, msg = validate_output(args.output)
    if not ok:
        errors.append(msg)

    if args.ship:
        ok, msg, _ = validate_destination(args.ship)
        if not ok:
            errors.append(msg)

    if errors:
        print("\n❌ Cannot publish — validation failed:\n")
        for err in errors:
            for line in err.split("\n"):
                print(f"   {line}")
            print()
        sys.exit(1)

    # ============================================
    # ALL VALID — run publish
    # ============================================
    _run_id = new_run_id()
    _job_id = new_job_id()
    _scope = "remote" if args.ship else "local"
    _t0 = time.time()

    emit_run_event(RunlogEvent(
        ts=now_ts(), run_id=_run_id, job_id=_job_id,
        tool="pypub", event="job_start", status="ok", scope=_scope,
        snapshot={
            "template": args.template,
            "data": args.data,
            "output": args.output,
            "mode": args.mode,
            "destination": args.ship,
        },
    ))

    result = publish_and_ship(
        template_path=args.template,
        data_path=args.data,
        output_path=args.output,
        mode=args.mode,
        ext=args.ext,
        overwrite=args.overwrite,
        destination=args.ship,
        remote_path_override=args.remote_path,
    )

    emit_run_event(RunlogEvent(
        ts=now_ts(), run_id=_run_id, job_id=_job_id,
        tool="pypub", event="job_end",
        status="ok" if result.get("success") else "fail",
        scope=_scope,
        timing_ms=int((time.time() - _t0) * 1000),
        counts={
            "files": len(result.get("local", {}).get("files", [])),
            "errors": len(result.get("errors", [])),
        },
        errors=result.get("errors") or None,
    ))

    if not result["success"]:
        print("\n❌ Publish failed:\n")
        for err in result["errors"]:
            print(f"   {err}")

        # Suggest fixes based on error content
        all_errors = " ".join(result["errors"]).lower()
        print("\nTroubleshooting:")
        if "not found" in all_errors or "no records" in all_errors:
            print("  • Check file paths and data format")
            print("  • Run: cyberpublisher --guide")
        if "sftp" in all_errors or "ssh" in all_errors or "connection" in all_errors:
            print("  • Local files are preserved in:", args.output)
            print("  • Check connection and try again")
        if "overwrite" in all_errors:
            print("  • Add --overwrite to replace existing files")
        print()
        sys.exit(1)

    # ============================================
    # SUCCESS OUTPUT
    # ============================================
    local = result["local"]
    print(f"\n✅ Published {len(local['files'])} file(s) to {args.output}")

    if result.get("remote"):
        remote = result["remote"]
        print(f"✅ Shipped {remote['uploaded']} file(s) to {remote['destination']}")

    sys.exit(0)


# ========================================================
# DESTINATION COMMANDS
# ========================================================

def cmd_dest_add(args):
    """Interactive destination configuration."""
    name = args.name
    
    print(f"\n📝 Configure destination '{name}'")
    print("─" * 50)
    
    # Collect info interactively
    host = input("Host (IP or domain): ").strip()
    user = input("Username: ").strip()
    path = input("Remote path: ").strip()
    
    # Auth method
    print("\nAuthentication method:")
    print("  1. SSH key (recommended)")
    print("  2. Password")
    auth_choice = input("Choice [1]: ").strip() or "1"
    
    dest = {
        "host": host,
        "user": user,
        "path": path,
    }
    
    if auth_choice == "2":
        import getpass
        password = getpass.getpass("Password: ")
        dest["password"] = password
    else:
        key_filename = input("SSH key path [~/.ssh/id_rsa]: ").strip() or "~/.ssh/id_rsa"
        dest["key_filename"] = key_filename

    # Publish tier
    print("\nPublish tier:")
    print("  1. dev   (no tracking — local scratch, dev server)")
    print("  2. stage (stage tracking snippets)")
    print("  3. prod  (prod tracking snippets)")
    tier_choice = input("Tier [1]: ").strip() or "1"
    tier_map = {"1": "dev", "2": "stage", "3": "prod"}
    dest["publish_target"] = tier_map.get(tier_choice, "dev")

    # Validate
    if not all([host, user, path]):
        print("\n❌ Invalid configuration")
        print("   Required: host, user, path")
        sys.exit(1)

    # Save
    add_destination(name, dest)
    print(f"\n✅ Destination '{name}' saved (publish_target: {dest['publish_target']})")


def cmd_dest_list(args):
    """List all configured destinations."""
    destinations = load_destinations()
    
    if not destinations:
        print("No destinations configured.")
        print("\nRun: cyberpublisher dest add <name>")
        return
    
    print("\n📍 Configured Destinations:")
    print("─" * 70)
    
    for name, dest in sorted(destinations.items()):
        user = dest.get('user', '')
        host = dest.get('host', '')
        path = dest.get('path', '')
        
        auth = "🔑 SSH key" if dest.get('key_filename') else "🔒 Password"
        
        print(f"  {name}:")
        print(f"    {user}@{host}:{path}")
        print(f"    Auth: {auth}")
        print()


def cmd_dest_remove(args):
    """Remove destination configuration."""
    name = args.name
    
    destinations = load_destinations()
    
    if name not in destinations:
        print(f"❌ Destination '{name}' not found")
        sys.exit(1)
    
    # Show what we're removing
    dest = destinations[name]
    host = dest.get('host', 'unknown')
    
    # Confirm
    confirm = input(f"\n⚠️  Remove destination '{name}' ({host})? [y/N]: ").strip().lower()
    
    if confirm != 'y':
        print("Cancelled.")
        sys.exit(0)
    
    remove_destination(name)
    print(f"✅ Destination '{name}' removed")


# ========================================================
# MAP COMMAND
# ========================================================

def _load_policy_json(filename: str) -> dict:
    """Load a JSON policy file from common_policy/. Returns {} if missing or corrupt."""
    from pypub.instance_manager import get_active_instance_root
    path = get_active_instance_root() / "common_policy" / filename
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_robots_disallow() -> list:
    """
    Load robots_disallow.txt from common_policy/.
    Returns list of path strings (comments and blanks stripped).
    """
    from pypub.instance_manager import get_active_instance_root
    path = get_active_instance_root() / "common_policy" / "robots_disallow.txt"
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


def _apply_path_filters(map_payload: dict, exclude_paths: list, exclude_extensions: list) -> dict:
    """Return a shallow-copied map_payload with path/extension filters applied to nodes."""
    ex_paths = set(exclude_paths or [])
    ex_exts = set(e.lower() for e in (exclude_extensions or []))
    if not ex_paths and not ex_exts:
        return map_payload
    filtered = []
    for node in map_payload.get("nodes", []):
        if node.get("path") in ex_paths:
            continue
        if ex_exts:
            files = [f for f in node.get("files", []) if f.get("ext", "").lower() not in ex_exts]
            node = {**node, "files": files}
        filtered.append(node)
    return {**map_payload, "nodes": filtered}


def cmd_map(args):
    """Scan a local directory and generate site artifacts."""
    target = args.target
    out_dir = Path(args.out).expanduser().resolve() if args.out else Path(target).expanduser().resolve()

    _run_id = new_run_id()
    _job_id = new_job_id()
    _t0 = time.time()

    emit_run_event(RunlogEvent(
        ts=now_ts(), run_id=_run_id, job_id=_job_id,
        tool="mapper", event="job_start", status="ok", scope="local",
        snapshot={"target": target, "out": str(out_dir), "base_url": args.base_url},
    ))

    try:
        # ------------------------------------------------
        # Scan
        # ------------------------------------------------
        map_payload = scan_local(target)
        dir_count = map_payload["stats"]["directories"]
        file_count = map_payload["stats"]["files"]
        timestamp_hm = map_payload["mapper_run"]["date"]
        run_epoch = map_payload["mapper_run"]["epoch"]

        out_dir.mkdir(parents=True, exist_ok=True)
        artifacts = []

        # ------------------------------------------------
        # Robots
        # ------------------------------------------------
        if not args.no_robots:
            disallow_lines = _load_robots_disallow()
            robots_header = (
                "=== PYPUB_MAPPER_RUN ===\n"
                f"DATE: {timestamp_hm}\n"
                f"EPOCH: {run_epoch}\n"
                "============================\n"
            )
            robots_lines = [robots_header, "User-agent: *\n", "Allow: /\n"]
            for path in disallow_lines:
                robots_lines.append(f"Disallow: {path}\n")
            if args.base_url:
                robots_lines.append(f"\nSitemap: {args.base_url.rstrip('/')}/sitemap.xml\n")
            robots_path = out_dir / "robots.txt"
            robots_path.write_text("".join(robots_lines), encoding="utf-8")
            artifacts.append("robots.txt")

        # ------------------------------------------------
        # Sitemap
        # ------------------------------------------------
        if args.base_url and not args.no_sitemap:
            sm_filters = _load_policy_json("sitemap_filters.json")
            sm_payload = _apply_path_filters(
                map_payload,
                sm_filters.get("exclude_paths", []),
                sm_filters.get("exclude_extensions", []),
            )
            sitemap_xml = generate_sitemap(sm_payload, args.base_url)
            stamp = (
                "<!--\n"
                "=== PYPUB_MAPPER_RUN ===\n"
                f"DATE: {timestamp_hm}\n"
                f"EPOCH: {run_epoch}\n"
                "============================\n"
                "-->\n"
            )
            full_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + stamp + sitemap_xml
            (out_dir / "sitemap.xml").write_text(full_xml, encoding="utf-8")
            artifacts.append("sitemap.xml")

        # ------------------------------------------------
        # Site chart
        # ------------------------------------------------
        if args.chart:
            sc_filters = _load_policy_json("site_chart_filters.json")
            sc_payload = _apply_path_filters(
                map_payload,
                sc_filters.get("exclude_paths", []),
                [],
            )
            chart = build_site_chart(sc_payload, depth_limit=args.depth)
            (out_dir / "site_chart.json").write_text(
                json.dumps(chart, indent=2), encoding="utf-8"
            )
            artifacts.append("site_chart.json")

        # ------------------------------------------------
        # Site log
        # ------------------------------------------------
        if not args.no_log:
            log_path = out_dir / "site_log.txt"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"=== PYPUB_MAPPER_RUN ===\n"
                    f"DATE: {timestamp_hm}\n"
                    f"EPOCH: {run_epoch}\n"
                    f"dirs={dir_count} | files={file_count}\n\n"
                )
            artifacts.append("site_log.txt")

    except Exception as e:
        emit_run_event(RunlogEvent(
            ts=now_ts(), run_id=_run_id, job_id=_job_id,
            tool="mapper", event="job_end", status="fail", scope="local",
            timing_ms=int((time.time() - _t0) * 1000),
            errors=[str(e)],
        ))
        print(f"\nMap failed: {e}")
        sys.exit(1)

    emit_run_event(RunlogEvent(
        ts=now_ts(), run_id=_run_id, job_id=_job_id,
        tool="mapper", event="job_end", status="ok", scope="local",
        timing_ms=int((time.time() - _t0) * 1000),
        counts={"directories": dir_count, "files": file_count},
    ))

    print(f"\nScanned: {file_count} files in {dir_count} directories")
    for a in artifacts:
        print(f"  {out_dir / a}")
    sys.exit(0)


# ========================================================
# VERIFY COMMANDS
# ========================================================

def cmd_verify_local(args):
    """Audit local links in published HTML files."""
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    exit_code, missing = audit_local_links(
        month=args.month,
        channels=channels,
        local_root=args.local_root,
    )
    sys.exit(exit_code)


def cmd_verify_remote(args):
    """Verify remote files match local files on destination."""
    channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    exit_code, status = verify_remote(
        dest_name=args.dest,
        month=args.month,
        channels=channels,
        local_root=args.local_root,
    )
    sys.exit(exit_code)


def cmd_verify_sitemap(args):
    """Verify sitemap.xml or robots.txt is accessible and has content."""
    exit_code, status = verify_sitemap(
        url=args.url,
        min_bytes=args.min_bytes,
    )
    sys.exit(exit_code)


def cmd_verify_log(args):
    """Log a run receipt to the run log."""
    log_receipt(
        month=args.month,
        channels=args.channels,
        ship=args.ship,
        publish=args.publish,
        validate_local=args.validate_local,
        verify_remote=args.verify_remote,
        result=args.result,
        notes=args.notes,
        log_path=args.log,
    )


def cmd_verify_manifest(args):
    """Verify manifest: compare local published output with remote destination."""
    exit_code, results = verify_manifest(
        output_dir=args.output,
        dest_name=args.dest,
        remote_path_override=args.remote_path,
    )
    sys.exit(exit_code)


# ========================================================
# MAIN CLI
# ========================================================

def main():
    parser = argparse.ArgumentParser(
        description='pyPub - Jinja2 static site publisher',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # ------------------------------------------------
    # PUBLISH (default - no subcommand needed)
    # ------------------------------------------------
    parser.add_argument('-t', '--template',
                       help='Path to Jinja2 template file')
    parser.add_argument('-d', '--data',
                       help='Path to data file (JSON/XML/CSV)')
    parser.add_argument('-o', '--output',
                       help='Output directory for generated files')
    parser.add_argument('--mode', choices=['docs', 'index'], default='docs',
                       help='Publish mode: docs (one file per record) or index (single file)')
    parser.add_argument('--ext', default='.html',
                       help='File extension for output files (default: .html)')
    parser.add_argument('--overwrite', action='store_true',
                       help='Overwrite existing files (default: skip existing)')
    parser.add_argument('--ship', metavar='DESTINATION',
                       help='Ship to remote destination after publishing')
    parser.add_argument('--remote-path', metavar='PATH',
                       help='Override destination remote path')
    parser.add_argument('--guide', action='store_true',
                       help='Show quick reference guide and exit')

    # ------------------------------------------------
    # MAP SUBCOMMAND
    # ------------------------------------------------
    map_parser = subparsers.add_parser('map', help='Scan a directory and generate site artifacts')
    map_parser.add_argument('--target', required=True, metavar='DIR',
                            help='Directory to scan')
    map_parser.add_argument('--base-url', metavar='URL',
                            help='Site base URL — enables sitemap.xml (e.g. https://example.com)')
    map_parser.add_argument('--out', metavar='DIR',
                            help='Output directory for artifacts (default: target dir)')
    map_parser.add_argument('--no-sitemap', action='store_true',
                            help='Skip sitemap.xml even if --base-url is given')
    map_parser.add_argument('--no-robots', action='store_true',
                            help='Skip robots.txt')
    map_parser.add_argument('--chart', action='store_true',
                            help='Generate site_chart.json')
    map_parser.add_argument('--depth', type=int, default=2, metavar='N',
                            help='Site chart depth limit (default: 2)')
    map_parser.add_argument('--no-log', action='store_true',
                            help='Skip site_log.txt append')
    map_parser.set_defaults(func=cmd_map)

    # ------------------------------------------------
    # DEST SUBCOMMAND
    # ------------------------------------------------
    dest_parser = subparsers.add_parser('dest', help='Manage remote destinations')
    dest_subparsers = dest_parser.add_subparsers(dest='dest_command', help='Destination commands')
    
    # dest add
    dest_add = dest_subparsers.add_parser('add', help='Add/update destination')
    dest_add.add_argument('name', help='Destination name (e.g., moonunit, prod)')
    dest_add.set_defaults(func=cmd_dest_add)
    
    # dest list
    dest_list = dest_subparsers.add_parser('list', help='List all destinations')
    dest_list.set_defaults(func=cmd_dest_list)
    
    # dest remove
    dest_remove = dest_subparsers.add_parser('remove', help='Remove destination')
    dest_remove.add_argument('name', help='Destination name to remove')
    dest_remove.set_defaults(func=cmd_dest_remove)

    # ------------------------------------------------
    # VERIFY SUBCOMMAND
    # ------------------------------------------------
    verify_parser = subparsers.add_parser('verify', help='Verify published content')
    verify_subparsers = verify_parser.add_subparsers(dest='verify_command', help='Verification methods')

    # verify local
    verify_local = verify_subparsers.add_parser('local', help='Audit local links in HTML files')
    verify_local.add_argument('--month', required=True, metavar='YYYY-MM',
                             help='Month to audit (e.g. 2025-12)')
    verify_local.add_argument('--channels', default='dinner,coffee',
                             help='Comma-separated channels (default: dinner,coffee)')
    verify_local.add_argument('--local-root', default='test_dir',
                             help='Local site root directory (default: test_dir)')
    verify_local.set_defaults(func=cmd_verify_local)

    # verify remote
    verify_remote = verify_subparsers.add_parser('remote', help='Verify remote files on destination')
    verify_remote.add_argument('--dest', required=True, metavar='NAME',
                              help='Destination name (from cyberpublisher dest list)')
    verify_remote.add_argument('--month', required=True, metavar='YYYY-MM',
                              help='Month to verify (e.g. 2025-12)')
    verify_remote.add_argument('--channels', default='dinner,coffee',
                              help='Comma-separated channels (default: dinner,coffee)')
    verify_remote.add_argument('--local-root', default='test_dir',
                              help='Local site root directory (default: test_dir)')
    verify_remote.set_defaults(func=cmd_verify_remote)

    # verify sitemap
    verify_sitemap = verify_subparsers.add_parser('sitemap', help='Verify sitemap.xml or robots.txt accessibility')
    verify_sitemap.add_argument('--url', required=True, metavar='URL',
                               help='Full URL to sitemap.xml or robots.txt')
    verify_sitemap.add_argument('--min-bytes', type=int, default=50,
                               help='Fail if response smaller than this (default: 50)')
    verify_sitemap.set_defaults(func=cmd_verify_sitemap)

    # verify log
    verify_log = verify_subparsers.add_parser('log', help='Log a run receipt')
    verify_log.add_argument('--month', required=True, metavar='YYYY-MM',
                           help='Month published')
    verify_log.add_argument('--channels', default='dinner,coffee',
                           help='Channels published')
    verify_log.add_argument('--ship', default='none',
                           help='Destination shipped to (default: none)')
    verify_log.add_argument('--publish', required=True,
                           help='Publish result (e.g. "ok (18 files)" or "FAIL (...)")')
    verify_log.add_argument('--validate-local', required=True,
                           help='Local validation result (e.g. "ok (0 missing)")')
    verify_log.add_argument('--verify-remote', default='n/a',
                           help='Remote verification result')
    verify_log.add_argument('--result', required=True, choices=['PASS', 'FAIL'],
                           help='Overall result')
    verify_log.add_argument('--notes', default='',
                           help='Additional notes')
    verify_log.add_argument('--log', default='logs/run_receipts.log',
                           help='Log file path (default: logs/run_receipts.log)')
    verify_log.set_defaults(func=cmd_verify_log)

    # verify manifest
    verify_manifest_cmd = verify_subparsers.add_parser('manifest', help='Verify manifest: compare local vs remote')
    verify_manifest_cmd.add_argument('--output', required=True, metavar='DIR',
                                   help='Local output directory (published files)')
    verify_manifest_cmd.add_argument('--dest', required=True, metavar='NAME',
                                   help='Destination name (from cyberpublisher dest list)')
    verify_manifest_cmd.add_argument('--remote-path', metavar='PATH',
                                   help='Override destination remote path (optional)')
    verify_manifest_cmd.set_defaults(func=cmd_verify_manifest)

    # ------------------------------------------------
    # PARSE & ROUTE
    # ------------------------------------------------
    args = parser.parse_args()

    # --guide: show quick reference and exit
    if getattr(args, 'guide', False):
        show_guide()
        sys.exit(0)

    # Route to appropriate handler
    if args.command == 'map':
        cmd_map(args)
    elif args.command == 'dest':
        if hasattr(args, 'func'):
            args.func(args)
        else:
            dest_parser.print_help()
    elif args.command == 'verify':
        if hasattr(args, 'func'):
            args.func(args)
        else:
            verify_parser.print_help()
    else:
        # Default: publish command
        if args.template and args.data and args.output:
            cmd_publish(args)
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
