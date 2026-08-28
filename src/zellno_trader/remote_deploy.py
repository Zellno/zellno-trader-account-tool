from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from ftplib import FTP, all_errors
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .deployment import DeploymentError, validate_snapshot_target
from .loader import load_account


ACCOUNT_NAME = re.compile(r"^Account_(\d{17})\.json$")


class RemoteDeployError(RuntimeError):
    """Raised when a remote deployment is blocked, fails, or is rolled back."""


@dataclass(frozen=True)
class ValidatedPackage:
    path: Path
    manifest: dict[str, Any]
    account_name: str
    steamid: str
    original_path: Path
    proposed_path: Path
    original_sha256: str
    proposed_sha256: str


@dataclass(frozen=True)
class RemoteDeployResult:
    account_name: str
    steamid: str
    remote_backup_name: str
    local_backup_path: Path
    audit_path: Path
    before_sha256: str
    after_sha256: str


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _safe_remote_root(value: str) -> str:
    path = PurePosixPath(value)
    if not value.startswith("/") or ".." in path.parts:
        raise RemoteDeployError("O caminho remoto deve ser absoluto e não pode conter '..'.")
    return str(path)


def _package_file(package_path: Path, relative: str, expected: str) -> Path:
    if relative != expected:
        raise RemoteDeployError(f"Caminho inesperado no manifesto do pacote: {relative}")
    candidate = package_path / relative
    if candidate.is_symlink() or not candidate.is_file():
        raise RemoteDeployError(f"Arquivo ausente ou inseguro no pacote: {relative}")
    if package_path.resolve() not in candidate.resolve().parents:
        raise RemoteDeployError("Arquivo do pacote escapou da pasta esperada.")
    return candidate


def validate_deployment_package(package_path: Path) -> ValidatedPackage:
    if package_path.is_symlink() or not package_path.is_dir():
        raise RemoteDeployError("A pasta do pacote é inválida ou é um link simbólico.")
    manifest_path = package_path / "deployment-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RemoteDeployError(f"Manifesto do pacote inválido: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise RemoteDeployError("Versão do manifesto do pacote não suportada.")
    if manifest.get("status") != "prepared_not_deployed":
        raise RemoteDeployError("O pacote não está no estado preparado para implantação.")
    if manifest.get("server_stopped_attested") is not True:
        raise RemoteDeployError("O pacote não deriva de snapshot declarado com servidor parado.")
    if manifest.get("account_trusted_for_preparation") is not True:
        raise RemoteDeployError("A conta não foi marcada como confiável durante a preparação.")
    if manifest.get("ftp_upload_performed") is not False:
        raise RemoteDeployError("O manifesto indica uso FTP anterior e foi recusado.")

    account_name = manifest.get("account_file")
    match = ACCOUNT_NAME.fullmatch(account_name or "")
    steamid = manifest.get("steamid64")
    if not match or steamid != match.group(1):
        raise RemoteDeployError("Identidade do pacote inválida ou divergente.")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise RemoteDeployError("Lista de arquivos do pacote inválida.")
    original_path = _package_file(
        package_path,
        files.get("original", ""),
        f"original/{account_name}",
    )
    proposed_path = _package_file(
        package_path,
        files.get("proposed", ""),
        f"proposed/{account_name}",
    )
    original_hash = manifest.get("before", {}).get("sha256")
    proposed_hash = manifest.get("proposed", {}).get("sha256")
    if not isinstance(original_hash, str) or _sha256_file(original_path) != original_hash:
        raise RemoteDeployError("O hash do original não corresponde ao manifesto do pacote.")
    if not isinstance(proposed_hash, str) or _sha256_file(proposed_path) != proposed_hash:
        raise RemoteDeployError("O hash da proposta não corresponde ao manifesto do pacote.")
    if original_hash == proposed_hash:
        raise RemoteDeployError("O pacote não contém uma alteração efetiva.")

    source_snapshot_raw = manifest.get("source_snapshot")
    if not isinstance(source_snapshot_raw, str):
        raise RemoteDeployError("Origem do snapshot ausente no manifesto.")
    source_snapshot = Path(source_snapshot_raw)
    try:
        target = validate_snapshot_target(source_snapshot, account_name)
    except DeploymentError as exc:
        raise RemoteDeployError(f"Snapshot de origem recusado: {exc}") from exc
    if target.source_sha256 != original_hash:
        raise RemoteDeployError("O original do pacote diverge do snapshot de origem.")
    proposed = load_account(
        proposed_path,
        target.config,
        expected_filename=account_name,
    )
    if not proposed.valid or proposed.raw is None or target.account.raw is None:
        raise RemoteDeployError("A proposta do pacote é inválida.")
    if proposed.steamid != steamid or proposed.name != manifest.get("name"):
        raise RemoteDeployError("A identidade da proposta diverge do manifesto.")
    for key, value in target.account.raw.items():
        if key not in {"MoneyAmount", "Licences"} and proposed.raw.get(key) != value:
            raise RemoteDeployError(f"Campo fora do escopo foi alterado: {key}")
    if set(proposed.raw) != set(target.account.raw):
        raise RemoteDeployError("A proposta alterou o conjunto de campos do JSON.")
    if proposed.money_amount != manifest.get("proposed", {}).get("money_amount"):
        raise RemoteDeployError("O saldo da proposta diverge do manifesto.")
    if list(proposed.licences) != manifest.get("proposed", {}).get("licences"):
        raise RemoteDeployError("As licenças da proposta divergem do manifesto.")
    return ValidatedPackage(
        path=package_path,
        manifest=manifest,
        account_name=account_name,
        steamid=steamid,
        original_path=original_path,
        proposed_path=proposed_path,
        original_sha256=original_hash,
        proposed_sha256=proposed_hash,
    )


