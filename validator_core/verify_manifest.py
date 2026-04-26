"""
Verify manifest — post-ship validation comparing local output against remote destination.
Inspects actual remote state after shipping.

Compares:
  - File count
  - Relative filenames/paths
  - File sizes

Exit codes:
  0 = PASS (local and remote match)
  2 = FAIL (missing, extra, or size mismatch)
  3 = connection/config/auth error
"""

from pathlib import Path
import os

try:
    import paramiko
except ImportError:
    paramiko = None


def load_destinations_from_orchestrator():
    """Import and call the orchestrator's load_destinations function."""
    try:
        from pypub.orchestrator import load_destinations
        return load_destinations()
    except ImportError:
        raise ImportError("Could not import load_destinations from pypub.orchestrator")


def sftp_connect(host, user, port, key_filename=None, password=None):
    """Establish SFTP connection."""
    if paramiko is None:
        raise ImportError("paramiko is required for remote verification")

    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())

    if key_filename:
        key_path = os.path.expanduser(key_filename)
        key = paramiko.RSAKey.from_private_key_file(key_path)
        ssh.connect(host, port=port, username=user, pkey=key)
    else:
        ssh.connect(host, port=port, username=user, password=password)

    return ssh, ssh.open_sftp()


def build_local_manifest(local_dir: Path) -> list:
    """
    Build manifest of local HTML files.
    Returns: list of (relpath_posix, size_bytes)
    """
    local_dir = Path(local_dir).expanduser().resolve()

    if not local_dir.exists():
        raise FileNotFoundError(f"Local directory not found: {local_dir}")

    if not local_dir.is_dir():
        raise ValueError(f"Path is not a directory: {local_dir}")

    items = []
    for f in sorted(local_dir.rglob("*.html")):
        if f.is_file():
            rel = f.relative_to(local_dir).as_posix()
            size = f.stat().st_size
            items.append((rel, size))

    return items


def build_remote_manifest(sftp, remote_base: str) -> list:
    """
    Build manifest of remote HTML files via SFTP.
    Returns: list of (relpath_posix, size_bytes)
    """
    items = []

    def walk_remote(sftp, path, base_len):
        """Recursively walk remote directory via SFTP."""
        try:
            listing = sftp.listdir_attr(path)
        except IOError:
            return

        for item in listing:
            full_path = f"{path}/{item.filename}".replace("//", "/")

            if item.filename.startswith('.'):
                continue

            # If it's a file and ends with .html
            if item.filename.endswith('.html'):
                rel = full_path[base_len:].lstrip('/')
                items.append((rel, item.st_size))

            # If it's a directory, recurse
            elif item.filename not in ['.', '..'] and os.path.isdir(f"{path}/{item.filename}"):
                try:
                    walk_remote(sftp, full_path, base_len)
                except Exception:
                    pass

    try:
        walk_remote(sftp, remote_base, len(remote_base))
    except Exception as e:
        raise IOError(f"Cannot access remote path {remote_base}: {e}")

    return sorted(items)


def compare_manifests(local_manifest: list, remote_manifest: list) -> tuple[list, list, list, int]:
    """
    Compare local and remote manifests.
    Returns: (missing_on_remote, extra_on_remote, size_mismatches, exit_code)
    """
    local_dict = {rel: size for rel, size in local_manifest}
    remote_dict = {rel: size for rel, size in remote_manifest}

    missing = []
    mismatched = []

    # Check local files exist on remote with correct size
    for rel, local_size in local_manifest:
        if rel not in remote_dict:
            missing.append(rel)
        elif remote_dict[rel] != local_size:
            mismatched.append((rel, local_size, remote_dict[rel]))

    # Check for extra files on remote
    extra = [rel for rel in remote_dict.keys() if rel not in local_dict]

    exit_code = 0
    if missing or mismatched or extra:
        exit_code = 2

    return missing, extra, mismatched, exit_code


def verify_manifest(
    output_dir: str,
    dest_name: str,
    remote_path_override: str = None,
) -> tuple[int, dict]:
    """
    Verify manifest: compare local published output with remote destination.

    Args:
        output_dir: Local directory containing published files
        dest_name: Destination name (from pypub dest list)
        remote_path_override: Optional override of remote path from destination config

    Returns: (exit_code, results_dict)
    """
    output_dir = Path(output_dir).expanduser().resolve()

    try:
        # Load destination config
        dests = load_destinations_from_orchestrator()
        dest_config = dests.get(dest_name)
        if not dest_config:
            raise KeyError(
                f"Destination '{dest_name}' not found\n"
                f"Available: {', '.join(dests.keys())}"
            )

        # Extract connection details
        host = dest_config.get("host")
        user = dest_config.get("user")
        port = int(dest_config.get("port", 22))
        remote_base = remote_path_override or dest_config.get("path")
        key_filename = dest_config.get("key_filename") or dest_config.get("keyfile")
        password = dest_config.get("password")

        if not all([host, user, remote_base]):
            raise ValueError("Destination missing host/user/path")

        # Build local manifest
        print(f"Scanning local: {output_dir}")
        local_manifest = build_local_manifest(output_dir)
        local_count = len(local_manifest)
        print(f"Local files: {local_count}")

        # Connect and build remote manifest
        print(f"Connecting to {user}@{host}:{port}...")
        ssh, sftp = sftp_connect(host, user, port, key_filename=key_filename, password=password)

        try:
            print(f"Scanning remote: {remote_base}")
            remote_manifest = build_remote_manifest(sftp, remote_base)
            remote_count = len(remote_manifest)
            print(f"Remote files: {remote_count}")
        finally:
            try:
                sftp.close()
            except Exception:
                pass
            ssh.close()

        # Compare
        missing, extra, mismatched, exit_code = compare_manifests(local_manifest, remote_manifest)

        # Report
        print()
        if exit_code == 0:
            print(f"✅ PASS: {local_count} files match exactly")
            return 0, {
                "dest": dest_name,
                "local_count": local_count,
                "remote_count": remote_count,
                "missing": 0,
                "extra": 0,
                "mismatched": 0,
            }

        # Mismatch details
        print(f"❌ FAIL: {dest_name}")
        print(f"Local: {local_count}  Remote: {remote_count}")

        if missing:
            print(f"\n🔴 Missing on remote ({len(missing)}):")
            for rel in missing[:20]:
                print(f"   {rel}")
            if len(missing) > 20:
                print(f"   ... and {len(missing) - 20} more")

        if extra:
            print(f"\n🟠 Extra on remote ({len(extra)}):")
            for rel in extra[:20]:
                print(f"   {rel}")
            if len(extra) > 20:
                print(f"   ... and {len(extra) - 20} more")

        if mismatched:
            print(f"\n🟡 Size mismatch ({len(mismatched)}):")
            for rel, local_sz, remote_sz in mismatched[:20]:
                print(f"   {rel}")
                print(f"      local={local_sz} bytes  remote={remote_sz} bytes")
            if len(mismatched) > 20:
                print(f"   ... and {len(mismatched) - 20} more")

        return 2, {
            "dest": dest_name,
            "local_count": local_count,
            "remote_count": remote_count,
            "missing": len(missing),
            "extra": len(extra),
            "mismatched": len(mismatched),
        }

    except Exception as e:
        print(f"ERROR: {e}")
        return 3, {}
