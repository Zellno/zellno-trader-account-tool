from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from zellno_trader.deployment import prepare_deployment, validate_snapshot_target
from zellno_trader.planning import profile_normal
from zellno_trader.remote_deploy import (
    RemoteDeployError,
    deploy_package_ftp,
    validate_deployment_package,
)


STEAMID = "76561198000000001"
ACCOUNT_NAME = f"Account_{STEAMID}.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FakeDeployFTP:
    def __init__(
        self,
        original: bytes,
        *,
        fail_activation: bool = False,
        corrupt_temp: bool = False,
        corrupt_backup: bool = False,
        corrupt_final: bool = False,
        fail_backup_read_once: bool = False,
    ) -> None:
        self.files = {ACCOUNT_NAME: original}
        self.calls: list[tuple] = []
        self.fail_activation = fail_activation
        self.corrupt_temp = corrupt_temp
        self.corrupt_backup = corrupt_backup
        self.corrupt_final = corrupt_final
        self.fail_backup_read_once = fail_backup_read_once
        self.activated = False

    def connect(self, *, host: str, port: int, timeout: int) -> None:
        self.calls.append(("connect", host, port, timeout))

    def login(self, *, user: str, passwd: str) -> None:
        self.calls.append(("login", user, "<hidden>"))

    def set_pasv(self, value: bool) -> None:
        self.calls.append(("set_pasv", value))

    def cwd(self, path: str) -> None:
        self.calls.append(("cwd", path))

    def nlst(self) -> list[str]:
        self.calls.append(("nlst",))
        return list(self.files)

    def retrbinary(self, command: str, callback) -> None:
        name = command.removeprefix("RETR ")
        self.calls.append(("retrbinary", name))
        if self.fail_backup_read_once and ".zellno-backup-" in name:
            self.fail_backup_read_once = False
            raise OSError("connection dropped after backup rename")
        data = self.files[name]
        if self.corrupt_temp and name.startswith(".zellno-upload-"):
            data += b"corrupt"
        if self.corrupt_backup and ".zellno-backup-" in name:
            data += b"corrupt"
        if self.corrupt_final and name == ACCOUNT_NAME and self.activated:
            data += b"corrupt"
        callback(data)

    def storbinary(self, command: str, source) -> None:
        name = command.removeprefix("STOR ")
        self.calls.append(("storbinary", name))
        self.files[name] = source.read()

    def rename(self, source: str, destination: str) -> None:
        self.calls.append(("rename", source, destination))
        if self.fail_activation and source.startswith(".zellno-upload-") and destination == ACCOUNT_NAME:
            self.fail_activation = False
            raise OSError("activation failure")
        self.files[destination] = self.files.pop(source)
        if source.startswith(".zellno-upload-") and destination == ACCOUNT_NAME:
            self.activated = True
        if destination == ACCOUNT_NAME and ".zellno-backup-" in source:
            self.activated = False

    def delete(self, name: str) -> None:
        self.calls.append(("delete", name))
        del self.files[name]

    def quit(self) -> None:
        self.calls.append(("quit",))

    def close(self) -> None:
        self.calls.append(("close",))


class RemoteDeployTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "snapshot"
        bank = self.snapshot / "TraderPlusBankDatabase"
        config_dir = self.snapshot / "TraderPlusConfig"
        bank.mkdir(parents=True)
        config_dir.mkdir()
        self.account_path = bank / ACCOUNT_NAME
        self.account_path.write_text(
            json.dumps(
                {
                    "Version": "2.5",
                    "SteamID64": STEAMID,
                    "Name": "Zellno",
                    "MoneyAmount": 4013,
                    "MaxAmount": 99000000,
                    "Licences": ["Bob Licence", "Military Licence"],
                    "Insurances": {"preserve": True},
                    "Unknown": [1, 2],
                }
            ),
            encoding="utf-8",
        )
        config_path = config_dir / "TraderPlusGeneralConfig.json"
        config_path.write_text(
            json.dumps({"Version": "2.5", "Licences": ["Bob Licence"]}),
            encoding="utf-8",
        )
        manifest = {
            "version": 1,
            "transport": "plain_ftp_read_only",
            "server_stopped_attested": True,
            "accounts": [
                {
                    "file": ACCOUNT_NAME,
                    "name": "Zellno",
                    "steamid64": STEAMID,
                    "status": "VÁLIDA COM AVISOS",
                    "issues": [{"severity": "warning", "code": "unknown_licence"}],
                }
            ],
            "files": [
                {
                    "path": f"TraderPlusBankDatabase/{ACCOUNT_NAME}",
                    "sha256": digest(self.account_path),
                },
                {
                    "path": "TraderPlusConfig/TraderPlusGeneralConfig.json",
                    "sha256": digest(config_path),
                },
            ],
        }
        (self.snapshot / "snapshot-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        target = validate_snapshot_target(self.snapshot, ACCOUNT_NAME)
        plan = profile_normal(target.account)
        prepared = prepare_deployment(target, plan, self.root / "packages")
        self.package = validate_deployment_package(prepared.path)
        self.original = self.account_path.read_bytes()
        self.proposed = self.package.proposed_path.read_bytes()
        self.state = self.root / "state"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def deploy(self, fake: FakeDeployFTP, **overrides):
        arguments = {
            "host": "ftp.example.test",
            "port": 21,
            "user": "admin",
            "password": "secret-not-stored",
            "state_dir": self.state,
            "server_stopped_attested": True,
            "ftp_factory": lambda: fake,
        }
        arguments.update(overrides)
        return deploy_package_ftp(self.package, **arguments)

    def test_success_keeps_remote_and_local_backup(self) -> None:
        fake = FakeDeployFTP(self.original)
        result = self.deploy(fake)
        self.assertEqual(fake.files[ACCOUNT_NAME], self.proposed)
        self.assertEqual(fake.files[result.remote_backup_name], self.original)
        self.assertEqual(result.local_backup_path.read_bytes(), self.original)
        audit = [
            json.loads(line)
            for line in result.audit_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([item["result"] for item in audit], ["authorized_pending", "deployed"])
        self.assertNotIn("secret-not-stored", result.audit_path.read_text(encoding="utf-8"))

    def test_changed_remote_is_blocked_before_upload(self) -> None:
        fake = FakeDeployFTP(self.original + b"changed")
        with self.assertRaisesRegex(RemoteDeployError, "mudou desde o snapshot"):
            self.deploy(fake)
        operations = {call[0] for call in fake.calls}
        self.assertNotIn("storbinary", operations)
        self.assertNotIn("rename", operations)
        self.assertEqual(fake.files[ACCOUNT_NAME], self.original + b"changed")

    def test_server_stopped_attestation_is_required(self) -> None:
        fake = FakeDeployFTP(self.original)
        with self.assertRaisesRegex(RemoteDeployError, "--server-stopped"):
            self.deploy(fake, server_stopped_attested=False)
        self.assertEqual(fake.calls, [])

    def test_corrupt_temporary_upload_is_removed_without_touching_original(self) -> None:
        fake = FakeDeployFTP(self.original, corrupt_temp=True)
        with self.assertRaisesRegex(RemoteDeployError, "upload temporário"):
            self.deploy(fake)
        self.assertEqual(fake.files, {ACCOUNT_NAME: self.original})
        audit = self.state / "remote-audit.jsonl"
        self.assertIn('"result":"failed_rolled_back"', audit.read_text(encoding="utf-8"))

    def test_activation_failure_restores_original(self) -> None:
        fake = FakeDeployFTP(self.original, fail_activation=True)
        with self.assertRaisesRegex(RemoteDeployError, "original remoto foi restaurado"):
            self.deploy(fake)
        self.assertEqual(fake.files, {ACCOUNT_NAME: self.original})
        audit = self.state / "remote-audit.jsonl"
        self.assertIn('"result":"failed_rolled_back"', audit.read_text(encoding="utf-8"))

    def test_remote_backup_is_verified_before_activation(self) -> None:
        fake = FakeDeployFTP(self.original, corrupt_backup=True)
        with self.assertRaisesRegex(RemoteDeployError, "backup remoto divergiu"):
            self.deploy(fake)
        self.assertEqual(fake.files, {ACCOUNT_NAME: self.original})

    def test_connection_failure_after_backup_rename_restores_original(self) -> None:
        fake = FakeDeployFTP(self.original, fail_backup_read_once=True)
        with self.assertRaises(RemoteDeployError):
            self.deploy(fake)
        self.assertEqual(fake.files, {ACCOUNT_NAME: self.original})

    def test_final_verification_failure_rolls_back(self) -> None:
        fake = FakeDeployFTP(self.original, corrupt_final=True)
        with self.assertRaisesRegex(RemoteDeployError, "verificação final falhou"):
            self.deploy(fake)
        self.assertEqual(fake.files[ACCOUNT_NAME], self.original)
        self.assertTrue(any(".zellno-failed-" in name for name in fake.files))

    def test_tampered_proposal_is_rejected_locally(self) -> None:
        self.package.proposed_path.write_bytes(self.proposed + b"tampered")
        with self.assertRaisesRegex(RemoteDeployError, "hash da proposta"):
            validate_deployment_package(self.package.path)


if __name__ == "__main__":
    unittest.main()
