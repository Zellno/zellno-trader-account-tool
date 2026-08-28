from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from zellno_trader.loader import load_account, load_general_config
from zellno_trader.planning import (
    PlanError,
    balance_set,
    licence_add,
    licence_remove,
    profile_normal,
    profile_test,
)


class PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "TraderPlusGeneralConfig.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "Version": "2.5",
                    "Licences": ["Bob Licence", "Mason Licence", "Vera Licence"],
                }
            ),
            encoding="utf-8",
        )
        self.account_path = self.root / "Account_76561198000000001.json"
        self.account_path.write_text(
            json.dumps(
                {
                    "Version": "2.5",
                    "SteamID64": "76561198000000001",
                    "Name": "Jogador Teste",
                    "MoneyAmount": 4013,
                    "MaxAmount": 99000000,
                    "Licences": ["Bob Licence", "Military Licence", "Mason Licence"],
                    "Insurances": {"opaque": {"must": "survive"}},
                    "UnknownFutureField": [1, 2, 3],
                }
            ),
            encoding="utf-8",
        )
        self.config = load_general_config(self.config_path)
        self.account = load_account(self.account_path, self.config)
        self.original_bytes = self.account_path.read_bytes()
        self.original_raw = copy.deepcopy(self.account.raw)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def assert_immutable(self) -> None:
        self.assertEqual(self.account_path.read_bytes(), self.original_bytes)
        self.assertEqual(self.account.raw, self.original_raw)

    def test_normal_profile_is_exact_and_immutable(self) -> None:
        plan = profile_normal(self.account)
        self.assertEqual(plan.after_balance, 0)
        self.assertEqual(plan.after_licences, ())
        self.assert_immutable()

    def test_test_profile_is_exact_and_immutable(self) -> None:
        plan = profile_test(
            self.account,
            self.config,
            balance=1000000,
            licences=["Bob Licence", "Vera Licence"],
        )
        self.assertEqual(plan.after_balance, 1000000)
        self.assertEqual(plan.after_licences, ("Bob Licence", "Vera Licence"))
        self.assert_immutable()

    def test_balance_preserves_licences(self) -> None:
        plan = balance_set(self.account, 500000)
        self.assertEqual(plan.after_licences, self.account.licences)
        self.assert_immutable()

    def test_add_is_idempotent(self) -> None:
        plan = licence_add(self.account, self.config, "Bob Licence")
        self.assertFalse(plan.has_changes)
        self.assert_immutable()

    def test_remove_unknown_old_licence(self) -> None:
        plan = licence_remove(self.account, "Military Licence")
        self.assertNotIn("Military Licence", plan.after_licences)
        self.assert_immutable()

    def test_unknown_add_is_blocked(self) -> None:
        with self.assertRaises(PlanError):
            licence_add(self.account, self.config, "Admin Licence")
        self.assert_immutable()

    def test_balance_above_limit_is_blocked(self) -> None:
        with self.assertRaises(PlanError):
            balance_set(self.account, 99000001)
        self.assert_immutable()

    def test_inconsistent_account_is_blocked(self) -> None:
        bad_path = self.root / "Account_76561198000000002.json"
        bad_path.write_bytes(self.original_bytes)
        bad = load_account(bad_path, self.config)
        with self.assertRaises(PlanError):
            profile_normal(bad)


if __name__ == "__main__":
    unittest.main()
