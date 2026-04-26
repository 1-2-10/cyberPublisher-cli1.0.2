#!/usr/bin/env python3
"""
========================================================
pyPub — Orchestrator
========================================================
Coordinates the publish → ship workflow.

Workflow:
  1. Publish locally via pub_core
  2. (Optional) Ship to remote destination via shipper_remote

Auth Strategy:
  - Default: SSH keys (system handles)
  - Fallback: Encrypted passwords (stored in destinations)
========================================================
"""

import json
import os
from pathlib import Path
from typing import Optional

from publisher_core.pub_core import PublisherCore
from pypub_utils.shipper_remote import ship_remote
from pypub.crypto import encrypt_bytes, decrypt_bytes
from pypub.instance_manager import get_active_instance_root


# ========================================================
# Destination Management
# ========================================================

_ENC_PREFIX = "enc:"


def _destinations_read_path() -> Path:
    """
    Path to read destinations from.

    Always: ~/.pypub/instances/<id>/common_policy/destinations.json
    Seeded with a generic placeholder template on first instance bootstrap.
    """
    return get_active_instance_root() / "common_policy" / "destinations.json"


def _destinations_write_path() -> Path:
    """
    Path to write destinations to — always the instance common_policy path.
    """
    return get_active_instance_root() / "common_policy" / "destinations.json"


def _encrypt_password(plaintext: str) -> str:
    """Encrypt a password for storage. Returns 'enc:<fernet_token>'."""
    return _ENC_PREFIX + encrypt_bytes(plaintext.encode()).decode()


def _decrypt_password(stored: str) -> str:
    """Decrypt a stored password. Handles legacy plaintext transparently."""
    if stored.startswith(_ENC_PREFIX):
        return decrypt_bytes(stored[len(_ENC_PREFIX):].encode()).decode()
    return stored  # legacy plaintext — return as-is


def load_destinations() -> dict:
    """Load destinations from instance common_policy."""
    path = _destinations_read_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_destinations(destinations: dict):
    """Save destinations to instance common_policy path."""
    path = _destinations_write_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(destinations, indent=2, fp=f)


def get_destination(name: str) -> Optional[dict]:
    """Get a specific destination by name/alias. Decrypts password if present."""
    destinations = load_destinations()
    dest = destinations.get(name)
    if dest and dest.get("password"):
        dest = {**dest, "password": _decrypt_password(dest["password"])}
    return dest


# ========================================================
# Core Orchestration
# ========================================================


def _infer_ship_root(output_path: str) -> Path:
    """
    Choose a shipping root that preserves directory structure.

    My_Dinner default:
      If output_path is .../test_dir/<channel>/<YYYY-MM>,
      ship_root becomes .../test_dir so the shipper preserves
      channel/month subdirectories on the remote.

    Fallback:
      ship_root = output_path (preserves current behavior).
    """
    out_path = Path(output_path).expanduser().resolve()
    parts = out_path.parts

    if "test_dir" in parts:
        i = parts.index("test_dir")
        return Path(*parts[: i + 1])

    return out_path


_VALID_TARGETS = ("dev", "stage", "prod")


def _resolve_publish_target(destination: str) -> str:
    """
    Resolve publish tier from the destination record's explicit 'publish_target' field.

    Returns one of: "dev" | "stage" | "prod"

    Raises ValueError if:
      - destination not found in destinations.json
      - publish_target field is missing (no silent fallback — must be explicit)
      - publish_target value is not a recognised tier

    Backward compatibility:
      If publish_target is absent the record is considered misconfigured.
      Add 'publish_target' explicitly to each destination record to resolve.
    """
    dest = get_destination(destination)
    if not dest:
        raise ValueError(
            f"Destination '{destination}' not found. "
            f"Run: pypub dest list"
        )

    raw = (dest.get("publish_target") or "").strip().lower()

    if not raw:
        raise ValueError(
            f"Destination '{destination}' is missing 'publish_target' field. "
            f"Add 'publish_target': 'dev' | 'stage' | 'prod' to the record. "
            f"Run: pypub dest add {destination}  (to reconfigure)"
        )

    if raw not in _VALID_TARGETS:
        raise ValueError(
            f"Destination '{destination}' has invalid publish_target: '{raw}'. "
            f"Expected one of: {', '.join(_VALID_TARGETS)}"
        )

    return raw


