from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Iterable

from . import __version__
from .loader import load_account, load_accounts, load_general_config
from .models import Account, ChangePlan, GeneralConfig
from .planning import (
    PlanError,
    balance_set,
    balance_zero,
    licence_add,
    licence_clear,
    licence_remove,
    profile_normal,
    profile_test,
)
from .storage import (
    StorageError,
    apply_plan_local,
    default_state_dir,
    list_backups,
    resolve_backup,
    restore_plan,
)
from .remote import RemoteError, create_snapshot
from .deployment import (
    DeploymentError,
    prepare_deployment,
    validate_snapshot_target,
)
from .remote_deploy import (
    RemoteDeployError,
    deploy_package_ftp,
    validate_deployment_package,
)
from .economy import (
    DEFAULT_SIGNALS,
    EconomyAuditError,
    audit_economy,
    render_text,
    write_reports,
)


def _money(value: int | None) -> str:
    return "—" if value is None else f"{value:,}".replace(",", ".")


def _print_config_errors(config: GeneralConfig) -> bool:
    if config.valid:
        return False
    print(f"ERRO: configuração inválida: {config.path}", file=sys.stderr)
    for issue in config.issues:
        print(f"- [{issue.code}] {issue.message}", file=sys.stderr)
    return True


def _print_list(accounts: Iterable[Account]) -> int:
    accounts = list(accounts)
    if not accounts:
        print("Nenhuma conta do TraderPlus encontrada.")
        return 0
    print(f"{'STATUS':<18} {'NOME':<24} {'STEAMID64':<19} {'SALDO':>12}  ARQUIVO")
    print("-" * 110)
    result = 0
    for account in accounts:
        if not account.valid:
            result = 2
        print(
            f"{account.status:<18} "
            f"{(account.name or '—')[:24]:<24} "
            f"{(account.steamid or '—'):<19} "
            f"{_money(account.money_amount):>12}  "
            f"{account.path.name}"
        )
    print(f"\nTotal: {len(accounts)} conta(s). Modo somente leitura.")
    return result


def _print_account(account: Account) -> int:
    print(f"Status:          {account.status}")
    print(f"Arquivo:         {account.path.name}")
    print(f"Nome:            {account.name or '—'}")
    print(f"SteamID64:       {account.steamid or '—'}")
    print(f"ID no arquivo:   {account.filename_steamid or '—'}")
    print(f"Versão:          {account.version or '—'}")
    print(f"Saldo:           {_money(account.money_amount)}")
    print(f"Limite:          {_money(account.max_amount)}")
    print("Seguros:         fora do escopo (não inspecionados)")

    print("\nLicenças configuradas possuídas:")
    if account.configured_licences:
        for licence in account.configured_licences:
            print(f"  - {licence}")
    else:
        print("  nenhuma")

    print("\nLicenças antigas ou desconhecidas:")
    if account.unknown_licences:
        for licence in account.unknown_licences:
            print(f"  - {licence}")
    else:
        print("  nenhuma")

    print("\nLicenças configuradas não possuídas:")
    if account.missing_licences:
        for licence in account.missing_licences:
            print(f"  - {licence}")
    else:
        print("  nenhuma")

    if account.issues:
        print("\nDiagnóstico:")
        for issue in account.issues:
            label = "ERRO" if issue.severity == "error" else "AVISO"
            print(f"  - {label} [{issue.code}]: {issue.message}")
    else:
        print("\nDiagnóstico: nenhuma inconsistência encontrada.")

    print("\nModo somente leitura. Nenhum arquivo foi alterado.")
    return 0 if account.valid else 2


def _select_accounts(args: argparse.Namespace, accounts: list[Account]) -> list[Account]:
    if getattr(args, "file", None):
        return [account for account in accounts if account.path.name == args.file]
    if getattr(args, "steamid", None):
        return [
            account
            for account in accounts
            if account.filename_steamid == args.steamid or account.steamid == args.steamid
        ]
    folded = args.name.casefold()
    return [account for account in accounts if account.name and account.name.casefold() == folded]


