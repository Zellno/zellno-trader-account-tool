from __future__ import annotations

import copy
import difflib
import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .loader import load_account, load_general_config
from .models import Account, ChangePlan, GeneralConfig


class DeploymentError(RuntimeError):
    """Raised when a deployment package cannot be prepared safely."""


@dataclass(frozen=True)
class SnapshotTarget:
    snapshot_path: Path
    account: Account
    config: GeneralConfig
    source_sha256: str


@dataclass(frozen=True)
class DeploymentResult:
    path: Path
    manifest_path: Path
    original_path: Path
    proposed_path: Path
    diff_path: Path
    audit_path: Path
    before_sha256: str
    proposed_sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(snapshot_path: Path) -> dict[str, Any]:
    manifest_path = snapshot_path / "snapshot-manifest.json"
    if snapshot_path.is_symlink() or not snapshot_path.is_dir():
        raise DeploymentError("A pasta do snapshot é inválida ou é um link simbólico.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"Manifesto do snapshot inválido: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise DeploymentError("Versão do manifesto do snapshot não suportada.")
    if manifest.get("transport") != "plain_ftp_read_only":
        raise DeploymentError("O snapshot não foi criado pelo fluxo FTP somente leitura.")
    if manifest.get("server_stopped_attested") is not True:
        raise DeploymentError(
            "O snapshot foi obtido com o servidor online ou sem a declaração --server-stopped."
        )
    return manifest


def _manifest_file_hash(manifest: dict[str, Any], relative_path: str) -> str:
    matches = [item for item in manifest.get("files", []) if item.get("path") == relative_path]
    if len(matches) != 1 or not isinstance(matches[0].get("sha256"), str):
        raise DeploymentError(f"Arquivo ausente ou duplicado no manifesto: {relative_path}")
    return matches[0]["sha256"]


def validate_snapshot_target(snapshot_path: Path, filename: str) -> SnapshotTarget:
    if Path(filename).name != filename:
        raise DeploymentError("Informe somente o nome do arquivo Account_*.json.")
    manifest = _read_manifest(snapshot_path)
    account_entries = [item for item in manifest.get("accounts", []) if item.get("file") == filename]
    if len(account_entries) != 1:
        raise DeploymentError("A conta selecionada está ausente ou duplicada no manifesto.")
    entry = account_entries[0]
    issues = entry.get("issues", [])
    if any(issue.get("severity") == "error" for issue in issues if isinstance(issue, dict)):
        raise DeploymentError("A conta selecionada é inválida ou inconsistente no snapshot.")

    account_relative = f"TraderPlusBankDatabase/{filename}"
    config_relative = "TraderPlusConfig/TraderPlusGeneralConfig.json"
    account_path = snapshot_path / account_relative
    config_path = snapshot_path / config_relative
    for candidate in (account_path, config_path):
        if candidate.is_symlink() or not candidate.is_file():
            raise DeploymentError(f"Arquivo ausente ou inseguro no snapshot: {candidate.name}")
        if snapshot_path.resolve() not in candidate.resolve().parents:
            raise DeploymentError("Arquivo do snapshot escapou da pasta esperada.")

    account_hash = _manifest_file_hash(manifest, account_relative)
    config_hash = _manifest_file_hash(manifest, config_relative)
    if _sha256(account_path) != account_hash:
        raise DeploymentError("O hash da conta não corresponde ao manifesto do snapshot.")
    if _sha256(config_path) != config_hash:
        raise DeploymentError("O hash da configuração não corresponde ao manifesto do snapshot.")

    config = load_general_config(config_path)
    if not config.valid:
        raise DeploymentError("A configuração do snapshot é inválida.")
    account = load_account(account_path, config)
    if not account.valid:
        raise DeploymentError("A conta selecionada falhou na validação atual.")
    if account.steamid != entry.get("steamid64") or account.name != entry.get("name"):
        raise DeploymentError("A identidade atual da conta difere do manifesto.")
    return SnapshotTarget(snapshot_path, account, config, account_hash)


def _candidate(account: Account, plan: ChangePlan) -> dict[str, Any]:
    if account.raw is None:
        raise DeploymentError("A conta não possui documento JSON válido.")
    candidate = copy.deepcopy(account.raw)
    candidate["MoneyAmount"] = plan.after_balance
    candidate["Licences"] = list(plan.after_licences)
    if set(candidate) != set(account.raw):
        raise DeploymentError("A proposta alterou o conjunto de campos do JSON.")
    for key, value in account.raw.items():
        if key not in {"MoneyAmount", "Licences"} and candidate[key] != value:
            raise DeploymentError(f"Campo fora do escopo foi alterado: {key}")
    return candidate


def prepare_deployment(
    target: SnapshotTarget,
    plan: ChangePlan,
    destination: Path,
) -> DeploymentResult:
    if not plan.has_changes:
        raise DeploymentError("Não existem alterações para incluir no pacote.")
    if plan.account_path != target.account.path or plan.steamid != target.account.steamid:
        raise DeploymentError("O plano não pertence à conta validada do snapshot.")

    snapshot_resolved = target.snapshot_path.resolve()
    destination_resolved = destination.resolve()
    if destination.is_symlink():
        raise DeploymentError("A pasta de destino não pode ser um link simbólico.")
    if destination_resolved == snapshot_resolved or snapshot_resolved in destination_resolved.parents:
        raise DeploymentError("O destino do pacote não pode ficar dentro do snapshot.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    nonce = uuid.uuid4().hex
    destination.mkdir(parents=True, exist_ok=True)
    partial = destination / f".deployment-{stamp}-{nonce}-{plan.steamid}-partial"
    final = destination / f"deployment-{stamp}-{nonce}-{plan.steamid}"
    partial.mkdir()
    try:
        original_dir = partial / "original"
        proposed_dir = partial / "proposed"
        original_dir.mkdir()
        proposed_dir.mkdir()
        original_path = original_dir / target.account.path.name
        proposed_path = proposed_dir / target.account.path.name
        shutil.copyfile(target.account.path, original_path)
        if _sha256(original_path) != target.source_sha256:
            raise DeploymentError("A cópia original do pacote diverge do snapshot.")

        candidate = _candidate(target.account, plan)
        proposed_path.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )
        proposed = load_account(
            proposed_path,
            target.config,
            expected_filename=target.account.path.name,
        )
        if not proposed.valid or proposed.raw != candidate:
            raise DeploymentError("A proposta falhou na validação estrutural.")
        if proposed.money_amount != plan.after_balance or proposed.licences != plan.after_licences:
            raise DeploymentError("A proposta não corresponde ao plano confirmado.")

        before_hash = _sha256(original_path)
        proposed_hash = _sha256(proposed_path)
        before_text = json.dumps(target.account.raw, ensure_ascii=False, indent=4).splitlines()
        after_text = json.dumps(candidate, ensure_ascii=False, indent=4).splitlines()
        diff_path = partial / "changes.diff"
        diff_path.write_text(
            "\n".join(
                difflib.unified_diff(
                    before_text,
                    after_text,
                    fromfile=f"original/{target.account.path.name}",
                    tofile=f"proposed/{target.account.path.name}",
                    lineterm="",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        created_at = datetime.now(timezone.utc).isoformat()
        audit = {
            "timestamp_utc": created_at,
            "result": "deployment_prepared",
            "scope": "local_only_no_ftp_upload",
            "operation": plan.operation,
            "account_file": target.account.path.name,
            "name": plan.name,
            "steamid64": plan.steamid,
            "before": {
                "money_amount": plan.before_balance,
                "licences": list(plan.before_licences),
                "sha256": before_hash,
            },
            "proposed": {
                "money_amount": plan.after_balance,
                "licences": list(plan.after_licences),
                "sha256": proposed_hash,
            },
        }
        audit_path = partial / "audit.jsonl"
        audit_path.write_text(
            json.dumps(audit, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        manifest = {
            "version": 1,
            "created_at_utc": created_at,
            "status": "prepared_not_deployed",
            "server_stopped_attested": True,
            "account_trusted_for_preparation": True,
            "ftp_upload_performed": False,
            "source_snapshot": str(target.snapshot_path),
            "operation": plan.operation,
            "account_file": target.account.path.name,
            "name": plan.name,
            "steamid64": plan.steamid,
            "before": {
                "money_amount": plan.before_balance,
                "licences": list(plan.before_licences),
                "sha256": before_hash,
            },
            "proposed": {
                "money_amount": plan.after_balance,
                "licences": list(plan.after_licences),
                "sha256": proposed_hash,
            },
            "preserved_fields": sorted(
                key for key in (target.account.raw or {}) if key not in {"MoneyAmount", "Licences"}
            ),
            "files": {
                "original": f"original/{target.account.path.name}",
                "proposed": f"proposed/{target.account.path.name}",
                "diff": "changes.diff",
                "audit": "audit.jsonl",
            },
        }
        manifest_path = partial / "deployment-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        partial.rename(final)
        return DeploymentResult(
            path=final,
            manifest_path=final / manifest_path.name,
            original_path=final / "original" / target.account.path.name,
            proposed_path=final / "proposed" / target.account.path.name,
            diff_path=final / diff_path.name,
            audit_path=final / audit_path.name,
            before_sha256=before_hash,
            proposed_sha256=proposed_hash,
        )
    except Exception as exc:
        shutil.rmtree(partial, ignore_errors=True)
        if isinstance(exc, DeploymentError):
            raise
        raise DeploymentError(f"Falha ao preparar o pacote: {exc}") from exc