def publish_and_ship(
    template_path: str,
    data_path: str,
    output_path: str,
    mode: str = "docs",
    ext: str = ".html",
    overwrite: bool = False,
    destination: Optional[str] = None,
    remote_path_override: Optional[str] = None,
    ship_root_override: Optional[str] = None,
) -> dict:
    """
    Orchestrate: publish locally, optionally ship remote.

    Args:
        template_path: Path to Jinja2 template
        data_path: Path to data file (JSON/XML/CSV)
        output_path: Local output directory
        mode: 'docs' (one file per record) or 'index' (single file)
        ext: Output file extension (default: .html)
        overwrite: Overwrite existing files (default: False)
        destination: Destination name from destinations.json (optional)
        remote_path_override: Override destination's default path (optional)
        ship_root_override: Override ship-root directory used to preserve structure (optional)

    Returns:
        {
            "success": bool,
            "local": {
                "files": [...],
                "logs": [...]
            },
            "remote": {
                "uploaded": int,
                "destination": "user@host:path"
            },
            "errors": [...]
        }
    """
    if destination:
        try:
            publish_target = _resolve_publish_target(destination)
        except ValueError as e:
            return {"success": False, "local": {}, "remote": {}, "errors": [str(e)]}
    else:
        publish_target = "dev"

    result = {"success": False, "local": {}, "remote": {}, "errors": []}

    # ================================================
    # Phase 1: Local Publish
    # ================================================
    try:
        core = PublisherCore(
            template_path=template_path,
            data_path=data_path,
            output_path=output_path,
            output_ext=ext,
            mode=mode,
            overwrite=overwrite,
            dry_run=False,
            publish_target=publish_target,
        )

        pub_result = core.run()

        if not pub_result.success:
            result["errors"].extend(pub_result.errors)
            return result

        result["local"] = {
            "files": pub_result.files,
            "logs": pub_result.logs,
            "output_path": output_path,
        }

    except Exception as e:
        result["errors"].append(f"Publish failed: {e}")
        return result

    # ================================================
    # Phase 2: Remote Ship (Optional)
    # ================================================
    if destination:
        # Decide ship_root (base dir for preserving structure)
        ship_root = (
            Path(ship_root_override).expanduser().resolve()
            if ship_root_override
            else _infer_ship_root(output_path)
        )

        try:
            # Load destination config
            dest_config = get_destination(destination)

            if not dest_config:
                result["errors"].append(f"Destination '{destination}' not found")
                result["errors"].append(
                    f"Available: {list(load_destinations().keys())}"
                )
                return result

            # Handle local destinations (just copy)
            if dest_config.get("type") == "local" or "local_path" in dest_config:
                local_dest = dest_config.get("local_path") or dest_config.get("path")

                # Simple file copy for local destinations
                import shutil

                Path(local_dest).mkdir(parents=True, exist_ok=True)

                copied = []
                for src_file in pub_result.files:
                    src = Path(src_file).expanduser().resolve()
                    rel = src.relative_to(ship_root)
                    dst_file = Path(local_dest) / rel
                    dst_file.parent.mkdir(parents=True, exist_ok=True)

                    if overwrite or not dst_file.exists():
                        shutil.copy2(src, dst_file)
                        copied.append(str(dst_file))

                result["remote"] = {
                    "uploaded": len(copied),
                    "destination": local_dest,
                    "type": "local",
                }

            # Handle remote destinations (SFTP)
            else:
                # Build remote_spec for shipper_remote
                remote_spec = {
                    "host": dest_config.get("host"),
                    "user": dest_config.get("user"),
                    "port": dest_config.get("port", 22),
                    "path": remote_path_override or dest_config.get("path"),
                }

                # Auth: SSH key (preferred) or password (fallback)
                if dest_config.get("key_filename"):
                    key_path = os.path.expanduser(dest_config["key_filename"])
                    remote_spec["keyfile"] = key_path
                elif dest_config.get("password"):
                    remote_spec["password"] = dest_config["password"]
                else:
                    # No explicit auth - let paramiko try agent/default keys
                    pass

                # Ship files (ship_root preserves directory structure)
                ship_remote(
                    output_dir=str(ship_root),
                    remote_spec=remote_spec,
                    overwrite=overwrite,
                )

                result["remote"] = {
                    "uploaded": len(pub_result.files),
                    "destination": f"{remote_spec['user']}@{remote_spec['host']}:{remote_spec['path']}",
                    "type": "sftp",
                }

        except TypeError as e:
            # Likely a parameter name mismatch calling ship_remote
            result["errors"].append(f"Remote ship configuration error: {e}")
            result["errors"].append(f"Local files preserved in: {output_path}")
            return result
        except Exception as e:
            err_str = str(e).lower()
            if "authentication" in err_str or "auth" in err_str:
                result["errors"].append(
                    f"SFTP authentication failed for {remote_spec.get('user')}@{remote_spec.get('host')}\n"
                    f"   Check SSH key or password in destination config"
                )
            elif "timed out" in err_str or "timeout" in err_str:
                result["errors"].append(
                    f"Connection timed out: {remote_spec.get('host')}:{remote_spec.get('port', 22)}\n"
                    f"   Check host is reachable and port {remote_spec.get('port', 22)} is open"
                )
            elif "no such file" in err_str or "permission denied" in err_str:
                result["errors"].append(
                    f"Remote path error on {remote_spec.get('host')}: {e}\n"
                    f"   Check remote path exists and user has write permission"
                )
            else:
                result["errors"].append(f"Remote ship failed: {e}")
            result["errors"].append(f"Local files preserved in: {output_path}")
            return result

    # ================================================
    # Success
    # ================================================
    result["success"] = True
    return result


# ========================================================
# Destination Helpers (For CLI)
# ========================================================


def add_destination(name: str, config: dict):
    """Add or update a destination. Encrypts password before writing to disk."""
    stored = dict(config)
    if stored.get("password"):
        stored["password"] = _encrypt_password(stored["password"])
    destinations = load_destinations()
    destinations[name] = stored
    save_destinations(destinations)


def remove_destination(name: str) -> bool:
    """Remove a destination. Returns True if existed."""
    destinations = load_destinations()
    if name in destinations:
        del destinations[name]
        save_destinations(destinations)
        return True
    return False


def list_destination_names() -> list:
    """Get list of configured destination names"""
    return list(load_destinations().keys())
