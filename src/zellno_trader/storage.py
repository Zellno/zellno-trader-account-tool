from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .loader import load_account
from .models import Account, ChangePlan, GeneralConfig


class StorageError(RuntimeError):
    """Raised when a local apply or restore cannot complete safely."""


@dataclass(frozen=True)
class ApplyResult:
    backup_path: Path
    audit_path: Path
    before_sha256: str
    after_sha256: str


def default_state_dir(accounts_dir: Path) -> Path:
    return accounts_dir / ".zellno-trader-account-tool"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _candidate_data(account: Account, plan: ChangePlan) -> dict[str, Any]:
    if account.raw is None:
        raise StorageError("A conta não possui documento JSON válido.")
    candidate = copy.deepcopy(account.raw)
    candidate["MoneyAmount"] = plan.after_balance
    candidate["Licences"] = list(plan.after_licences)

    if set(candidate) != set(account.raw):
        raise StorageError("A geração alterou o conjunto de campos do JSON.")
    for key, value in account.raw.items():
        if key not in {"MoneyAmount", "Licences"} and candidate[key] != value:
            raise StorageError(f"Campo fora do escopo foi alterado: {key}")
    return candidate


def _write_candidate_temp(target: Path, candidate: dict[str, Any]) -> Path:
    mode = stat.S_IMODE(target.stat().st_mode)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temp_path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(candidate, handle, ensure_ascii=False, indent=4)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        reread = json.loads(temp_path.read_text(encoding="utf-8"))
        if reread != candidate:
            raise StorageError("O arquivo temporário não corresponde ao estado proposto.")
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _create_backup(target: Path, backup_dir: Path, before_hash: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_name = f"{target.name}.{_timestamp()}.{before_hash[:12]}.backup.json"
    backup_path = backup_dir / backup_name
    with target.open("rb") as source, backup_path.open("xb") as destination:
        shutil.copyfileobj(source, destination)
        destination.flush()
        os.fsync(destination.fileno())
    shutil.copystat(target, backup_path)
    if _sha256(backup_path) != before_hash:
        raise StorageError("O backup não corresponde ao arquivo original.")
    return backup_path


def _atomic_restore_file(backup_path: Path, target: Path) -> None:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{target.name}.rollback.",
        suffix=".tmp",
        dir=target.parent,
    )
    rollback_path = Path(raw_path)
    try:
        with backup_path.open("rb") as source, os.fdopen(descriptor, "wb") as destination:
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(rollback_path, stat.S_IMODE(backup_path.stat().st_mode))
        os.replace(rollback_path, target)
    finally:
        rollback_path.unlink(missing_ok=True)


def _append_audit(
    audit_path: Path,
    account: Account,
    plan: ChangePlan,
    backup_path: Path,
    before_hash: str,
    after_hash: str,
) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "result": "applied",
        "scope": "local_only",
        "operation": plan.operation,
        "account_file": account.path.name,
        "name": plan.name,
        "steamid64": plan.steamid,
        "before": {
            "money_amount": plan.before_balance,
            "licences": list(plan.before_licences),
            "sha256": before_hash,
        },
        "after": {
            "money_amount": plan.after_balance,
            "licences": list(plan.after_licences),
            "sha256": after_hash,
        },
        "backup": str(backup_path),
    }
    with audit_path.open("a", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def apply_plan_local(
    account: Account,
    plan: ChangePlan,
    config: GeneralConfig,
    state_dir: Path,
) -> ApplyResult:
    target = account.path
    if not plan.has_changes:
        raise StorageError("Não existem alterações para aplicar.")
    if not account.valid or account.raw is None:
        raise StorageError("A conta é inválida ou inconsistente.")
    if target.is_symlink():
        raise StorageError("Contas acessadas por link simbólico não podem ser alteradas.")
    if target.resolve().parent != target.parent.resolve():
        raise StorageError("O destino da conta não pertence à pasta esperada.")

    candidate = _candidate_data(account, plan)
    before_hash = _sha256(target)
    backup_path = _create_backup(target, state_dir / "backups", before_hash)
    temp_path = _write_candidate_temp(target, candidate)
    audit_path = state_dir / "audit.jsonl"

    try:
        staged = load_account(
            temp_path,
            config,
            expected_filename=target.name,
        )
        if not staged.valid:
            raise StorageError("O arquivo temporário falhou na validação estrutural prévia.")
        if staged.money_amount != plan.after_balance or staged.licences != plan.after_licences:
            raise StorageError("O arquivo temporário não corresponde ao plano confirmado.")
        if staged.raw != candidate:
            raise StorageError("O arquivo temporário diverge do documento proposto.")

        os.replace(temp_path, target)
        applied = load_account(target, config)
        if not applied.valid:
            raise StorageError("A conta gravada falhou na validação posterior.")
        if applied.money_amount != plan.after_balance or applied.licences != plan.after_licences:
            raise StorageError("A conta gravada não corresponde ao plano confirmado.")
        if applied.raw is None:
            raise StorageError("A conta gravada não pôde ser relida.")
        for key, value in account.raw.items():
            if key not in {"MoneyAmount", "Licences"} and applied.raw.get(key) != value:
                raise StorageError(f"Campo fora do escopo mudou após a gravação: {key}")
        after_hash = _sha256(target)
        _append_audit(audit_path, account, plan, backup_path, before_hash, after_hash)
    except Exception as exc:
        _atomic_restore_file(backup_path, target)
        if _sha256(target) != before_hash:
            raise StorageError("Falha crítica: não foi possível restaurar o original.") from exc
        if isinstance(exc, StorageError):
            raise
        raise StorageError(f"Aplicação cancelada e original restaurado: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)

    return ApplyResult(
        backup_path=backup_path,
        audit_path=audit_path,
        before_sha256=before_hash,
        after_sha256=after_hash,
    )


def list_backups(state_dir: Path) -> list[Path]:
    backup_dir = state_dir / "backups"
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob("Account_*.backup.json"), reverse=True)


def resolve_backup(state_dir: Path, backup_name: str) -> Path:
    if Path(backup_name).name != backup_name:
        raise StorageError("Informe somente o nome do arquivo de backup.")
    backup_dir = (state_dir / "backups").resolve()
    candidate = (backup_dir / backup_name).resolve()
    if candidate.parent != backup_dir or not candidate.is_file():
        raise StorageError("Backup não encontrado na pasta administrada pela ferramenta.")
    return candidate


def restore_plan(
    current: Account,
    backup_path: Path,
    config: GeneralConfig,
) -> ChangePlan:
    backup = load_account(
        backup_path,
        config,
        expected_filename=current.path.name,
    )
    if not current.valid or not backup.valid:
        raise StorageError("A conta atual ou o backup é inválido.")
    if backup.steamid != current.steamid:
        raise StorageError("O backup pertence a outro SteamID64.")
    if None in {current.name, current.steamid, current.money_amount, backup.money_amount}:
        raise StorageError("Não foi possível determinar o estado da restauração.")
    return ChangePlan(
        operation=f"Restaurar backup: {backup_path.name}",
        account_path=current.path,
        name=current.name or "",
        steamid=current.steamid or "",
        before_balance=current.money_amount or 0,
        after_balance=backup.money_amount or 0,
        before_licences=current.licences,
        after_licences=backup.licences,
    )