def _remote_names(ftp: FTP) -> set[str]:
    return {posixpath.basename(item.rstrip("/")) for item in ftp.nlst()}


def _retrieve_bytes(ftp: FTP, name: str) -> bytes:
    output = io.BytesIO()
    ftp.retrbinary(f"RETR {name}", output.write)
    return output.getvalue()


def _append_audit(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _restore_original_state(
    ftp: FTP,
    package: ValidatedPackage,
    *,
    temp_name: str,
    backup_name: str,
    failed_name: str,
) -> None:
    names = _remote_names(ftp)
    original_exists = package.account_name in names
    backup_exists = backup_name in names

    if original_exists:
        current_hash = _sha256_bytes(_retrieve_bytes(ftp, package.account_name))
        if current_hash != package.original_sha256:
            if not backup_exists:
                raise RemoteDeployError("O destino divergiu e o backup remoto não foi encontrado.")
            if failed_name in names:
                raise RemoteDeployError("O nome forense de rollback já existe no servidor.")
            ftp.rename(package.account_name, failed_name)
            ftp.rename(backup_name, package.account_name)
    elif backup_exists:
        ftp.rename(backup_name, package.account_name)
    else:
        raise RemoteDeployError("Nem o original nem o backup remoto foram encontrados.")

    restored = _retrieve_bytes(ftp, package.account_name)
    if _sha256_bytes(restored) != package.original_sha256:
        raise RemoteDeployError("O arquivo restaurado não corresponde ao original esperado.")
    names = _remote_names(ftp)
    if temp_name in names:
        ftp.delete(temp_name)


def _attempt_recovery(
    ftp: FTP,
    package: ValidatedPackage,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    timeout: int,
    bank_remote: str,
    temp_name: str,
    backup_name: str,
    failed_name: str,
    ftp_factory: Callable[[], FTP],
) -> tuple[bool, FTP]:
    try:
        _restore_original_state(
            ftp,
            package,
            temp_name=temp_name,
            backup_name=backup_name,
            failed_name=failed_name,
        )
        return True, ftp
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass
    recovery = ftp_factory()
    try:
        recovery.connect(host=host, port=port, timeout=timeout)
        recovery.login(user=user, passwd=password)
        recovery.set_pasv(True)
        recovery.cwd(bank_remote)
        _restore_original_state(
            recovery,
            package,
            temp_name=temp_name,
            backup_name=backup_name,
            failed_name=failed_name,
        )
        return True, recovery
    except Exception:
        try:
            recovery.close()
        except Exception:
            pass
        return False, recovery


def deploy_package_ftp(
    package: ValidatedPackage,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    state_dir: Path,
    remote_root: str = "/profile/TraderPlus",
    server_stopped_attested: bool = False,
    timeout: int = 30,
    ftp_factory: Callable[[], FTP] = FTP,
) -> RemoteDeployResult:
    if not server_stopped_attested:
        raise RemoteDeployError("A implantação exige a declaração explícita --server-stopped.")
    root = _safe_remote_root(remote_root)
    if state_dir.is_symlink():
        raise RemoteDeployError("A pasta de estado não pode ser um link simbólico.")
    bank_remote = posixpath.join(root, "TraderPlusBankDatabase")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    short_hash = package.original_sha256[:12]
    temp_name = f".zellno-upload-{package.steamid}-{stamp}.tmp"
    backup_name = f"{package.account_name}.zellno-backup-{stamp}-{short_hash}"
    failed_name = f"{package.account_name}.zellno-failed-{stamp}"
    proposed_bytes = package.proposed_path.read_bytes()
    ftp = ftp_factory()
    audit_path = state_dir / "remote-audit.jsonl"
    local_backup_path = (
        state_dir
        / "remote-backups"
        / f"{package.account_name}.{stamp}.{short_hash}.remote-before.json"
    )
    mutation_started = False
    rollback_performed = False
    deployment_verified = False
    remote_current: bytes | None = None
    try:
        ftp.connect(host=host, port=port, timeout=timeout)
        ftp.login(user=user, passwd=password)
        ftp.set_pasv(True)
        ftp.cwd(bank_remote)
        names = _remote_names(ftp)
        if package.account_name not in names:
            raise RemoteDeployError("A conta de destino não existe no diretório remoto.")
        if any(name in names for name in (temp_name, backup_name, failed_name)):
            raise RemoteDeployError("Um nome temporário ou de backup já existe no servidor.")

        remote_current = _retrieve_bytes(ftp, package.account_name)
        current_hash = _sha256_bytes(remote_current)
        if current_hash != package.original_sha256:
            raise RemoteDeployError(
                "O arquivo remoto mudou desde o snapshot; implantação bloqueada antes do upload."
            )
        local_backup_path.parent.mkdir(parents=True, exist_ok=True)
        with local_backup_path.open("xb") as handle:
            handle.write(remote_current)
            handle.flush()
            os.fsync(handle.fileno())
        if _sha256_file(local_backup_path) != current_hash:
            raise RemoteDeployError("O backup local pré-implantação falhou na verificação.")

        _append_audit(
            audit_path,
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "result": "authorized_pending",
                "scope": "remote_ftp",
                "account_file": package.account_name,
                "steamid64": package.steamid,
                "before_sha256": package.original_sha256,
                "proposed_sha256": package.proposed_sha256,
                "local_backup": str(local_backup_path),
                "planned_remote_backup": backup_name,
                "package": str(package.path),
            },
        )
        mutation_started = True
        ftp.storbinary(f"STOR {temp_name}", io.BytesIO(proposed_bytes))
        if _sha256_bytes(_retrieve_bytes(ftp, temp_name)) != package.proposed_sha256:
            try:
                ftp.delete(temp_name)
            finally:
                raise RemoteDeployError("O upload temporário falhou na leitura de verificação.")

        ftp.rename(package.account_name, backup_name)
        if _sha256_bytes(_retrieve_bytes(ftp, backup_name)) != package.original_sha256:
            try:
                ftp.rename(backup_name, package.account_name)
                rollback_performed = True
                ftp.delete(temp_name)
            except Exception as exc:
                raise RemoteDeployError(
                    "FALHA CRÍTICA: backup remoto divergente e restauração não confirmada."
                ) from exc
            raise RemoteDeployError("O backup remoto divergiu e o original foi restaurado.")
        try:
            ftp.rename(temp_name, package.account_name)
        except Exception as exc:
            try:
                ftp.rename(backup_name, package.account_name)
                rollback_performed = True
                try:
                    ftp.delete(temp_name)
                except Exception:
                    pass
            except Exception as rollback_exc:
                raise RemoteDeployError(
                    "FALHA CRÍTICA: ativação falhou e o rollback remoto não foi confirmado."
                ) from rollback_exc
            raise RemoteDeployError("A ativação falhou e o original remoto foi restaurado.") from exc

        final_bytes = _retrieve_bytes(ftp, package.account_name)
        if _sha256_bytes(final_bytes) != package.proposed_sha256:
            try:
                ftp.rename(package.account_name, failed_name)
                ftp.rename(backup_name, package.account_name)
                restored = _retrieve_bytes(ftp, package.account_name)
                if _sha256_bytes(restored) != package.original_sha256:
                    raise RemoteDeployError("Rollback executado, mas o original restaurado diverge.")
                rollback_performed = True
            except Exception as exc:
                raise RemoteDeployError(
                    "FALHA CRÍTICA: proposta inválida e rollback remoto não confirmado."
                ) from exc
            raise RemoteDeployError("A verificação final falhou e o original remoto foi restaurado.")

        deployment_verified = True

        try:
            ftp.quit()
        except all_errors:
            ftp.close()
        try:
            _append_audit(
                audit_path,
                {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "result": "deployed",
                    "scope": "remote_ftp",
                    "account_file": package.account_name,
                    "steamid64": package.steamid,
                    "before_sha256": package.original_sha256,
                    "after_sha256": package.proposed_sha256,
                    "remote_backup": backup_name,
                    "local_backup": str(local_backup_path),
                    "package": str(package.path),
                },
            )
        except OSError as exc:
            raise RemoteDeployError(
                "ATENÇÃO: implantação remota confirmada, mas a auditoria final falhou; "
                "não repita a operação."
            ) from exc
        return RemoteDeployResult(
            account_name=package.account_name,
            steamid=package.steamid,
            remote_backup_name=backup_name,
            local_backup_path=local_backup_path,
            audit_path=audit_path,
            before_sha256=package.original_sha256,
            after_sha256=package.proposed_sha256,
        )
    except (RemoteDeployError, OSError, *all_errors) as exc:
        if mutation_started and not deployment_verified and not rollback_performed:
            rollback_performed, ftp = _attempt_recovery(
                ftp,
                package,
                host=host,
                port=port,
                user=user,
                password=password,
                timeout=timeout,
                bank_remote=bank_remote,
                temp_name=temp_name,
                backup_name=backup_name,
                failed_name=failed_name,
                ftp_factory=ftp_factory,
            )
        try:
            ftp.close()
        except Exception:
            pass
        if mutation_started:
            try:
                _append_audit(
                    audit_path,
                    {
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "result": "failed_rolled_back" if rollback_performed else "failed",
                        "scope": "remote_ftp",
                        "account_file": package.account_name,
                        "steamid64": package.steamid,
                        "before_sha256": package.original_sha256,
                        "proposed_sha256": package.proposed_sha256,
                        "rollback_performed": rollback_performed,
                        "error": str(exc),
                        "package": str(package.path),
                    },
                )
            except OSError:
                pass
        if mutation_started and not deployment_verified and not rollback_performed:
            raise RemoteDeployError(
                "FALHA CRÍTICA: a implantação falhou e a restauração remota não foi confirmada."
            ) from exc
        if isinstance(exc, RemoteDeployError):
            raise
        raise RemoteDeployError(f"Falha na implantação FTP: {exc}") from exc