def _add_selector(parser: argparse.ArgumentParser) -> None:
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--name", help="Nome exato, sem diferenciar maiúsculas")
    selector.add_argument("--steamid", help="SteamID64 com 17 dígitos")
    selector.add_argument("--file", help="Nome exato do arquivo Account_*.json")


def _print_licence_state(title: str, licences: tuple[str, ...]) -> None:
    print(title)
    if licences:
        for licence in licences:
            print(f"  - {licence}")
    else:
        print("  nenhuma")


def _print_plan(
    plan: ChangePlan,
    *,
    apply_requested: bool = False,
    package_requested: bool = False,
) -> int:
    if package_requested:
        title = "PLANO DE PREPARAÇÃO DO PACOTE LOCAL"
    else:
        title = "PLANO DE APLICAÇÃO LOCAL" if apply_requested else "DRY-RUN — PLANO DE ALTERAÇÃO"
    print(title)
    print("=" * 72)
    print(f"Operação:        {plan.operation}")
    print(f"Arquivo:         {plan.account_path.name}")
    print(f"Nome:            {plan.name}")
    print(f"SteamID64:       {plan.steamid}")

    print("\nESTADO ATUAL")
    print(f"Saldo:           {_money(plan.before_balance)}")
    _print_licence_state("Licenças:", plan.before_licences)

    print("\nESTADO PROPOSTO")
    print(f"Saldo:           {_money(plan.after_balance)}")
    _print_licence_state("Licenças:", plan.after_licences)

    print("\nALTERAÇÕES")
    if not plan.has_changes:
        print("Nenhuma alteração necessária. O estado desejado já está aplicado.")
    else:
        if plan.balance_changed:
            print(f"MoneyAmount: {_money(plan.before_balance)} -> {_money(plan.after_balance)}")
        if plan.added_licences:
            print("Licenças adicionadas:")
            for licence in plan.added_licences:
                print(f"  + {licence}")
        if plan.removed_licences:
            print("Licenças removidas:")
            for licence in plan.removed_licences:
                print(f"  - {licence}")

    if package_requested:
        print("\nModo: PREPARAÇÃO LOCAL SOLICITADA")
        print("Nenhum pacote foi criado até a confirmação do SteamID64.")
    elif apply_requested:
        print("\nModo: APLICAÇÃO LOCAL SOLICITADA")
        print("Nenhum arquivo foi alterado até a confirmação do SteamID64.")
    else:
        print("\nModo: DRY-RUN")
        print("Nenhum arquivo foi criado, gravado, renomeado ou substituído.")
    return 0


