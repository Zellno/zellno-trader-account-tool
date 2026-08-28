from __future__ import annotations

import csv
import hashlib
import io
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .loader import load_accounts, load_general_config


DEFAULT_SIGNALS = (5000, 33250, 150000)


class EconomyAuditError(RuntimeError):
    """Raised when local snapshots cannot be audited safely."""


@dataclass(frozen=True)
class Observation:
    snapshot: str
    created_at_utc: str
    server_stopped_attested: bool
    file: str
    steamid64: str
    name: str
    balance: int
    licences: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class RejectedAccount:
    snapshot: str
    file: str
    name: str | None
    steamid64: str | None
    status: str
    issues: tuple[str, ...]


@dataclass(frozen=True)
class Change:
    steamid64: str
    name: str
    before_snapshot: str
    after_snapshot: str
    before_at_utc: str
    after_at_utc: str
    before_balance: int
    after_balance: int
    balance_delta: int
    added_licences: tuple[str, ...]
    removed_licences: tuple[str, ...]
    matched_signals: tuple[int, ...]
    administrative: bool
    administrative_at_utc: str | None


@dataclass(frozen=True)
class EconomyReport:
    generated_at_utc: str
    snapshots_dir: str
    snapshot_count: int
    valid_observation_count: int
    rejected_accounts: tuple[RejectedAccount, ...]
    observations: tuple[Observation, ...]
    changes: tuple[Change, ...]
    latest_snapshot: str
    latest_at_utc: str
    latest_account_count: int
    latest_total_balance: int
    latest_mean_balance: float
    latest_median_balance: float
    richest_name: str | None
    richest_steamid64: str | None
    richest_balance: int | None
    richest_share_percent: float
    signals: tuple[int, ...]
    remote_audit_path: str | None
    administrative_change_count: int
    ignored_audit_record_count: int


