"""
Remote verify — check remote files on a destination against local files.
Uses SFTP to verify file existence and size match.
Exit codes:
  0 = PASS (all files exist with matching size)
  2 = FAIL (missing or size mismatch)
  3 = config/auth error
"""

from pathlib import Path
import sys

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
        import os
        key_path = os.path.expanduser(key_filename)
        key = paramiko.RSAKey.from_private_key_file(key_path)
        ssh.connect(host, port=port, username=user, pkey=key)
    else:
        ssh.connect(host, port=port, username=user, password=password)

    return ssh, ssh.open_sftp()


def build_manifest(local_root: Path, month: str, channels: list) -> list:
    """Build manifest of local files. Returns list of (relpath_posix, size_bytes, local_abs)."""
    items = []
    for ch in channels:
        base = local_root / ch / month
        if not base.exists():
            raise FileNotFoundError(f"Local month dir missing: {base}")
        for f in sorted(base.rglob("*.html")):
            if f.is_file():
                rel = f.relative_to(local_root).as_posix()
                items.append((rel, f.stat().st_size, str(f)))
    return items


def verify_remote(dest_name: str, month: str, channels: list, local_root: str = "test_dir") -> tuple[int, dict]:
    """
    Verify remote files against local manifest.
    Returns: (exit_code, status_dict)
    """
    local_root = Path(local_root).expanduser().resolve()

    try:
        dests = load_destinations_from_orchestrator()
        d = dests.get(dest_name)
        if not d:
            raise KeyError(f"Destination not found: {dest_name}. Available: {', '.join(dests.keys())}")

        host = d.get("host")
        user = d.get("user")
        port = int(d.get("port", 22))
        remote_base = d.get("path")
        key_filename = d.get("key_filename") or d.get("keyfile")
        password = d.get("password")

        if not all([host, user, remote_base]):
            raise ValueError("Destination missing host/user/path")

        manifest = build_manifest(local_root, month, channels)

        ssh, sftp = sftp_connect(host, user, port, key_filename=key_filename, password=password)
        missing = []
        mismatched = []

        try:
            for rel, size, local_abs in manifest:
                remote_path = str(Path(remote_base) / Path(rel))
                try:
                    st = sftp.stat(remote_path)
                except FileNotFoundError:
                    missing.append((rel, remote_path))
                    continue
                if int(st.st_size) != int(size):
                    mismatched.append((rel, size, int(st.st_size), remote_path))
        finally:
            try:
                sftp.close()
            except Exception:
                pass
            ssh.close()

        total = len(manifest)
        if missing or mismatched:
            print(f"FAIL: dest={dest_name} month={month}")
            print(f"Expected files: {total}")
            print(f"Missing: {len(missing)}  Size mismatches: {len(mismatched)}")
            for rel, rpath in missing[:50]:
                print(f"MISS  {rel}  ->  {rpath}")
            for rel, lsz, rsz, rpath in mismatched[:50]:
                print(f"SIZE  {rel}  local={lsz} remote={rsz}  ->  {rpath}")
            return 2, {
                "total": total,
                "missing": len(missing),
                "mismatched": len(mismatched),
            }

        print(f"PASS: dest={dest_name} month={month} files={total} (exists+size match)")
        return 0, {
            "total": total,
            "missing": 0,
            "mismatched": 0,
        }

    except Exception as e:
        print(f"ERROR: {e}")
        return 3, {}
