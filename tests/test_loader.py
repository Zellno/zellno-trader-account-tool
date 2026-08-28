from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zellno_trader.loader import load_account, load_general_config


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "TraderPlusGeneralConfig.json"
        self.config_path.write_text(
            json.dumps({"Version": "2.5", "Licences": ["Bob Licence", "Mason Licence"]}),
            encoding="utf-8",
        )
        self.config = load_general_config(self.config_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_account(self, filename: str, **overrides: object) -> Path:
        data: dict[str, object] = {
            "Version": "2.5",
            "SteamID64": "76561198000000001",
            "Name": "Jogador Teste",
            "MoneyAmount": 4000,
            "MaxAmount": 1000000,
            "Licences": ["Bob Licence"],
            "Insurances": {},
        }
        data.update(overrides)
        path = self.root / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_valid_account(self) -> None:
        path = self.write_account("Account_76561198000000001.json")
        account = load_account(path, self.config)
        self.assertTrue(account.valid)
        self.assertEqual(account.configured_licences, ("Bob Licence",))
        self.assertEqual(account.missing_licences, ("Mason Licence",))

    def test_identity_mismatch_is_blocking(self) -> None:
        path = self.write_account("Account_76561198000000002.json")
        account = load_account(path, self.config)
        self.assertFalse(account.valid)
        self.assertEqual(account.status, "INCONSISTENTE")
        self.assertIn("identity_mismatch", {issue.code for issue in account.issues})

    def test_old_licence_is_warning(self) -> None:
        path = self.write_account(
            "Account_76561198000000001.json",
            Licences=["Bob Licence", "Military Licence"],
        )
        account = load_account(path, self.config)
        self.assertTrue(account.valid)
        self.assertEqual(account.unknown_licences, ("Military Licence",))
        self.assertEqual(account.status, "VÁLIDA COM AVISOS")

    def test_balance_above_limit_is_blocking(self) -> None:
        path = self.write_account(
            "Account_76561198000000001.json",
            MoneyAmount=1000001,
        )
        account = load_account(path, self.config)
        self.assertFalse(account.valid)
        self.assertIn("balance_above_limit", {issue.code for issue in account.issues})

    def test_invalid_json_is_blocking(self) -> None:
        path = self.root / "Account_76561198000000001.json"
        path.write_text("{invalid", encoding="utf-8")
        account = load_account(path, self.config)
        self.assertFalse(account.valid)
        self.assertIn("invalid_json", {issue.code for issue in account.issues})


if __name__ == "__main__":
    unittest.main()