def _add_apply(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplicar somente à cópia local após confirmação explícita",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zellno-trader",
        description="Auditoria somente leitura de contas do TraderPlus.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--accounts-dir", type=Path, help="Pasta local com Account_*.json")
    parser.add_argument(
        "--general-config",
        type=Path,
        help="Cópia local de TraderPlusGeneralConfig.json",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="Pasta de backups e auditoria; padrão: <accounts-dir>/.zellno-trader-account-tool",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    economy = commands.add_parser("economy", help="Auditar a economia usando snapshots locais")
    economy_commands = economy.add_subparsers(dest="economy_command", required=True)
    audit = economy_commands.add_parser(
        "audit",
        help="Comparar snapshots sem abrir conexão FTP nem alterar arquivos",
    )
    audit.add_argument(
        "--snapshots-dir",
        required=True,
        type=Path,
        help="Pasta que contém snapshot-*",
    )
    audit.add_argument(
        "--signal",
        action="append",
        type=int,
        help="Variação positiva suspeita; repita para configurar várias",
    )
    audit.add_argument(
        "--destination",
        type=Path,
        help="Criar relatórios TXT, JSON e CSV nesta pasta",
    )
    audit.add_argument(
        "--remote-audit",
        type=Path,
        help="Cruzar implantações homologadas do remote-audit.jsonl",
    )
    account = commands.add_parser("account", help="Consultar contas")
    account_commands = account.add_subparsers(dest="account_command", required=True)
    account_commands.add_parser("list", help="Listar contas")
    show = account_commands.add_parser("show", help="Mostrar uma conta")
    _add_selector(show)

    plan = account_commands.add_parser("plan", help="Simular alterações sem gravar arquivos")
    plan_kinds = plan.add_subparsers(dest="plan_kind", required=True)

    profile = plan_kinds.add_parser("profile", help="Simular um perfil predefinido")
    profile_kinds = profile.add_subparsers(dest="profile_kind", required=True)
    normal = profile_kinds.add_parser("normal", help="Saldo zero e nenhuma licença")
    _add_selector(normal)
    _add_apply(normal)
    test = profile_kinds.add_parser("test", help="Saldo e licenças finais selecionados")
    _add_selector(test)
    test.add_argument("--balance", required=True, type=int, help="Saldo final exato")
    test.add_argument(
        "--licence",
        action="append",
        default=[],
        help="Licença final; repita a opção para selecionar várias",
    )
    _add_apply(test)

    balance = plan_kinds.add_parser("balance", help="Simular alteração de saldo")
    balance_kinds = balance.add_subparsers(dest="balance_kind", required=True)
    set_balance = balance_kinds.add_parser("set", help="Definir saldo exato")
    _add_selector(set_balance)
    set_balance.add_argument("--amount", required=True, type=int, help="Saldo final exato")
    _add_apply(set_balance)
    zero = balance_kinds.add_parser("zero", help="Zerar saldo")
    _add_selector(zero)
    _add_apply(zero)

    licence = plan_kinds.add_parser("licence", help="Simular alteração de licença")
    licence_kinds = licence.add_subparsers(dest="licence_kind", required=True)
    add = licence_kinds.add_parser("add", help="Adicionar uma licença configurada")
    _add_selector(add)
    add.add_argument("--licence", required=True, help="Nome exato da licença")
    _add_apply(add)
    remove = licence_kinds.add_parser("remove", help="Remover uma licença possuída")
    _add_selector(remove)
    remove.add_argument("--licence", required=True, help="Nome exato da licença")
    _add_apply(remove)
    clear = licence_kinds.add_parser("clear", help="Remover todas as licenças")
    _add_selector(clear)
    _add_apply(clear)

    backup = commands.add_parser("backup", help="Listar ou restaurar backups locais")
    backup_commands = backup.add_subparsers(dest="backup_command", required=True)
    backup_commands.add_parser("list", help="Listar backups administrados pela ferramenta")
    restore = backup_commands.add_parser("restore", help="Restaurar saldo e licenças de um backup")
    restore.add_argument("--file", required=True, help="Conta local que receberá a restauração")
    restore.add_argument("--backup", required=True, help="Nome exato do arquivo de backup")
    _add_apply(restore)

    remote = commands.add_parser("remote", help="Operações FTP estritamente somente leitura")
    remote_commands = remote.add_subparsers(dest="remote_command", required=True)
    snapshot = remote_commands.add_parser("snapshot", help="Baixar e validar um novo snapshot")
    snapshot.add_argument("--host", required=True, help="Servidor FTP")
    snapshot.add_argument("--port", type=int, default=21, help="Porta FTP; padrão: 21")
    snapshot.add_argument("--user", required=True, help="Usuário FTP")
    snapshot.add_argument("--destination", required=True, type=Path, help="Pasta local de snapshots")
    snapshot.add_argument(
        "--remote-root",
        default="/profile/TraderPlus",
        help="Raiz remota do TraderPlus",
    )
    snapshot.add_argument(
        "--allow-plain-ftp",
        action="store_true",
        help="Reconhecer explicitamente que as credenciais trafegarão sem TLS",
    )
    snapshot.add_argument(
        "--server-stopped",
        action="store_true",
        help="Declarar que o servidor foi parado antes do snapshot",
    )
    deploy = remote_commands.add_parser(
        "deploy",
        help="Validar ou implantar remotamente um pacote preparado",
    )
    deploy.add_argument("--package", required=True, type=Path, help="Pasta exata do pacote")
    deploy.add_argument("--host", required=True, help="Servidor FTP")
    deploy.add_argument("--port", type=int, default=21, help="Porta FTP; padrão: 21")
    deploy.add_argument("--user", required=True, help="Usuário FTP")
    deploy.add_argument(
        "--state-dir",
        required=True,
        type=Path,
        help="Pasta externa para backup local e auditoria remota",
    )
    deploy.add_argument(
        "--remote-root",
        default="/profile/TraderPlus",
        help="Raiz remota do TraderPlus",
    )
    deploy.add_argument(
        "--allow-plain-ftp",
        action="store_true",
        help="Reconhecer explicitamente que as credenciais trafegarão sem TLS",
    )
    deploy.add_argument(
        "--server-stopped",
        action="store_true",
        help="Declarar que o servidor está parado durante a implantação",
    )
    deploy.add_argument(
        "--apply",
        action="store_true",
        help="Executar a implantação após dupla confirmação",
    )

    deployment = commands.add_parser(
        "deployment",
        help="Preparar pacote local validado, sem envio FTP",
    )
    deployment_commands = deployment.add_subparsers(
        dest="deployment_command",
        required=True,
    )
    prepare = deployment_commands.add_parser(
        "prepare",
        help="Planejar ou criar um pacote a partir de snapshot parado",
    )
    prepare.add_argument("--snapshot", required=True, type=Path, help="Pasta exata do snapshot")
    prepare.add_argument("--file", required=True, help="Arquivo Account_*.json exato")
    prepare.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="Pasta onde os pacotes serão criados",
    )
    prepare_profiles = prepare.add_subparsers(dest="deployment_profile", required=True)
    deployment_normal = prepare_profiles.add_parser(
        "normal",
        help="Saldo zero e nenhuma licença",
    )
    deployment_normal.add_argument(
        "--create",
        action="store_true",
        help="Criar o pacote após confirmação explícita",
    )
    deployment_test = prepare_profiles.add_parser(
        "test",
        help="Saldo e licenças finais selecionados",
    )
    deployment_test.add_argument("--balance", required=True, type=int, help="Saldo final exato")
    deployment_test.add_argument(
        "--licence",
        action="append",
        default=[],
        help="Licença final; repita para selecionar várias",
    )
    deployment_test.add_argument(
        "--create",
        action="store_true",
        help="Criar o pacote após confirmação explícita",
    )
    return parser


