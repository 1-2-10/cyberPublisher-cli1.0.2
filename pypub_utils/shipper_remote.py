from pathlib import Path
import paramiko
import os


def ship_remote(output_dir, remote_spec, overwrite=False):
    """
    Ship published output to a remote host via SFTP.
    Preserves directory structure from output_dir to remote.

    Guarded:
      • No-op if output dir missing or empty
      • Only runs on successful publish

    Args:
        output_dir (str | Path):
            Directory containing published files to ship
            (e.g., test_dir/dinner/2025-12/)
        remote_spec (dict):
            {
              "host": "...",
              "user": "...",
              "port": 22,
              "path": "/remote/output/dir",
              "key_filename": "...",  # optional (note: changed from "keyfile")
              "password": "..."       # optional
            }
        overwrite (bool):
            overwrite existing files if True
    """
    src_dir = Path(output_dir).resolve()

    # --------------------------------------------------
    # GUARD — nothing to ship
    # --------------------------------------------------
    if not src_dir.exists() or not any(src_dir.rglob("*")):
        return  # clean no-op

    host = remote_spec.get("host")
    user = remote_spec.get("user")
    port = int(remote_spec.get("port", 22))
    remote_base = remote_spec.get("path")

    if not all([host, user, remote_base]):
        raise RuntimeError("Incomplete remote_spec for remote publish")

    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())

    try:
        # ---------------------------------------------
        # CONNECT (key preferred, fallback to password)
        # ---------------------------------------------
        key_filename = remote_spec.get("key_filename") or remote_spec.get("keyfile")

        if key_filename:
            key = paramiko.RSAKey.from_private_key_file(
                os.path.expanduser(key_filename)
            )
            ssh.connect(host, port=port, username=user, pkey=key)
        else:
            ssh.connect(
                host,
                port=port,
                username=user,
                password=remote_spec.get("password"),
            )

        sftp = ssh.open_sftp()

        # ---------------------------------------------
        # ENSURE REMOTE DIRECTORY EXISTS (recursive)
        # ---------------------------------------------
        def _ensure_remote_dir(sftp, remote_path):
            """
            Create remote directories recursively if needed.
            """
            parts = Path(remote_path).parts
            current = ""
            for part in parts:
                current = f"{current}/{part}" if current else part
                try:
                    sftp.stat(current)
                except FileNotFoundError:
                    sftp.mkdir(current)

        # Ensure base remote path exists
        _ensure_remote_dir(sftp, remote_base)

        # ---------------------------------------------
        # UPLOAD FILES (preserving directory structure)
        # ---------------------------------------------
        for src_file in src_dir.rglob("*"):
            if not src_file.is_file():
                continue

            # Calculate relative path from source directory
            rel_path = src_file.relative_to(src_dir)
            remote_file = str(Path(remote_base) / rel_path)

            # Ensure parent directory exists on remote
            remote_parent = str(Path(remote_file).parent)
            _ensure_remote_dir(sftp, remote_parent)

            # Skip if exists and overwrite=False
            if not overwrite:
                try:
                    sftp.stat(remote_file)
                    continue  # exists, skip
                except FileNotFoundError:
                    pass

            # Upload with atomic rename
            tmp_remote = remote_file + ".tmp"
            sftp.put(str(src_file), tmp_remote)

            try:
                sftp.rename(tmp_remote, remote_file)
            except IOError:
                # Fallback for filesystems that don't support atomic rename
                try:
                    sftp.remove(remote_file)
                except IOError:
                    pass
                sftp.rename(tmp_remote, remote_file)

    finally:
        try:
            sftp.close()
        except Exception:
            pass
        ssh.close()
