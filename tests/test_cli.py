from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from zellno_trader.cli import main


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "TraderPlusGeneralConfig.json"
        self.config.write_text(
            json.dumps({"Version": "2.5", "Licences": ["Bob Licence"]}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_account(self, filename_id: str, internal_id: str, name: str = "Zellno") -> None:
        (self.root / f"Account_{filename_id}.json").write_text(
            json.dumps(
                {
                    "Version": "2.5",
                    "SteamID64": internal_id,
                    "Name": name,
                    "MoneyAmount": 0,
                    "MaxAmount": 1000000,
                    "Licences": [],
                    "Insurances": {},
                }
            ),
            encoding="utf-8",
        )

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        base = ["--accounts-dir", str(self.root), "--general-config", str(self.config)]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(base + list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_missing_account_explains_lazy_creation(self) -> None:
        code, stdout, _ = self.run_cli("account", "show", "--name", "Jota")
        self.assertEqual(code, 1)
        self.assertIn("pode ter jogado sem abrir uma conta", stdout)

    def test_ambiguous_name_is_blocked(self) -> None:
        self.write_account("76561198000000001", "76561198000000001")
        self.write_account("76561198000000002", "76561198000000001")
        code, _, stderr = self.run_cli("account", "show", "--name", "Zellno")
        self.assertEqual(code, 2)
        self.assertIn("busca ambígua", stderr)

    def test_explicit_file_displays_inconsistency(self) -> None:
        self.write_account("76561198000000002", "76561198000000001")
        code, stdout, _ = self.run_cli(
            "account", "show", "--file", "Account_76561198000000002.json"
        )
        self.assertEqual(code, 2)
        self.assertIn("INCONSISTENTE", stdout)

    def test_normal_profile_dry_run(self) -> None:
        self.write_account("76561198000000001", "76561198000000001")
        account_path = self.root / "Account_76561198000000001.json"
        original = account_path.read_bytes()
        code, stdout, _ = self.run_cli(
            "account",
            "plan",
            "profile",
            "normal",
            "--file",
            account_path.name,
        )
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN — PLANO DE ALTERAÇÃO", stdout)
        self.assertIn("Nenhum arquivo foi criado", stdout)
        self.assertEqual(account_path.read_bytes(), original)

    def test_inconsistent_plan_is_blocked(self) -> None:
        self.write_account("76561198000000002", "76561198000000001")
        code, _, stderr = self.run_cli(
            "account",
            "plan",
            "profile",
            "normal",
            "--file",
            "Account_76561198000000002.json",
        )
        self.assertEqual(code, 2)
        self.assertIn("planejamento bloqueado", stderr)

    def test_wrong_confirmation_does_not_write(self) -> None:
        self.write_account("76561198000000001", "76561198000000001")
        account_path = self.root / "Account_76561198000000001.json"
        original = account_path.read_bytes()
        with mock.patch("builtins.input", return_value="STEAMID ERRADO"):
            code, stdout, _ = self.run_cli(
                "account",
                "plan",
                "balance",
                "set",
                "--file",
                account_path.name,
                "--amount",
                "500000",
                "--apply",
            )
        self.assertEqual(code, 1)
        self.assertIn("Confirmação incorreta", stdout)
        self.assertEqual(account_path.read_bytes(), original)
        self.assertFalse((self.root / ".zellno-trader-account-tool").exists())

    def test_correct_confirmation_applies_locally(self) -> None:
        steamid = "76561198000000001"
        self.write_account(steamid, steamid)
        with mock.patch("builtins.input", return_value=steamid):
            code, stdout, stderr = self.run_cli(
                "account",
                "plan",
                "balance",
                "set",
                "--file",
                f"Account_{steamid}.json",
                "--amount",
                "500000",
                "--apply",
            )
        self.assertEqual(code, 0, stderr)
        self.assertIn("APLICAÇÃO LOCAL CONCLUÍDA", stdout)
        current = json.loads((self.root / f"Account_{steamid}.json").read_text(encoding="utf-8"))
        self.assertEqual(current["MoneyAmount"], 500000)
        self.assertTrue((self.root / ".zellno-trader-account-tool" / "audit.jsonl").is_file())

    def fake_package(self):
        steamid = "76561198000000001"
        return SimpleNamespace(
            path=self.root / "package",
            account_name=f"Account_{steamid}.json",
            steamid=steamid,
            original_sha256="a" * 64,
            proposed_sha256="b" * 64,
            manifest={"name": "Zellno", "operation": "Perfil: Jogador normal"},
        )

    def test_remote_deploy_dry_run_opens_no_connection(self) -> None:
        with (
            mock.patch("zellno_trader.cli.validate_deployment_package", return_value=self.fake_package()),
            mock.patch("zellno_trader.cli.deploy_package_ftp") as deploy,
            mock.patch("zellno_trader.cli.getpass.getpass") as password_prompt,
        ):
            code, stdout, stderr = self.run_cli(
                "remote",
                "deploy",
                "--package",
                str(self.root / "package"),
                "--host",
                "ftp.example.test",
                "--user",
                "admin",
                "--state-dir",
                str(self.root / "state"),
            )
        self.assertEqual(code, 0, stderr)
        self.assertIn("DRY-RUN REMOTO", stdout)
        deploy.assert_not_called()
        password_prompt.assert_not_called()

    def test_remote_deploy_wrong_second_confirmation_opens_no_connection(self) -> None:
        with (
            mock.patch("zellno_trader.cli.validate_deployment_package", return_value=self.fake_package()),
            mock.patch("zellno_trader.cli.deploy_package_ftp") as deploy,
            mock.patch("zellno_trader.cli.getpass.getpass") as password_prompt,
            mock.patch("builtins.input", side_effect=["76561198000000001", "NAO"]),
        ):
            code, stdout, _ = self.run_cli(
                "remote",
                "deploy",
                "--package",
                str(self.root / "package"),
                "--host",
                "ftp.example.test",
                "--user",
                "admin",
                "--state-dir",
                str(self.root / "state"),
                "--server-stopped",
                "--allow-plain-ftp",
                "--apply",
            )
        self.assertEqual(code, 1)
        self.assertIn("Confirmação incorreta", stdout)
        deploy.assert_not_called()
        password_prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
