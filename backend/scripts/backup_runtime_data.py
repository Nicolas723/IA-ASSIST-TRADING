#!/usr/bin/env python3
"""Back up and verify the runtime state of the trading assistant.

A backup is a single zip file that contains:

* ``database.dump`` — PostgreSQL custom-format dump (``pg_dump -Fc``).
* ``vault.zip``     — the encrypted credential vault and runtime overrides
  (``.vault_store.json``, ``.runtime_config.json``, ``.runtime_llm_config.json``).
* ``manifest.json`` — sizes, SHA-256 checksums, table row counts and the git
  commit, so a restore can prove it is using intact data.

The database is dumped through ``pg_dump`` when it is installed locally, and
through ``docker exec`` otherwise (the development stack runs PostgreSQL in the
``ata-postgres`` container). The vault is copied byte-for-byte; it stays
encrypted with ``API_KEY_ENCRYPTION_KEY``, so the backup alone is not enough to
read the exchange keys.

Usage (from ``backend/``)::

    python scripts/backup_runtime_data.py
    python scripts/backup_runtime_data.py --verify
    python scripts/backup_runtime_data.py --output-dir D:/ata-backups --keep 14
    python scripts/backup_runtime_data.py --container ata-postgres --verify

``--verify`` restores the dump into a throw-away database
(``trading_db_verify_<timestamp>``), counts rows in the main tables and drops
it again. It never touches the live database.
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
REPO_DIR = BACKEND_DIR.parent
DEFAULT_ENV_FILE = BACKEND_DIR / ".env"
DEFAULT_VAULT_DIR = BACKEND_DIR / "vault"
DEFAULT_OUTPUT_DIR = REPO_DIR / "backups"
DEFAULT_CONTAINER = "ata-postgres"
BACKUP_PREFIX = "backup-"

# Tables that must exist in a usable restore. TimescaleDB internal schemas are
# intentionally excluded: they are recreated by the extension itself.
REQUIRED_TABLES = (
    "alembic_version",
    "orders",
    "positions",
    "trades",
    "portfolio_snapshots",
    "decision_logs",
    "shadow_scan_runs",
)


def _log(message: str) -> None:
    print(message, flush=True)


def _fail(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _load_env(path: Path) -> dict[str, str]:
    """Read a ``.env`` file without importing the application."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Strip inline comments only when they are separated by whitespace, so a
        # password containing '#' is preserved.
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


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _container_running(container: str) -> bool:
    if not _docker_available():
        return False
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _local_tool(name: str) -> str | None:
    return shutil.which(name)


