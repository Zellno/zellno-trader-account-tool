from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import Account, GeneralConfig, Issue


ACCOUNT_FILENAME = re.compile(r"^Account_(\d{17})\.json$")
STEAMID64 = re.compile(r"^\d{17}$")


def _read_json(path: Path) -> tuple[Any | None, Issue | None]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle), None
    except FileNotFoundError:
        return None, Issue("error", "file_not_found", f"Arquivo não encontrado: {path}")
    except UnicodeDecodeError as exc:
        return None, Issue("error", "encoding", f"Codificação inválida: {exc}")
    except json.JSONDecodeError as exc:
        return None, Issue(
            "error",
            "invalid_json",
            f"JSON inválido na linha {exc.lineno}, coluna {exc.colno}: {exc.msg}",
        )
    except OSError as exc:
        return None, Issue("error", "read_error", f"Falha de leitura: {exc}")


def load_general_config(path: Path) -> GeneralConfig:
    raw, read_issue = _read_json(path)
    issues: list[Issue] = []
    if read_issue:
        issues.append(read_issue)
        return GeneralConfig(path=path, version="", licences=(), issues=issues)
    if not isinstance(raw, dict):
        issues.append(Issue("error", "root_type", "A configuração deve conter um objeto JSON."))
        return GeneralConfig(path=path, version="", licences=(), issues=issues)

    version = raw.get("Version")
    if not isinstance(version, str):
        issues.append(Issue("error", "version_type", "Version deve ser texto."))
        version = ""

    licences_raw = raw.get("Licences")
    licences: tuple[str, ...] = ()
    if not isinstance(licences_raw, list) or not all(isinstance(item, str) for item in licences_raw):
        issues.append(Issue("error", "licences_type", "Licences deve ser uma lista de textos."))
    else:
        licences = tuple(licences_raw)
        if len(set(licences)) != len(licences):
            issues.append(Issue("error", "duplicate_config_licence", "A configuração contém licenças duplicadas."))

    return GeneralConfig(path=path, version=version, licences=licences, issues=issues)


def _require_text(raw: dict[str, Any], key: str, issues: list[Issue]) -> str | None:
    value = raw.get(key)
    if not isinstance(value, str):
        issues.append(Issue("error", f"{key.lower()}_type", f"{key} deve ser texto."))
        return None
    return value


def _require_integer(raw: dict[str, Any], key: str, issues: list[Issue]) -> int | None:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        issues.append(Issue("error", f"{key.lower()}_type", f"{key} deve ser um número inteiro."))
        return None
    return value


def load_account(
    path: Path,
    config: GeneralConfig,
    *,
    expected_filename: str | None = None,
) -> Account:
    issues: list[Issue] = []
    identity_filename = expected_filename or path.name
    filename_match = ACCOUNT_FILENAME.fullmatch(identity_filename)
    filename_steamid = filename_match.group(1) if filename_match else None
    if filename_steamid is None:
        issues.append(
            Issue("error", "filename", "Nome esperado: Account_<SteamID64 com 17 dígitos>.json")
        )

    raw, read_issue = _read_json(path)
    if read_issue:
        issues.append(read_issue)
        return Account(path=path, filename_steamid=filename_steamid, raw=None, issues=issues)
    if not isinstance(raw, dict):
        issues.append(Issue("error", "root_type", "A conta deve conter um objeto JSON."))
        return Account(path=path, filename_steamid=filename_steamid, raw=None, issues=issues)

    version = _require_text(raw, "Version", issues)
    steamid = _require_text(raw, "SteamID64", issues)
    name = _require_text(raw, "Name", issues)
    money_amount = _require_integer(raw, "MoneyAmount", issues)
    max_amount = _require_integer(raw, "MaxAmount", issues)

    if steamid is not None and not STEAMID64.fullmatch(steamid):
        issues.append(Issue("error", "steamid_format", "SteamID64 deve conter exatamente 17 dígitos."))
    if filename_steamid and steamid and filename_steamid != steamid:
        issues.append(
            Issue(
                "error",
                "identity_mismatch",
                f"SteamID64 interno {steamid} difere do nome do arquivo {filename_steamid}.",
            )
        )

    if money_amount is not None and money_amount < 0:
        issues.append(Issue("error", "negative_balance", "MoneyAmount não pode ser negativo."))
    if max_amount is not None and max_amount < 0:
        issues.append(Issue("error", "negative_limit", "MaxAmount não pode ser negativo."))
    if money_amount is not None and max_amount is not None and money_amount > max_amount:
        issues.append(Issue("error", "balance_above_limit", "MoneyAmount é maior que MaxAmount."))

    licences_raw = raw.get("Licences")
    licences: tuple[str, ...] = ()
    if not isinstance(licences_raw, list) or not all(isinstance(item, str) for item in licences_raw):
        issues.append(Issue("error", "licences_type", "Licences deve ser uma lista de textos."))
    else:
        licences = tuple(licences_raw)
        duplicates = sorted({item for item in licences if licences.count(item) > 1})
        if duplicates:
            issues.append(
                Issue("warning", "duplicate_licence", f"Licenças duplicadas: {', '.join(duplicates)}")
            )

    configured = tuple(item for item in licences if item in config.licences)
    unknown = tuple(item for item in licences if item not in config.licences)
    missing = tuple(item for item in config.licences if item not in licences)
    if unknown:
        issues.append(
            Issue(
                "warning",
                "unknown_licence",
                "Licenças antigas ou não configuradas: " + ", ".join(unknown),
            )
        )
    if version and config.version and version != config.version:
        issues.append(
            Issue(
                "warning",
                "version_mismatch",
                f"Versão da conta {version} difere da configuração {config.version}.",
            )
        )

    return Account(
        path=path,
        filename_steamid=filename_steamid,
        raw=raw,
        version=version,
        steamid=steamid,
        name=name,
        money_amount=money_amount,
        max_amount=max_amount,
        licences=licences,
        configured_licences=configured,
        unknown_licences=unknown,
        missing_licences=missing,
        issues=issues,
    )


def load_accounts(directory: Path, config: GeneralConfig) -> list[Account]:
    if not directory.is_dir():
        return []
    return [load_account(path, config) for path in sorted(directory.glob("Account_*.json"))]
