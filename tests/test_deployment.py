from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zellno_trader.deployment import (
    DeploymentError,
    prepare_deployment,
    validate_snapshot_target,
)
from zellno_trader.planning import profile_normal, profile_test


VALID_ID = "76561198000000001"
OTHER_ID = "76561198000000002"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "snapshot-test"
        self.bank = self.snapshot / "TraderPlusBankDatabase"
        self.config_dir = self.snapshot / "TraderPlusConfig"
        self.bank.mkdir(parents=True)
        self.config_dir.mkdir()
        self.filename = f"Account_{VALID_ID}.json"
        self.account_path = self.bank / self.filename
        self.account_document = {
            "Version": "2.5",
            "SteamID64": VALID_ID,
            "Name": "Zellno",
            "MoneyAmount": 4013,
            "MaxAmount": 99000000,
            "Licences": ["Bob Licence", "Military Licence"],
            "Insurances": {"untouched": [1, 2, 3]},
            "FutureTraderPlusField": {"preserve": True},
        }
        self.account_path.write_text(
            json.dumps(self.account_document, ensure_ascii=False),
            encoding="utf-8",
        )
        self.config_path = self.config_dir / "TraderPlusGeneralConfig.json"
        self.config_path.write_text(
            json.dumps({"Version": "2.5", "Licences": ["Bob Licence", "Mason Licence"]}),
            encoding="utf-8",
        )
        self.invalid_name = f"Account_{OTHER_ID}.json"
        self.invalid_path = self.bank / self.invalid_name
        self.invalid_path.write_text(
            json.dumps({**self.account_document, "MoneyAmount": 1000000}),
            encoding="utf-8",
        )
        self.write_manifest(server_stopped=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, *, server_stopped: bool) -> None:
        manifest = {
            "version": 1,
            "transport": "plain_ftp_read_only",
            "server_stopped_attested": server_stopped,
            "trusted_for_editing": False,
            "accounts": [
                {
                    "file": self.filename,
                    "name": "Zellno",
                    "steamid64": VALID_ID,
                    "status": "VÁLIDA COM AVISOS",
                    "issues": [
                        {
                            "severity": "warning",
                            "code": "unknown_licence",
                            "message": "Licença antiga",
                        }
                    ],
                },
                {
                    "file": self.invalid_name,
                    "name": "Zellno",
                    "steamid64": VALID_ID,
                    "status": "INCONSISTENTE",
                    "issues": [
                        {
                            "severity": "error",
                            "code": "identity_mismatch",
                            "message": "Identidade divergente",
                        }
                    ],
                },
            ],
            "files": [
                {
                    "path": f"TraderPlusBankDatabase/{self.filename}",
                    "sha256": sha256(self.account_path),
                },
                {
                    "path": f"TraderPlusBankDatabase/{self.invalid_name}",
                    "sha256": sha256(self.invalid_path),
                },
                {
                    "path": "TraderPlusConfig/TraderPlusGeneralConfig.json",
                    "sha256": sha256(self.config_path),
                },
            ],
        }
        (self.snapshot / "snapshot-manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )

    def test_valid_account_is_trusted_despite_invalid_sibling(self) -> None:
        target = validate_snapshot_target(self.snapshot, self.filename)
        self.assertTrue(target.account.valid)
        self.assertEqual(target.account.steamid, VALID_ID)

    def test_invalid_selected_account_is_blocked(self) -> None:
        with self.assertRaisesRegex(DeploymentError, "inválida ou inconsistente"):
            validate_snapshot_target(self.snapshot, self.invalid_name)

    def test_online_snapshot_is_blocked(self) -> None:
        self.write_manifest(server_stopped=False)
        with self.assertRaisesRegex(DeploymentError, "--server-stopped"):
            validate_snapshot_target(self.snapshot, self.filename)

    def test_tampered_account_is_blocked(self) -> None:
        self.account_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(DeploymentError, "hash"):
            validate_snapshot_target(self.snapshot, self.filename)

    def test_destination_inside_snapshot_is_blocked(self) -> None:
        target = validate_snapshot_target(self.snapshot, self.filename)
        plan = profile_normal(target.account)
        with self.assertRaisesRegex(DeploymentError, "dentro do snapshot"):
            prepare_deployment(target, plan, self.snapshot / "deployments")
        self.assertFalse((self.snapshot / "deployments").exists())

    def test_package_preserves_out_of_scope_and_snapshot(self) -> None:
        target = validate_snapshot_target(self.snapshot, self.filename)
        original_snapshot = self.account_path.read_bytes()
        plan = profile_normal(target.account)
        result = prepare_deployment(target, plan, self.root / "deployments")

        self.assertEqual(self.account_path.read_bytes(), original_snapshot)
        self.assertEqual(result.original_path.read_bytes(), original_snapshot)
        proposed = json.loads(result.proposed_path.read_text(encoding="utf-8"))
        self.assertEqual(proposed["MoneyAmount"], 0)
        self.assertEqual(proposed["Licences"], [])
        self.assertEqual(proposed["Insurances"], self.account_document["Insurances"])
        self.assertEqual(
            proposed["FutureTraderPlusField"],
            self.account_document["FutureTraderPlusField"],
        )
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "prepared_not_deployed")
        self.assertFalse(manifest["ftp_upload_performed"])
        self.assertIn("MoneyAmount", result.diff_path.read_text(encoding="utf-8"))

    def test_test_profile_package_is_exact(self) -> None:
        target = validate_snapshot_target(self.snapshot, self.filename)
        plan = profile_test(
            target.account,
            target.config,
            balance=1000000,
            licences=["Bob Licence", "Mason Licence"],
        )
        result = prepare_deployment(target, plan, self.root / "deployments")
        proposed = json.loads(result.proposed_path.read_text(encoding="utf-8"))
        self.assertEqual(proposed["MoneyAmount"], 1000000)
        self.assertEqual(proposed["Licences"], ["Bob Licence", "Mason Licence"])

    def test_repeated_preparation_has_identical_proposed_content(self) -> None:
        target = validate_snapshot_target(self.snapshot, self.filename)
        plan = profile_normal(target.account)
        first = prepare_deployment(target, plan, self.root / "deployments")
        second = prepare_deployment(target, plan, self.root / "deployments")
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.proposed_path.read_bytes(), second.proposed_path.read_bytes())
        self.assertEqual(first.proposed_sha256, second.proposed_sha256)


if __name__ == "__main__":
    unittest.main()