@dataclass(frozen=True)
class EconomyReportFiles:
    path: Path
    text_path: Path
    json_path: Path
    csv_path: Path
    manifest_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EconomyAuditError(f"Manifesto inválido em {path.parent.name}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("created_at_utc"), str):
        raise EconomyAuditError(f"Manifesto inválido em {path.parent.name}: data ausente.")
    return raw


def _verify_snapshot(snapshot: Path, manifest: dict[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise EconomyAuditError(f"Snapshot {snapshot.name} não possui manifesto de arquivos.")
    declared: list[str] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise EconomyAuditError(f"Entrada de integridade inválida em {snapshot.name}.")
        relative = entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise EconomyAuditError(f"Entrada de integridade incompleta em {snapshot.name}.")
        declared.append(relative)
        candidate = snapshot / relative
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise EconomyAuditError(f"Arquivo ausente em {snapshot.name}: {relative}") from exc
        if snapshot.resolve() not in resolved.parents:
            raise EconomyAuditError(f"Caminho inseguro no manifesto de {snapshot.name}: {relative}")
        expected_size = entry.get("size")
        if (
            not resolved.is_file()
            or (isinstance(expected_size, int) and resolved.stat().st_size != expected_size)
            or _sha256(resolved) != expected
        ):
            raise EconomyAuditError(f"Integridade divergente em {snapshot.name}: {relative}")
    if len(declared) != len(set(declared)):
        raise EconomyAuditError(f"O manifesto de {snapshot.name} contém arquivos duplicados.")
    actual = sorted(
        str(path.relative_to(snapshot))
        for path in snapshot.rglob("*.json")
        if path.name != "snapshot-manifest.json"
    )
    if sorted(declared) != actual:
        raise EconomyAuditError(
            f"O conjunto de arquivos JSON de {snapshot.name} diverge do manifesto."
        )


def _signals_for(delta: int, signals: tuple[int, ...]) -> tuple[int, ...]:
    if delta <= 0:
        return ()
    return tuple(signal for signal in signals if delta == signal or delta % signal == 0)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp inválido") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp sem fuso horário")
    return parsed.astimezone(timezone.utc)


def _read_deployments(path: Path | None) -> tuple[list[dict[str, str]], int]:
    if path is None:
        return [], 0
    path = path.expanduser().resolve()
    if not path.is_file():
        raise EconomyAuditError(f"Auditoria remota não encontrada: {path}")
    accepted: list[dict[str, str]] = []
    ignored = 0
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise EconomyAuditError(f"Não foi possível ler a auditoria remota: {exc}") from exc
    required = (
        "timestamp_utc", "account_file", "steamid64", "before_sha256", "after_sha256"
    )
    for line in lines:
        try:
            record = json.loads(line)
            if (
                not isinstance(record, dict)
                or record.get("result") != "deployed"
                or record.get("scope") != "remote_ftp"
                or any(not isinstance(record.get(key), str) for key in required)
            ):
                raise ValueError("registro não homologado")
            _timestamp(record["timestamp_utc"])
        except (json.JSONDecodeError, ValueError):
            ignored += 1
            continue
        accepted.append({key: record[key] for key in required})
    return accepted, ignored


def audit_economy(
    snapshots_dir: Path,
    *,
    signals: Iterable[int] = DEFAULT_SIGNALS,
    remote_audit_path: Path | None = None,
) -> EconomyReport:
    snapshots_dir = snapshots_dir.expanduser().resolve()
    if not snapshots_dir.is_dir():
        raise EconomyAuditError(f"Pasta de snapshots não encontrada: {snapshots_dir}")
    clean_signals = tuple(sorted(set(signals)))
    if not clean_signals or any(isinstance(item, bool) or item <= 0 for item in clean_signals):
        raise EconomyAuditError("Os sinais econômicos devem ser inteiros positivos.")

    observations: list[Observation] = []
    rejected: list[RejectedAccount] = []
    snapshot_names: list[str] = []

    snapshot_paths = sorted(path for path in snapshots_dir.glob("snapshot-*") if path.is_dir())
    if not snapshot_paths:
        raise EconomyAuditError("Nenhum snapshot foi encontrado.")

    deployments, ignored_audit_records = _read_deployments(remote_audit_path)

    for snapshot in snapshot_paths:
        manifest = _read_manifest(snapshot / "snapshot-manifest.json")
        _verify_snapshot(snapshot, manifest)
        config_path = snapshot / "TraderPlusConfig" / "TraderPlusGeneralConfig.json"
        accounts_dir = snapshot / "TraderPlusBankDatabase"
        config = load_general_config(config_path)
        if not config.valid:
            raise EconomyAuditError(f"Configuração inválida em {snapshot.name}.")
        accounts = load_accounts(accounts_dir, config)
        snapshot_names.append(snapshot.name)
        created = manifest["created_at_utc"]
        stopped = bool(manifest.get("server_stopped_attested", False))
        declared_hashes = {
            entry["path"]: entry["sha256"]
            for entry in manifest["files"]
        }
        for account in accounts:
            if not account.valid:
                rejected.append(
                    RejectedAccount(
                        snapshot=snapshot.name,
                        file=account.path.name,
                        name=account.name,
                        steamid64=account.steamid,
                        status=account.status,
                        issues=tuple(issue.message for issue in account.issues),
                    )
                )
                continue
            if account.steamid is None or account.name is None or account.money_amount is None:
                raise EconomyAuditError(f"Conta válida incompleta em {snapshot.name}: {account.path.name}")
            observations.append(
                Observation(
                    snapshot=snapshot.name,
                    created_at_utc=created,
                    server_stopped_attested=stopped,
                    file=account.path.name,
                    steamid64=account.steamid,
                    name=account.name,
                    balance=account.money_amount,
                    licences=account.licences,
                    sha256=declared_hashes[
                        f"TraderPlusBankDatabase/{account.path.name}"
                    ],
                )
            )

    by_player: dict[str, list[Observation]] = {}
    for observation in observations:
        by_player.setdefault(observation.steamid64, []).append(observation)
    changes: list[Change] = []
    for steamid, history in sorted(by_player.items()):
        history.sort(key=lambda item: (item.created_at_utc, item.snapshot))
        for before, after in zip(history, history[1:]):
            delta = after.balance - before.balance
            matching_deployments = [
                item
                for item in deployments
                if item["steamid64"] == steamid
                and item["account_file"] == after.file
                and item["before_sha256"] == before.sha256
                and item["after_sha256"] == after.sha256
                and _timestamp(before.created_at_utc)
                <= _timestamp(item["timestamp_utc"])
                <= _timestamp(after.created_at_utc)
            ]
            administrative = len(matching_deployments) == 1
            changes.append(
                Change(
                    steamid64=steamid,
                    name=after.name,
                    before_snapshot=before.snapshot,
                    after_snapshot=after.snapshot,
                    before_at_utc=before.created_at_utc,
                    after_at_utc=after.created_at_utc,
                    before_balance=before.balance,
                    after_balance=after.balance,
                    balance_delta=delta,
                    added_licences=tuple(item for item in after.licences if item not in before.licences),
                    removed_licences=tuple(item for item in before.licences if item not in after.licences),
                    matched_signals=_signals_for(delta, clean_signals),
                    administrative=administrative,
                    administrative_at_utc=(
                        matching_deployments[0]["timestamp_utc"] if administrative else None
                    ),
                )
            )

    latest_name = snapshot_names[-1]
    latest = [item for item in observations if item.snapshot == latest_name]
    if not latest:
        raise EconomyAuditError(f"O snapshot mais recente {latest_name} não possui contas válidas.")
    balances = [item.balance for item in latest]
    richest = max(latest, key=lambda item: item.balance)
    total = sum(balances)
    share = (richest.balance / total * 100.0) if total else 0.0
    latest_at = max(item.created_at_utc for item in latest)
    return EconomyReport(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        snapshots_dir=str(snapshots_dir),
        snapshot_count=len(snapshot_names),
        valid_observation_count=len(observations),
        rejected_accounts=tuple(rejected),
        observations=tuple(observations),
        changes=tuple(changes),
        latest_snapshot=latest_name,
        latest_at_utc=latest_at,
        latest_account_count=len(latest),
        latest_total_balance=total,
        latest_mean_balance=statistics.fmean(balances),
        latest_median_balance=statistics.median(balances),
        richest_name=richest.name,
        richest_steamid64=richest.steamid64,
        richest_balance=richest.balance,
        richest_share_percent=share,
        signals=clean_signals,
        remote_audit_path=(
            str(remote_audit_path.expanduser().resolve()) if remote_audit_path else None
        ),
        administrative_change_count=sum(item.administrative for item in changes),
        ignored_audit_record_count=ignored_audit_records,
    )


def _money(value: int | float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{int(value):,}".replace(",", ".")


def render_text(report: EconomyReport) -> str:
    lines = [
        "AUDITORIA ECONÔMICA DO TRADERPLUS",
        "=" * 72,
        f"Snapshots validados:       {report.snapshot_count}",
        f"Observações válidas:       {report.valid_observation_count}",
        f"Contas rejeitadas:         {len(report.rejected_accounts)} ocorrência(s)",
        f"Alterações administrativas:{report.administrative_change_count:>9}",
        f"Registros audit. ignorados:{report.ignored_audit_record_count:>9}",
        f"Snapshot mais recente:     {report.latest_snapshot}",
        f"Data mais recente (UTC):   {report.latest_at_utc}",
        "",
        "ECONOMIA MAIS RECENTE",
        f"Contas válidas:            {report.latest_account_count}",
        f"Saldo total:               {_money(report.latest_total_balance)}",
        f"Saldo médio:               {_money(report.latest_mean_balance)}",
        f"Saldo mediano:             {_money(report.latest_median_balance)}",
        f"Maior saldo:               {report.richest_name} — {_money(report.richest_balance or 0)}",
        f"Participação do maior:     {report.richest_share_percent:.2f}%",
        "",
        "VARIAÇÕES ENTRE OBSERVAÇÕES",
    ]
    if not report.changes:
        lines.append("Nenhuma variação comparável.")
    for change in report.changes:
        signal = (
            " | ALERTA: múltiplo de " + ", ".join(_money(item) for item in change.matched_signals)
            if change.matched_signals and not change.administrative
            else ""
        )
        admin = " | ADMINISTRATIVA CONFIRMADA" if change.administrative else ""
        lines.append(
            f"{change.name} ({change.steamid64}): "
            f"{_money(change.before_balance)} -> {_money(change.after_balance)} "
            f"({change.balance_delta:+,})".replace(",", ".") + admin + signal
        )
        if change.administrative_at_utc:
            lines.append(f"  Implantação registrada em: {change.administrative_at_utc}")
        if change.added_licences:
            lines.append("  Licenças adicionadas: " + ", ".join(change.added_licences))
        if change.removed_licences:
            lines.append("  Licenças removidas: " + ", ".join(change.removed_licences))
    lines.extend(["", "CONTAS EXCLUÍDAS DAS MÉTRICAS"])
    if not report.rejected_accounts:
        lines.append("Nenhuma.")
    for item in report.rejected_accounts:
        lines.append(f"{item.snapshot} | {item.file} | {item.status}")
        for issue in item.issues:
            lines.append(f"  - {issue}")
    lines.extend(
        [
            "",
            "INTERPRETAÇÃO",
            "Alertas indicam variações líquidas compatíveis com os sinais configurados.",
            "Eles não comprovam a origem do dinheiro e podem ocultar ganhos seguidos de gastos.",
            "Modo somente leitura. Nenhum snapshot ou arquivo remoto foi alterado.",
        ]
    )
    return "\n".join(lines) + "\n"


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def render_json(report: EconomyReport) -> str:
    return json.dumps(_json_value(asdict(report)), ensure_ascii=False, indent=2) + "\n"


def render_csv(report: EconomyReport) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "steamid64", "name", "before_at_utc", "after_at_utc", "before_balance",
            "after_balance", "balance_delta", "added_licences", "removed_licences",
            "matched_signals",
            "administrative", "administrative_at_utc",
        ]
    )
    for change in report.changes:
        writer.writerow(
            [
                change.steamid64, change.name, change.before_at_utc, change.after_at_utc,
                change.before_balance, change.after_balance, change.balance_delta,
                " | ".join(change.added_licences), " | ".join(change.removed_licences),
                " | ".join(str(item) for item in change.matched_signals),
                "yes" if change.administrative else "no", change.administrative_at_utc or "",
            ]
        )
    return output.getvalue()


def write_reports(report: EconomyReport, destination: Path) -> EconomyReportFiles:
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    partial = destination / f".economy-audit-{stamp}-partial"
    final = destination / f"economy-audit-{stamp}"
    partial.mkdir()
    try:
        text_path = partial / "economy-audit.txt"
        json_path = partial / "economy-audit.json"
        csv_path = partial / "economy-changes.csv"
        text_path.write_text(render_text(report), encoding="utf-8")
        json_path.write_text(render_json(report), encoding="utf-8")
        csv_path.write_text(render_csv(report), encoding="utf-8", newline="")
        manifest = {
            "version": 1,
            "generated_at_utc": report.generated_at_utc,
            "mode": "local_read_only",
            "files": [
                {"path": path.name, "sha256": _sha256(path), "size": path.stat().st_size}
                for path in (text_path, json_path, csv_path)
            ],
        }
        manifest_path = partial / "report-manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        partial.rename(final)
    except Exception:
        import shutil

        shutil.rmtree(partial, ignore_errors=True)
        raise
    return EconomyReportFiles(
        path=final,
        text_path=final / text_path.name,
        json_path=final / json_path.name,
        csv_path=final / csv_path.name,
        manifest_path=final / manifest_path.name,
    )
