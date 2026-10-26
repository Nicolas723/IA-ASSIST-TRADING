#!/usr/bin/env python3
"""Restore a backup created by ``backup_runtime_data.py``.

The restore is destructive: it replaces the database objects and the vault
files with the contents of the archive. It therefore requires ``--yes`` and
refuses to run while the backend is serving traffic unless ``--force`` is also
passed (stopping the backend first is the safe option).

Usage (from ``backend/``)::

    # Inspect what a backup contains
    python scripts/restore_runtime_data.py --backup ../backups/backup-20261026-063500.zip

    # Restore database and vault
    python scripts/restore_runtime_data.py --backup ../backups/backup-...zip --yes

    # Restore the vault only
    python scripts/restore_runtime_data.py --backup ../backups/backup-...zip --yes --skip-database

    # Restore the database only, into another database (safe rehearsal)
    python scripts/restore_runtime_data.py --backup ../backups/backup-...zip --yes \
        --skip-vault --database trading_db_rehearsal

Before touching the vault, the current directory is copied to
``<vault-dir>.bak-<timestamp>``. Before restoring the database, every other
connection to the target database is terminated; run this with the backend
stopped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote, urlparse

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = BACKEND_DIR / ".env"
DEFAULT_VAULT_DIR = BACKEND_DIR / "vault"
DEFAULT_CONTAINER = "ata-postgres"


def _log(message: str) -> None:
    print(message, flush=True)


def _fail(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _database_config(env: dict[str, str]) -> dict[str, str]:
    url = env.get("DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not url:
        _fail("DATABASE_URL is not set in the env file or environment")
    normalized = url.replace("+asyncpg", "").replace("+psycopg2", "")
    parsed = urlparse(normalized)
    if not parsed.hostname or not parsed.path.lstrip("/"):
        _fail(f"Unsupported DATABASE_URL: {normalized!r}")
    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
        "name": parsed.path.lstrip("/"),
    }


def _container_running(container: str) -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _tool_command(
    tool: str,
    *,
    db: dict[str, str],
    container: str | None,
    extra: list[str],
) -> tuple[list[str], dict[str, str]]:
    if container:
        return (["docker", "exec", "-i", container, tool, "-U", db["user"], *extra], {})
    executable = shutil.which(tool)
    if executable is None:
        _fail(f"{tool} is not installed and no Docker container is available")
    env = {"PGPASSWORD": db["password"]} if db["password"] else {}
    return (
        [executable, "-h", db["host"], "-p", db["port"], "-U", db["user"], *extra],
        env,
    )


def _run(
    cmd: list[str],
    *,
    stdin_path: Path | None = None,
    env_extra: dict[str, str] | None = None,
    description: str,
) -> None:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    stdin = stdin_path.open("rb") if stdin_path else subprocess.DEVNULL
    try:
        result = subprocess.run(
            cmd,
            stdin=stdin,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    finally:
        if stdin_path:
            stdin.close()  # type: ignore[union-attr]
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        _fail(f"{description} failed (exit {result.returncode}): {stderr}")


def _psql(
    sql: str,
    *,
    db: dict[str, str],
    container: str | None,
    database: str,
) -> str:
    cmd, env_extra = _tool_command(
        "psql", db=db, container=container, extra=["-d", database, "-t", "-A", "-c", sql]
    )
    env = os.environ.copy()
    env.update(env_extra)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        _fail(f"psql query failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, object]:
    try:
        raw = archive.read("manifest.json")
    except KeyError:
        _fail("Backup has no manifest.json; it was not created by this tool")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        _fail("manifest.json is not a JSON object")
    return {str(key): value for key, value in data.items()}


def _check_archive(archive: zipfile.ZipFile, manifest: dict[str, object]) -> None:
    database = manifest.get("database", {})
    vault = manifest.get("vault", {})
    if not isinstance(database, dict) or not isinstance(vault, dict):
        _fail("Malformed manifest")
    for member, expected, label in (
        ("database.dump", database.get("sha256"), "database"),
        ("vault.zip", vault.get("sha256"), "vault"),
    ):
        try:
            data = archive.read(member)
        except KeyError:
            _fail(f"Backup is missing {member}")
        actual = hashlib.sha256(data).hexdigest()
        if expected and actual != expected:
            _fail(f"{label} checksum mismatch: expected {expected}, got {actual}")
        _log(f"Checksum OK: {member} ({len(data) / 1024:.1f} KiB)")


def _restore_database(
    *,
    dump_path: Path,
    db: dict[str, str],
    container: str | None,
    database: str,
    create_database: bool = False,
) -> None:
    _log(f"Restoring database into {database} ...")
    exists = _psql(
        f"SELECT 1 FROM pg_database WHERE datname = '{database}'",
        db=db,
        container=container,
        database="postgres",
    )
    if not exists:
        if not create_database:
            _fail(
                f"Database {database} does not exist. Create it first or pass "
                "--create-database (rehearsal only)."
            )
        _psql(
            f'CREATE DATABASE "{database}"',
            db=db,
            container=container,
            database="postgres",
        )
        _log(f"Created database {database}")
    try:
        _psql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{database}' AND pid <> pg_backend_pid()",
            db=db,
            container=container,
            database="postgres",
        )
    except SystemExit:
        _log("WARNING: could not terminate existing connections; the restore may block.")
    cmd, env_extra = _tool_command(
        "pg_restore",
        db=db,
        container=container,
        extra=[
            "-d",
            database,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
        ],
    )
    _run(cmd, stdin_path=dump_path, env_extra=env_extra, description="pg_restore")


def _restore_vault(vault_zip: Path, vault_dir: Path) -> Path:
    if vault_dir.exists():
        backup_dir = vault_dir.with_name(
            f"{vault_dir.name}.bak-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copytree(vault_dir, backup_dir)
        _log(f"Current vault copied to {backup_dir}")
    vault_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(vault_zip) as archive:
        for member in archive.namelist():
            if member.endswith("/") or ".." in Path(member).parts:
                continue
            target = vault_dir / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    _log(f"Vault restored into {vault_dir}")
    return vault_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    parser.add_argument("--container", default=None)
    parser.add_argument("--no-container", action="store_true")
    parser.add_argument("--database", default=None, help="Target database (default: manifest)")
    parser.add_argument(
        "--create-database",
        action="store_true",
        help="Create the target database when missing (rehearsal into a new database)",
    )
    parser.add_argument("--skip-database", action="store_true")
    parser.add_argument("--skip-vault", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Confirm the destructive restore")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow the restore without checking that the backend is stopped",
    )
    args = parser.parse_args()

    if not args.backup.exists():
        _fail(f"Backup not found: {args.backup}")
    if not args.yes:
        _fail("Refusing to restore without --yes (this replaces live data)")

    env = _load_env(args.env_file)
    db = _database_config(env)

    container: str | None = None
    if not args.no_container:
        candidate = args.container or DEFAULT_CONTAINER
        if _container_running(candidate):
            container = candidate
        elif args.container:
            _fail(f"Container {args.container} is not running")

    if not args.force and not args.skip_database:
        try:
            import httpx

            response = httpx.get("http://localhost:8000/health", timeout=2.0)
            if response.status_code == 200:
                _fail(
                    "The backend is running on localhost:8000. Stop it first "
                    "(docker compose stop backend) or pass --force."
                )
        except SystemExit:
            raise
        except Exception:
            pass

    with tempfile.TemporaryDirectory(prefix="ata-restore-") as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(args.backup) as archive:
            manifest = _read_manifest(archive)
            _check_archive(archive, manifest)
            _log(
                f"Backup created at {manifest.get('created_at')} "
                f"(commit {manifest.get('git_commit')})"
            )
            database_meta = manifest.get("database", {})
            if not isinstance(database_meta, dict):
                _fail("Malformed database section in manifest")
            target_database = args.database or str(database_meta.get("name") or db["name"])
            if not args.skip_database:
                archive.extract("database.dump", tmp_dir)
            if not args.skip_vault:
                archive.extract("vault.zip", tmp_dir)

        if not args.skip_database:
            _restore_database(
                dump_path=tmp_dir / "database.dump",
                db=db,
                container=container,
                database=target_database,
                create_database=args.create_database,
            )
            counts = database_meta.get("table_counts", {})
            if isinstance(counts, dict) and counts:
                _log(
                    "Backup row counts: "
                    + ", ".join(f"{key}={value}" for key, value in counts.items())
                )
        if not args.skip_vault:
            _restore_vault(tmp_dir / "vault.zip", args.vault_dir)

    _log("Restore complete. Start the backend and verify /api/v1/trading/real/status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
