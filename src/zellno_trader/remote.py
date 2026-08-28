from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from ftplib import FTP, all_errors
from pathlib import Path, PurePosixPath
from typing import Callable

from .loader import load_accounts, load_general_config


ACCOUNT_NAME = re.compile(r"^Account_\d{17}\.json$")


class RemoteError(RuntimeError):
    """Raised when a read-only FTP snapshot cannot be completed safely."""


@dataclass(frozen=True)
class SnapshotResult:
    path: Path
    manifest_path: Path
    account_count: int
    invalid_account_count: int
    trusted_for_editing: bool


def _safe_remote_root(value: str) -> str:
    path = PurePosixPath(value)
    if not value.startswith("/") or ".." in path.parts:
        raise RemoteError("O caminho remoto deve ser absoluto e não pode conter '..'.")
    return str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _account_names(ftp: FTP) -> list[str]:
    names = []
    for remote_name in ftp.nlst():
        name = posixpath.basename(remote_name.rstrip("/"))
        if ACCOUNT_NAME.fullmatch(name):
            names.append(name)
    if len(names) != len(set(names)):
        raise RemoteError("A listagem FTP contém nomes de conta duplicados.")
    return sorted(names)


def _download(ftp: FTP, remote_name: str, local_path: Path) -> None:
    if Path(remote_name).name != remote_name:
        raise RemoteError("Nome remoto inseguro recusado.")
    with local_path.open("xb") as handle:
        ftp.retrbinary(f"RETR {remote_name}", handle.write)


def create_snapshot(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    destination: Path,
    remote_root: str = "/profile/TraderPlus",
    server_stopped_attested: bool = False,
    timeout: int = 30,
    ftp_factory: Callable[[], FTP] = FTP,
) -> SnapshotResult:
    root = _safe_remote_root(remote_root)
    bank_remote = posixpath.join(root, "TraderPlusBankDatabase")
    config_remote = posixpath.join(root, "TraderPlusConfig")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, exist_ok=True)
    partial = destination / f".snapshot-{stamp}-partial"
    final = destination / f"snapshot-{stamp}"
    partial.mkdir()
    bank_local = partial / "TraderPlusBankDatabase"
    config_local = partial / "TraderPlusConfig"
    bank_local.mkdir()
    config_local.mkdir()

    ftp = ftp_factory()
    try:
        ftp.connect(host=host, port=port, timeout=timeout)
        ftp.login(user=user, passwd=password)
        ftp.set_pasv(True)

        ftp.cwd(bank_remote)
        names_before = _account_names(ftp)
        for name in names_before:
            _download(ftp, name, bank_local / name)
        names_after = _account_names(ftp)
        if names_before != names_after:
            raise RemoteError("A lista de contas mudou durante o snapshot; tente novamente.")

        ftp.cwd(config_remote)
        general_path = config_local / "TraderPlusGeneralConfig.json"
        _download(ftp, "TraderPlusGeneralConfig.json", general_path)

        try:
            ftp.quit()
        except all_errors:
            ftp.close()

        config = load_general_config(general_path)
        if not config.valid:
            messages = "; ".join(issue.message for issue in config.issues)
            raise RemoteError(f"Configuração baixada inválida: {messages}")
        accounts = load_accounts(bank_local, config)
        invalid = [account for account in accounts if not account.valid]

        files = []
        for path in sorted(partial.rglob("*.json")):
            files.append(
                {
                    "path": path.relative_to(partial).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        manifest = {
            "version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "transport": "plain_ftp_read_only",
            "remote_root": root,
            "server_stopped_attested": server_stopped_attested,
            "trusted_for_editing": server_stopped_attested and not invalid,
            "account_count": len(accounts),
            "invalid_account_count": len(invalid),
            "accounts": [
                {
                    "file": account.path.name,
                    "name": account.name,
                    "steamid64": account.steamid,
                    "status": account.status,
                    "issues": [
                        {"severity": issue.severity, "code": issue.code, "message": issue.message}
                        for issue in account.issues
                    ],
                }
                for account in accounts
            ],
            "files": files,
        }
        manifest_path = partial / "snapshot-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        partial.rename(final)
        return SnapshotResult(
            path=final,
            manifest_path=final / "snapshot-manifest.json",
            account_count=len(accounts),
            invalid_account_count=len(invalid),
            trusted_for_editing=manifest["trusted_for_editing"],
        )
    except (RemoteError, OSError, *all_errors) as exc:
        try:
            ftp.close()
        except Exception:
            pass
        shutil.rmtree(partial, ignore_errors=True)
        if isinstance(exc, RemoteError):
            raise
        raise RemoteError(f"Falha no snapshot FTP somente leitura: {exc}") from exc
