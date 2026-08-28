from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str


@dataclass
class GeneralConfig:
    path: Path
    version: str
    licences: tuple[str, ...]
    issues: list[Issue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


@dataclass
class Account:
    path: Path
    filename_steamid: str | None
    raw: dict[str, Any] | None
    version: str | None = None
    steamid: str | None = None
    name: str | None = None
    money_amount: int | None = None
    max_amount: int | None = None
    licences: tuple[str, ...] = ()
    configured_licences: tuple[str, ...] = ()
    unknown_licences: tuple[str, ...] = ()
    missing_licences: tuple[str, ...] = ()
    issues: list[Issue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def status(self) -> str:
        if any(issue.code == "identity_mismatch" for issue in self.issues):
            return "INCONSISTENTE"
        if not self.valid:
            return "INVÁLIDA"
        if self.issues:
            return "VÁLIDA COM AVISOS"
        return "VÁLIDA"


@dataclass(frozen=True)
class ChangePlan:
    operation: str
    account_path: Path
    name: str
    steamid: str
    before_balance: int
    after_balance: int
    before_licences: tuple[str, ...]
    after_licences: tuple[str, ...]

    @property
    def balance_changed(self) -> bool:
        return self.before_balance != self.after_balance

    @property
    def licences_changed(self) -> bool:
        return self.before_licences != self.after_licences

    @property
    def has_changes(self) -> bool:
        return self.balance_changed or self.licences_changed

    @property
    def added_licences(self) -> tuple[str, ...]:
        return tuple(item for item in self.after_licences if item not in self.before_licences)

    @property
    def removed_licences(self) -> tuple[str, ...]:
        return tuple(item for item in self.before_licences if item not in self.after_licences)
