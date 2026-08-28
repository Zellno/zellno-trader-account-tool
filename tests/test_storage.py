from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zellno_trader.loader import load_account, load_general_config
from zellno_trader.planning import balance_set, profile_normal
from zellno_trader.storage import (
    StorageError,
    apply_plan_local,
    list_backups,
    resolve_backup,
    restore_plan,
)


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.config_path = self.root / "TraderPlusGeneralConfig.json"
        self.config_path.write_text(
            json.dumps({"Version": "2.5", "Licences": ["Bob Licence", "Mason Licence"]}),
            encoding="utf-8",
        )
        self.account_path = self.root / "Account_76561198000000001.json"
        self.original = {
            "Version": "2.5",
            "SteamID64": "76561198000000001",
            "Name": "Jogador Teste",
            "MoneyAmount": 4013,
            "MaxAmount": 99000000,
            "Licences": ["Bob Licence", "Military Licence", "Mason Licence"],
            "Insurances": {"opaque": {"must": "survive"}},
            "UnknownFutureField": {"preserve": [1, 2, 3]},
        }
        self.account_path.write_text(json.dumps(self.original, indent=2), encoding="utf-8")
        self.original_bytes = self.account_path.read_bytes()
        self.config = load_general_config(self.config_path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def load(self):
        return load_account(self.account_path, self.config)

    def test_apply_creates_backup_audit_and_preserves_out_of_scope(self) -> None:
        account = self.load()
        result = apply_plan_local(account, profile_normal(account), self.config, self.state)

        current = json.loads(self.account_path.read_text(encoding="utf-8"))
        self.assertEqual(current["MoneyAmount"], 0)
        self.assertEqual(current["Licences"], [])
        self.assertEqual(current["Insurances"], self.original["Insurances"])
        self.assertEqual(current["UnknownFutureField"], self.original["UnknownFutureField"])
        self.assertEqual(result.backup_path.read_bytes(), self.original_bytes)

        records = [json.loads(line) for line in result.audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["steamid64"], "76561198000000001")
        self.assertEqual(records[0]["before"]["money_amount"], 4013)
        self.assertEqual(records[0]["after"]["money_amount"], 0)
        audit_text = result.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("Insurances", audit_text)
        self.assertNotIn("UnknownFutureField", audit_text)

    def test_audit_failure_rolls_back_exact_original(self) -> None:
        account = self.load()
        with mock.patch("zellno_trader.storage._append_audit", side_effect=OSError("sem espaço")):
            with self.assertRaises(StorageError):
                apply_plan_local(account, profile_normal(account), self.config, self.state)
        self.assertEqual(self.account_path.read_bytes(), self.original_bytes)

    def test_restore_uses_only_balance_and_licences(self) -> None:
        original_account = self.load()
        first = apply_plan_local(
            original_account,
            profile_normal(original_account),
            self.config,
            self.state,
        )

        changed = json.loads(self.account_path.read_text(encoding="utf-8"))
        changed["Insurances"] = {"new": "current insurance must remain"}
        changed["UnknownFutureField"] = {"new": True}
        self.account_path.write_text(json.dumps(changed), encoding="utf-8")

        current = self.load()
        plan = restore_plan(current, first.backup_path, self.config)
        apply_plan_local(current, plan, self.config, self.state)

        restored = json.loads(self.account_path.read_text(encoding="utf-8"))
        self.assertEqual(restored["MoneyAmount"], 4013)
        self.assertEqual(restored["Licences"], self.original["Licences"])
        self.assertEqual(restored["Insurances"], {"new": "current insurance must remain"})
        self.assertEqual(restored["UnknownFutureField"], {"new": True})

    def test_backup_resolution_rejects_paths(self) -> None:
        with self.assertRaises(StorageError):
            resolve_backup(self.state, "../outside.json")

    def test_list_backups_returns_created_backup(self) -> None:
        account = self.load()
        result = apply_plan_local(account, balance_set(account, 500000), self.config, self.state)
        self.assertEqual(list_backups(self.state), [result.backup_path])

    def test_inconsistent_account_never_creates_state(self) -> None:
        inconsistent_path = self.root / "Account_76561198000000002.json"
        inconsistent_path.write_bytes(self.original_bytes)
        inconsistent = load_account(inconsistent_path, self.config)
        with self.assertRaises(Exception):
            profile_normal(inconsistent)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