def _tool_command(
    tool: str,
    *,
    db: dict[str, str],
    container: str | None,
    extra: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Build a pg tool command for either the host or the database container."""
    if container:
        return (
            ["docker", "exec", "-i", container, tool, "-U", db["user"], *extra],
            {},
        )
    executable = _local_tool(tool)
    if executable is None:
        _fail(
            f"{tool} is not installed and no Docker container was provided. "
            "Install PostgreSQL client tools or pass --container."
        )
    env = {"PGPASSWORD": db["password"]} if db["password"] else {}
    return (
        [
            executable,
            "-h",
            db["host"],
            "-p",
            db["port"],
            "-U",
            db["user"],
            *extra,
        ],
        env,
    )


def _run(
    cmd: list[str],
    *,
    stdin_path: Path | None = None,
    stdout_path: Path | None = None,
    env_extra: dict[str, str] | None = None,
    description: str,
) -> None:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    stdin = stdin_path.open("rb") if stdin_path else subprocess.DEVNULL
    stdout = stdout_path.open("wb") if stdout_path else subprocess.DEVNULL
    try:
        result = subprocess.run(
            cmd,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    finally:
        if stdin_path:
            stdin.close()  # type: ignore[union-attr]
        if stdout_path:
            stdout.close()  # type: ignore[union-attr]
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        _fail(f"{description} failed (exit {result.returncode}): {stderr}")


def _psql_scalar(
    sql: str,
    *,
    db: dict[str, str],
    container: str | None,
    database: str | None = None,
) -> str:
    extra = ["-d", database or db["name"], "-t", "-A", "-c", sql]
    cmd, env_extra = _tool_command("psql", db=db, container=container, extra=extra)
    env = os.environ.copy()
    env.update(env_extra)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        _fail(f"psql query failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _table_counts(*, db: dict[str, str], container: str | None) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    for table in REQUIRED_TABLES:
        try:
            value = _psql_scalar(
                f'SELECT count(*) FROM "{table}"',
                db=db,
                container=container,
            )
            counts[table] = int(value)
        except SystemExit:
            # A missing table must not abort the backup; the manifest records it.
            counts[table] = None
    return counts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _zip_vault(vault_dir: Path, target: Path) -> list[str]:
    if not vault_dir.exists():
        _fail(f"Vault directory not found: {vault_dir}")
    files: list[str] = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(vault_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(vault_dir)
            if "__pycache__" in relative.parts:
                continue
            archive.write(path, arcname=relative.as_posix())
            files.append(relative.as_posix())
    if ".vault_store.json" not in files:
        _log("WARNING: the vault directory has no .vault_store.json; the backup")
        _log("         contains runtime config only, not exchange credentials.")
    return files


def _dump_database(
    *,
    db: dict[str, str],
    container: str | None,
    target: Path,
) -> None:
    cmd, env_extra = _tool_command(
        "pg_dump",
        db=db,
        container=container,
        extra=["-d", db["name"], "-Fc", "--no-owner", "--no-privileges"],
    )
    _run(
        cmd,
        stdout_path=target,
        env_extra=env_extra,
        description="pg_dump",
    )


def _verify_backup(
    backup_path: Path,
    *,
    db: dict[str, str],
    container: str | None,
) -> dict[str, object]:
    """Restore the dump into a temporary database and validate the contents."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    verify_db = f"{db['name']}_verify_{timestamp}"
    _log(f"Verifying restore into temporary database {verify_db} ...")
    _psql_scalar(
        f'CREATE DATABASE "{verify_db}"',
        db=db,
        container=container,
        database="postgres",
    )
    try:
        with tempfile.TemporaryDirectory(prefix="ata-verify-") as tmp:
            tmp_dir = Path(tmp)
            with zipfile.ZipFile(backup_path) as archive:
                archive.extract("database.dump", tmp_dir)
            dump_path = tmp_dir / "database.dump"
            cmd, env_extra = _tool_command(
                "pg_restore",
                db=db,
                container=container,
                extra=[
                    "-d",
                    verify_db,
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                ],
            )
            _run(
                cmd,
                stdin_path=dump_path,
                env_extra=env_extra,
                description="pg_restore (verification)",
            )
        counts: dict[str, int | None] = {}
        for table in REQUIRED_TABLES:
            try:
                counts[table] = int(
                    _psql_scalar(
                        f'SELECT count(*) FROM "{table}"',
                        db=db,
                        container=container,
                        database=verify_db,
                    )
                )
            except SystemExit:
                counts[table] = None
        missing = [table for table, count in counts.items() if count is None]
        if missing:
            _fail(f"Restored database is missing required tables: {', '.join(missing)}")
        _log("Restored row counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        return {"database": verify_db, "table_counts": counts}
    finally:
        try:
            _psql_scalar(
                f'DROP DATABASE IF EXISTS "{verify_db}"',
                db=db,
                container=container,
                database="postgres",
            )
            _log(f"Dropped temporary database {verify_db}")
        except SystemExit:
            _log(f"WARNING: could not drop {verify_db}; remove it manually.")


def _apply_retention(output_dir: Path, keep: int) -> list[Path]:
    if keep <= 0:
        return []
    backups = sorted(output_dir.glob(f"{BACKUP_PREFIX}*.zip"), key=lambda p: p.stat().st_mtime)
    removed: list[Path] = []
    for path in backups[:-keep]:
        path.unlink()
        removed.append(path)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--container",
        default=None,
        help=f"Docker container running PostgreSQL (default: {DEFAULT_CONTAINER} when available)",
    )
    parser.add_argument(
        "--no-container",
        action="store_true",
        help="Use local pg_dump/psql only, even if Docker is available",
    )
    parser.add_argument("--keep", type=int, default=7, help="Backups to retain (0 = keep all)")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Restore the dump into a temporary database and check row counts",
    )
    args = parser.parse_args()

    env = _load_env(args.env_file)
    db = _database_config(env)

    container: str | None = None
    if not args.no_container:
        candidate = args.container or DEFAULT_CONTAINER
        if _container_running(candidate):
            container = candidate
        elif args.container:
            _fail(f"Container {args.container} is not running")
    if container is None and _local_tool("pg_dump") is None:
        _fail(
            "No usable pg_dump: start the PostgreSQL container, pass --container, "
            "or install PostgreSQL client tools."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = args.output_dir / f"{BACKUP_PREFIX}{timestamp}.zip"
    _log(f"Backing up database {db['name']} and vault {args.vault_dir}")

    with tempfile.TemporaryDirectory(prefix="ata-backup-") as tmp:
        tmp_dir = Path(tmp)
        dump_path = tmp_dir / "database.dump"
        vault_zip = tmp_dir / "vault.zip"
        manifest_path = tmp_dir / "manifest.json"

        _dump_database(db=db, container=container, target=dump_path)
        _log(f"Database dump: {dump_path.stat().st_size / 1024:.1f} KiB")

        vault_files = _zip_vault(args.vault_dir, vault_zip)
        counts = _table_counts(db=db, container=container)
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "app": "ai-trading-assistant",
            "git_commit": _git_commit(),
            "database": {
                "name": db["name"],
                "dump_file": "database.dump",
                "size_bytes": dump_path.stat().st_size,
                "sha256": _sha256(dump_path),
                "source": f"docker:{container}" if container else "local:pg_dump",
                "table_counts": counts,
            },
            "vault": {
                "archive": "vault.zip",
                "size_bytes": vault_zip.stat().st_size,
                "sha256": _sha256(vault_zip),
                "files": vault_files,
            },
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(manifest_path, arcname="manifest.json")
            archive.write(dump_path, arcname="database.dump")
            archive.write(vault_zip, arcname="vault.zip")

    _log(f"Backup written: {backup_path} ({backup_path.stat().st_size / 1024:.1f} KiB)")

    if args.verify:
        result = _verify_backup(backup_path, db=db, container=container)
        _log(f"Verification OK: {result}")

    removed = _apply_retention(args.output_dir, args.keep)
    if removed:
        _log("Retention removed: " + ", ".join(path.name for path in removed))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
