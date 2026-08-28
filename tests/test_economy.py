from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zellno_trader.cli import main
from zellno_trader.economy import EconomyAuditError, audit_economy, write_reports


VALID_ID = "76561198000000001"
OTHER_ID = "76561198000000002"


class EconomyAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "snapshots"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def snapshot(
        self,
        stamp: str,
        balance: int,
        *,
        licences: list[str] | None = None,
        invalid_duplicate: bool = False,
        stopped: bool = True,
    ) -> Path:
        root = self.root / f"snapshot-{stamp}"
        bank = root / "TraderPlusBankDatabase"
        config_dir = root / "TraderPlusConfig"
        bank.mkdir(parents=True)
        config_dir.mkdir()
        config = {"Version": "2.5", "Licences": ["Bob Licence", "Mason Licence"]}
        (config_dir / "TraderPlusGeneralConfig.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

        def write_account(filename_id: str, internal_id: str, amount: int) -> None:
            raw = {
                "Version": "2.5",
                "SteamID64": internal_id,
                "Name": "Zellno",
                "MoneyAmount": amount,
                "MaxAmount": 1000000,
                "Licences": licences or [],
                "Insurances": {},
            }
            (bank / f"Account_{filename_id}.json").write_text(
                json.dumps(raw), encoding="utf-8"
            )

        write_account(VALID_ID, VALID_ID, balance)
        if invalid_duplicate:
            write_account(OTHER_ID, VALID_ID, 999999)
        files = []
        for path in sorted(root.rglob("*.json")):
            if path.name == "snapshot-manifest.json":
                continue
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        manifest = {
            "version": 1,
            "created_at_utc": f"2026-08-28T{stamp[:2]}:00:00+00:00",
            "server_stopped_attested": stopped,
            "files": files,
        }
        (root / "snapshot-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return root

    def test_metrics_changes_signals_and_invalid_exclusion(self) -> None:
        self.snapshot("100000", 100, licences=[])
        self.snapshot(
            "110000",
            5100,
            licences=["Bob Licence"],
            invalid_duplicate=True,
            stopped=False,
        )
        report = audit_economy(self.root)
        self.assertEqual(report.snapshot_count, 2)
        self.assertEqual(report.valid_observation_count, 2)
        self.assertEqual(report.latest_account_count, 1)
        self.assertEqual(report.latest_total_balance, 5100)
        self.assertEqual(len(report.rejected_accounts), 1)
        self.assertEqual(report.changes[0].balance_delta, 5000)
        self.assertEqual(report.changes[0].matched_signals, (5000,))
        self.assertEqual(report.changes[0].added_licences, ("Bob Licence",))

    def test_negative_admin_change_is_not_flagged(self) -> None:
        self.snapshot("100000", 5675)
        self.snapshot("110000", 0)
        report = audit_economy(self.root)
        self.assertEqual(report.changes[0].balance_delta, -5675)
        self.assertEqual(report.changes[0].matched_signals, ())

    def test_verified_remote_deployment_classifies_administrative_change(self) -> None:
        before = self.snapshot("100000", 5675)
        after = self.snapshot("110000", 0)
        before_account = before / "TraderPlusBankDatabase" / f"Account_{VALID_ID}.json"
        after_account = after / "TraderPlusBankDatabase" / f"Account_{VALID_ID}.json"
        audit = Path(self.temp.name) / "remote-audit.jsonl"
        audit.write_text(
            json.dumps(
                {
                    "timestamp_utc": "2026-08-28T10:30:00+00:00",
                    "result": "deployed",
                    "scope": "remote_ftp",
                    "account_file": f"Account_{VALID_ID}.json",
                    "steamid64": VALID_ID,
                    "before_sha256": hashlib.sha256(before_account.read_bytes()).hexdigest(),
                    "after_sha256": hashlib.sha256(after_account.read_bytes()).hexdigest(),
                }
            ) + "\n",
            encoding="utf-8",
        )
        report = audit_economy(self.root, remote_audit_path=audit)
        self.assertTrue(report.changes[0].administrative)
        self.assertEqual(report.administrative_change_count, 1)
        self.assertEqual(report.ignored_audit_record_count, 0)

    def test_invalid_or_hash_mismatched_audit_does_not_explain_change(self) -> None:
        self.snapshot("100000", 0)
        self.snapshot("110000", 150000)
        audit = Path(self.temp.name) / "remote-audit.jsonl"
        audit.write_text(
            "not-json\n" + json.dumps(
                {
                    "timestamp_utc": "2026-08-28T10:30:00+00:00",
                    "result": "deployed",
                    "scope": "remote_ftp",
                    "account_file": f"Account_{VALID_ID}.json",
                    "steamid64": VALID_ID,
                    "before_sha256": "0" * 64,
                    "after_sha256": "1" * 64,
                }
            ) + "\n",
            encoding="utf-8",
        )
        report = audit_economy(self.root, remote_audit_path=audit)
        self.assertFalse(report.changes[0].administrative)
        self.assertEqual(report.changes[0].matched_signals, (5000, 150000))
        self.assertEqual(report.ignored_audit_record_count, 1)

    def test_duplicate_matching_deployments_are_ambiguous(self) -> None:
        before = self.snapshot("100000", 100)
        after = self.snapshot("110000", 5100)
        before_account = before / "TraderPlusBankDatabase" / f"Account_{VALID_ID}.json"
        after_account = after / "TraderPlusBankDatabase" / f"Account_{VALID_ID}.json"
        record = {
            "timestamp_utc": "2026-08-28T10:30:00+00:00",
            "result": "deployed",
            "scope": "remote_ftp",
            "account_file": f"Account_{VALID_ID}.json",
            "steamid64": VALID_ID,
            "before_sha256": hashlib.sha256(before_account.read_bytes()).hexdigest(),
            "after_sha256": hashlib.sha256(after_account.read_bytes()).hexdigest(),
        }
        audit = Path(self.temp.name) / "remote-audit.jsonl"
        audit.write_text(
            json.dumps(record) + "\n" + json.dumps(record) + "\n",
            encoding="utf-8",
        )
        report = audit_economy(self.root, remote_audit_path=audit)
        self.assertFalse(report.changes[0].administrative)
        self.assertEqual(report.changes[0].matched_signals, (5000,))

    def test_tampered_snapshot_is_rejected(self) -> None:
        snapshot = self.snapshot("100000", 100)
        account = snapshot / "TraderPlusBankDatabase" / f"Account_{VALID_ID}.json"
        account.write_text("{}", encoding="utf-8")
        with self.assertRaises(EconomyAuditError):
            audit_economy(self.root)

    def test_unmanifested_account_is_rejected(self) -> None:
        snapshot = self.snapshot("100000", 100)
        injected = snapshot / "TraderPlusBankDatabase" / f"Account_{OTHER_ID}.json"
        injected.write_text(
            json.dumps(
                {
                    "Version": "2.5",
                    "SteamID64": OTHER_ID,
                    "Name": "Injetado",
                    "MoneyAmount": 150000,
                    "MaxAmount": 1000000,
                    "Licences": [],
                    "Insurances": {},
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(EconomyAuditError):
            audit_economy(self.root)

    def test_reports_have_manifest_and_expected_formats(self) -> None:
        self.snapshot("100000", 0)
        self.snapshot("110000", 150000)
        report = audit_economy(self.root)
        files = write_reports(report, Path(self.temp.name) / "reports")
        self.assertTrue(files.text_path.is_file())
        self.assertTrue(files.json_path.is_file())
        self.assertTrue(files.csv_path.is_file())
        manifest = json.loads(files.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["mode"], "local_read_only")
        self.assertEqual(len(manifest["files"]), 3)
        self.assertIn("150000", files.csv_path.read_text(encoding="utf-8"))

    def test_cli_audit_opens_no_ftp_connection(self) -> None:
        self.snapshot("100000", 0)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch("ftplib.FTP.connect") as connect,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(["economy", "audit", "--snapshots-dir", str(self.root)])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Modo somente leitura", stdout.getvalue())
        connect.assert_not_called()

    def test_cli_reconciled_audit_opens_no_ftp_connection(self) -> None:
        self.snapshot("100000", 0)
        audit = Path(self.temp.name) / "remote-audit.jsonl"
        audit.write_text("", encoding="utf-8")
        with mock.patch("ftplib.FTP.connect") as connect:
            code = main(
                [
                    "economy", "audit", "--snapshots-dir", str(self.root),
                    "--remote-audit", str(audit),
                ]
            )
        self.assertEqual(code, 0)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