def _build_plan(args: argparse.Namespace, account: Account, config: GeneralConfig) -> ChangePlan:
    if args.plan_kind == "profile":
        if args.profile_kind == "normal":
            return profile_normal(account)
        return profile_test(account, config, balance=args.balance, licences=args.licence)
    if args.plan_kind == "balance":
        if args.balance_kind == "zero":
            return balance_zero(account)
        return balance_set(account, args.amount)
    if args.licence_kind == "add":
        return licence_add(account, config, args.licence)
    if args.licence_kind == "remove":
        return licence_remove(account, args.licence)
    return licence_clear(account)


def _confirm_and_apply(
    plan: ChangePlan,
    account: Account,
    config: GeneralConfig,
    state_dir: Path,
    *,
    apply_requested: bool,
) -> int:
    _print_plan(plan, apply_requested=apply_requested)
    if not apply_requested:
        return 0
    if not plan.has_changes:
        print("\nAplicação desnecessária: o estado local já corresponde ao plano.")
        return 0

    print("\nCONFIRMAÇÃO OBRIGATÓRIA")
    try:
        confirmation = input(f"Digite o SteamID64 {plan.steamid} para confirmar: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAplicação cancelada. Nenhum arquivo foi alterado.")
        return 1
    if confirmation != plan.steamid:
        print("Confirmação incorreta. Nenhum arquivo foi alterado.")
        return 1

    try:
        result = apply_plan_local(account, plan, config, state_dir)
    except (StorageError, OSError) as exc:
        print(f"ERRO: aplicação local falhou: {exc}", file=sys.stderr)
        return 2

    print("\nAPLICAÇÃO LOCAL CONCLUÍDA")
    print(f"Backup:          {result.backup_path}")
    print(f"Auditoria:       {result.audit_path}")
    print(f"SHA-256 anterior: {result.before_sha256}")
    print(f"SHA-256 atual:    {result.after_sha256}")
    print("FTP:             não utilizado")
    print("Servidor:        não alterado")
    return 0


