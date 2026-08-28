from __future__ import annotations

from .models import Account, ChangePlan, GeneralConfig


class PlanError(ValueError):
    """Raised when a safe dry-run plan cannot be generated."""


def _base_plan(
    account: Account,
    operation: str,
    *,
    balance: int | None = None,
    licences: tuple[str, ...] | None = None,
) -> ChangePlan:
    if not account.valid:
        raise PlanError("A conta é inválida ou inconsistente; planejamento bloqueado.")
    if account.name is None or account.steamid is None:
        raise PlanError("A conta não possui identidade válida.")
    if account.money_amount is None or account.max_amount is None:
        raise PlanError("A conta não possui valores bancários válidos.")

    after_balance = account.money_amount if balance is None else balance
    after_licences = account.licences if licences is None else licences
    if after_balance < 0:
        raise PlanError("O saldo proposto não pode ser negativo.")
    if after_balance > account.max_amount:
        raise PlanError(
            f"O saldo proposto excede o limite da conta ({account.max_amount})."
        )
    if len(set(after_licences)) != len(after_licences):
        raise PlanError("O estado proposto contém licenças duplicadas.")

    return ChangePlan(
        operation=operation,
        account_path=account.path,
        name=account.name,
        steamid=account.steamid,
        before_balance=account.money_amount,
        after_balance=after_balance,
        before_licences=account.licences,
        after_licences=after_licences,
    )


def profile_normal(account: Account) -> ChangePlan:
    return _base_plan(account, "Perfil: Jogador normal", balance=0, licences=())


def profile_test(
    account: Account,
    config: GeneralConfig,
    *,
    balance: int,
    licences: list[str],
) -> ChangePlan:
    proposed = tuple(licences)
    _validate_selected_licences(proposed, config)
    return _base_plan(
        account,
        "Perfil: Teste administrativo",
        balance=balance,
        licences=proposed,
    )


def balance_set(account: Account, amount: int) -> ChangePlan:
    return _base_plan(account, "Saldo: definir valor exato", balance=amount)


def balance_zero(account: Account) -> ChangePlan:
    return _base_plan(account, "Saldo: zerar", balance=0)


def licence_add(account: Account, config: GeneralConfig, licence: str) -> ChangePlan:
    _validate_selected_licences((licence,), config)
    proposed = account.licences
    if licence not in proposed:
        proposed = proposed + (licence,)
    return _base_plan(account, f"Licença: adicionar {licence}", licences=proposed)


def licence_remove(account: Account, licence: str) -> ChangePlan:
    proposed = tuple(item for item in account.licences if item != licence)
    return _base_plan(account, f"Licença: remover {licence}", licences=proposed)


def licence_clear(account: Account) -> ChangePlan:
    return _base_plan(account, "Licenças: remover todas", licences=())


def _validate_selected_licences(licences: tuple[str, ...], config: GeneralConfig) -> None:
    duplicates = sorted({item for item in licences if licences.count(item) > 1})
    if duplicates:
        raise PlanError("Licenças repetidas na seleção: " + ", ".join(duplicates))
    unknown = tuple(item for item in licences if item not in config.licences)
    if unknown:
        raise PlanError(
            "Só é possível adicionar licenças da configuração ativa: " + ", ".join(unknown)
        )