def _find_account_by_file(
    accounts_dir: Path,
    accounts: list[Account],
    filename: str,
    config: GeneralConfig,
) -> Account | None:
    candidate = accounts_dir / filename
    if candidate.parent.resolve() != accounts_dir.resolve():
        return None
    for account in accounts:
        if account.path.name == filename:
            return account
    if candidate.is_file():
        return load_account(candidate, config)
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "economy" and args.economy_command == "audit":
        signals = tuple(args.signal) if args.signal else DEFAULT_SIGNALS
        try:
            report = audit_economy(
                args.snapshots_dir,
                signals=signals,
                remote_audit_path=args.remote_audit,
            )
        except EconomyAuditError as exc:
            print(f"ERRO: auditoria econômica recusada: {exc}", file=sys.stderr)
            return 2
        print(render_text(report), end="")
        if args.destination is not None:
            try:
                files = write_reports(report, args.destination)
            except OSError as exc:
                print(f"ERRO: falha ao criar relatórios: {exc}", file=sys.stderr)
                return 2
            print("\nRELATÓRIOS CRIADOS")
            print(f"Pasta:       {files.path}")
            print(f"Texto:       {files.text_path}")
            print(f"JSON:        {files.json_path}")
            print(f"CSV:         {files.csv_path}")
            print(f"Manifesto:   {files.manifest_path}")
        return 0

    if args.command == "remote" and args.remote_command == "snapshot":
        if not args.allow_plain_ftp:
            print(
                "ERRO: FTP sem TLS exige a opção explícita --allow-plain-ftp.",
                file=sys.stderr,
            )
            return 1
        print("AVISO: a hospedagem receberá usuário e senha por FTP sem TLS.")
        print("A senha será usada somente nesta conexão e não será armazenada.")
        try:
            password = getpass.getpass("Senha FTP: ")
        except (EOFError, KeyboardInterrupt):
            print("\nSnapshot cancelado. Nenhuma credencial foi armazenada.")
            return 1
        try:
            result = create_snapshot(
                host=args.host,
                port=args.port,
                user=args.user,
                password=password,
                destination=args.destination,
                remote_root=args.remote_root,
                server_stopped_attested=args.server_stopped,
            )
        except RemoteError as exc:
            print(f"ERRO: {exc}", file=sys.stderr)
            return 2
        finally:
            password = ""
        print("\nSNAPSHOT FTP CONCLUÍDO")
        print(f"Pasta:             {result.path}")
        print(f"Manifesto:         {result.manifest_path}")
        print(f"Contas:            {result.account_count}")
        print(f"Contas inválidas:  {result.invalid_account_count}")
        print(f"Confiável p/ edição: {'sim' if result.trusted_for_editing else 'não'}")
        print("Operações remotas: somente LIST e RETR")
        if not args.server_stopped:
            print("AVISO: snapshot online é apenas informativo e não deve ser editado.")
        return 2 if result.invalid_account_count else 0

    if args.command == "remote" and args.remote_command == "deploy":
        try:
            package = validate_deployment_package(args.package)
        except RemoteDeployError as exc:
            print(f"ERRO: pacote recusado: {exc}", file=sys.stderr)
            return 2
        print("PLANO DE IMPLANTAÇÃO FTP")
        print("=" * 72)
        print(f"Pacote:           {package.path}")
        print(f"Arquivo:          {package.account_name}")
        print(f"Nome:             {package.manifest.get('name')}")
        print(f"SteamID64:        {package.steamid}")
        print(f"Operação:         {package.manifest.get('operation')}")
        print(f"SHA-256 atual:    {package.original_sha256}")
        print(f"SHA-256 proposto: {package.proposed_sha256}")
        print("Estratégia:       backup local + backup remoto + arquivo temporário")
        print("Verificação:      hash remoto antes, temporário após upload e destino final")
        if not args.apply:
            print("\nModo: DRY-RUN REMOTO")
            print("Nenhuma conexão FTP foi aberta e nenhum arquivo remoto foi alterado.")
            return 0
        if not args.server_stopped:
            print("ERRO: --apply exige a declaração --server-stopped.", file=sys.stderr)
            return 1
        if not args.allow_plain_ftp:
            print("ERRO: --apply exige o consentimento --allow-plain-ftp.", file=sys.stderr)
            return 1
        print("\nCONFIRMAÇÃO REMOTA OBRIGATÓRIA")
        try:
            steamid_confirmation = input(
                f"Digite o SteamID64 {package.steamid}: "
            ).strip()
            action_confirmation = input("Digite IMPLANTAR para continuar: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nImplantação cancelada. Nenhuma conexão FTP foi aberta.")
            return 1
        if steamid_confirmation != package.steamid or action_confirmation != "IMPLANTAR":
            print("Confirmação incorreta. Nenhuma conexão FTP foi aberta.")
            return 1
        print("AVISO: a hospedagem receberá usuário e senha por FTP sem TLS.")
        print("A senha será usada somente nesta conexão e não será armazenada.")
        try:
            password = getpass.getpass("Senha FTP: ")
        except (EOFError, KeyboardInterrupt):
            print("\nImplantação cancelada. Nenhuma credencial foi armazenada.")
            return 1
        try:
            result = deploy_package_ftp(
                package,
                host=args.host,
                port=args.port,
                user=args.user,
                password=password,
                state_dir=args.state_dir,
                remote_root=args.remote_root,
                server_stopped_attested=args.server_stopped,
            )
        except RemoteDeployError as exc:
            print(f"ERRO: implantação remota falhou: {exc}", file=sys.stderr)
            return 2
        finally:
            password = ""
        print("\nIMPLANTAÇÃO FTP CONCLUÍDA")
        print(f"Arquivo:            {result.account_name}")
        print(f"SteamID64:          {result.steamid}")
        print(f"Backup remoto:      {result.remote_backup_name}")
        print(f"Backup local:       {result.local_backup_path}")
        print(f"Auditoria:          {result.audit_path}")
        print(f"SHA-256 anterior:   {result.before_sha256}")
        print(f"SHA-256 implantado: {result.after_sha256}")
        print("Servidor:           declarado parado durante toda a operação")
        return 0

    if args.command == "deployment":
        try:
            target = validate_snapshot_target(args.snapshot, args.file)
            if args.deployment_profile == "normal":
                plan = profile_normal(target.account)
            else:
                plan = profile_test(
                    target.account,
                    target.config,
                    balance=args.balance,
                    licences=args.licence,
                )
        except (DeploymentError, PlanError) as exc:
            print(f"ERRO: preparação bloqueada: {exc}", file=sys.stderr)
            return 2

        _print_plan(plan, package_requested=args.create)
        print("\nORIGEM DO PLANO")
        print(f"Snapshot:        {target.snapshot_path}")
        print("Servidor parado: declarado no manifesto")
        print("Confiança:       conta selecionada válida")
        if not args.create:
            print("\nModo: DRY-RUN DE IMPLANTAÇÃO")
            print("Nenhum pacote foi criado e nenhum arquivo do snapshot foi alterado.")
            return 0
        if not plan.has_changes:
            print("\nPacote desnecessário: o estado desejado já corresponde ao snapshot.")
            return 0

        print("\nCONFIRMAÇÃO OBRIGATÓRIA")
        try:
            confirmation = input(
                f"Digite o SteamID64 {plan.steamid} para criar o pacote: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nPreparação cancelada. Nenhum pacote foi criado.")
            return 1
        if confirmation != plan.steamid:
            print("Confirmação incorreta. Nenhum pacote foi criado.")
            return 1
        try:
            result = prepare_deployment(target, plan, args.destination)
        except (DeploymentError, OSError) as exc:
            print(f"ERRO: preparação falhou: {exc}", file=sys.stderr)
            return 2
        print("\nPACOTE LOCAL DE IMPLANTAÇÃO CRIADO")
        print(f"Pasta:             {result.path}")
        print(f"Manifesto:         {result.manifest_path}")
        print(f"Original:          {result.original_path}")
        print(f"Proposto:          {result.proposed_path}")
        print(f"Diferenças:        {result.diff_path}")
        print(f"Auditoria:         {result.audit_path}")
        print(f"SHA-256 original:  {result.before_sha256}")
        print(f"SHA-256 proposto:  {result.proposed_sha256}")
        print("FTP:               não utilizado")
        print("Servidor:          não alterado")
        return 0

    if args.accounts_dir is None or args.general_config is None:
        print(
            "ERRO: --accounts-dir e --general-config são obrigatórios para operações locais.",
            file=sys.stderr,
        )
        return 1
    config = load_general_config(args.general_config)
    if _print_config_errors(config):
        return 2
    if not args.accounts_dir.is_dir():
        print(f"ERRO: pasta de contas não encontrada: {args.accounts_dir}", file=sys.stderr)
        return 1

    state_dir = args.state_dir or default_state_dir(args.accounts_dir)
    accounts = load_accounts(args.accounts_dir, config)

    if args.command == "backup":
        if args.backup_command == "list":
            backups = list_backups(state_dir)
            if not backups:
                print("Nenhum backup local encontrado.")
                return 0
            print("BACKUPS LOCAIS")
            print("=" * 72)
            for path in backups:
                print(f"{path.name} | {path.stat().st_size} bytes")
            print(f"\nTotal: {len(backups)} backup(s).")
            return 0

        current = _find_account_by_file(args.accounts_dir, accounts, args.file, config)
        if current is None:
            print("ERRO: conta local não encontrada ou nome de arquivo inválido.", file=sys.stderr)
            return 1
        try:
            backup_path = resolve_backup(state_dir, args.backup)
            plan = restore_plan(current, backup_path, config)
        except StorageError as exc:
            print(f"ERRO: {exc}", file=sys.stderr)
            return 2
        return _confirm_and_apply(
            plan,
            current,
            config,
            state_dir,
            apply_requested=args.apply,
        )

    if args.account_command == "list":
        return _print_list(accounts)

    if getattr(args, "file", None):
        candidate = args.accounts_dir / args.file
        if candidate.parent.resolve() != args.accounts_dir.resolve():
            print("ERRO: --file deve conter somente o nome do arquivo.", file=sys.stderr)
            return 1
        if candidate.is_file() and not any(account.path == candidate for account in accounts):
            loaded = load_account(candidate, config)
            if args.account_command == "show":
                return _print_account(loaded)
            try:
                plan = _build_plan(args, loaded, config)
                return _confirm_and_apply(
                    plan,
                    loaded,
                    config,
                    state_dir,
                    apply_requested=args.apply,
                )
            except PlanError as exc:
                print(f"ERRO: {exc}", file=sys.stderr)
                return 2

    selected = _select_accounts(args, accounts)
    if not selected:
        print("Conta do TraderPlus não encontrada.")
        print("O jogador pode ter jogado sem abrir uma conta no TraderPlus.")
        return 1
    if len(selected) > 1:
        print("ERRO: busca ambígua; mais de um arquivo corresponde ao jogador.", file=sys.stderr)
        for account in selected:
            print(f"- {account.path.name}: {account.status}", file=sys.stderr)
        print("Use --file para inspecionar cada arquivo explicitamente.", file=sys.stderr)
        return 2
    if args.account_command == "show":
        return _print_account(selected[0])
    try:
        plan = _build_plan(args, selected[0], config)
        return _confirm_and_apply(
            plan,
            selected[0],
            config,
            state_dir,
            apply_requested=args.apply,
        )
    except PlanError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
