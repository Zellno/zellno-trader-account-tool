from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zellno_trader.remote import RemoteError, create_snapshot


VALID_ID = "76561198000000001"
OTHER_ID = "76561198000000002"


def account_bytes(filename_id: str, internal_id: str) -> bytes:
    return json.dumps(
        {
            "Version": "2.5",
            "SteamID64": internal_id,
            "Name": "Jogador Teste",
            "MoneyAmount": 0,
            "MaxAmount": 1000000,
            "Licences": [],
            "Insurances": {},
        }
    ).encode()


GENERAL = json.dumps({"Version": "2.5", "Licences": ["Bob Licence"]}).encode()


class FakeFTP:
    def __init__(self, *, change_listing: bool = False) -> None:
        self.cwd_path = "/"
        self.change_listing = change_listing
        self.nlst_calls = 0
        self.calls: list[tuple] = []
        self.files = {
            "/profile/TraderPlus/TraderPlusBankDatabase": {
                f"Account_{VALID_ID}.json": account_bytes(VALID_ID, VALID_ID),
            },
            "/profile/TraderPlus/TraderPlusConfig": {
                "TraderPlusGeneralConfig.json": GENERAL,
            },
        }

    def connect(self, *, host: str, port: int, timeout: int) -> None:
        self.calls.append(("connect", host, port, timeout))

    def login(self, *, user: str, passwd: str) -> None:
        self.calls.append(("login", user, "<hidden>"))

    def set_pasv(self, value: bool) -> None:
        self.calls.append(("set_pasv", value))

    def cwd(self, path: str) -> None:
        self.calls.append(("cwd", path))
        self.cwd_path = path

    def nlst(self) -> list[str]:
        self.calls.append(("nlst", self.cwd_path))
        self.nlst_calls += 1
        names = list(self.files[self.cwd_path])
        if self.change_listing and self.nlst_calls == 2:
            names.append(f"Account_{OTHER_ID}.json")
        return names

    def retrbinary(self, command: str, callback) -> None:
        self.calls.append(("retrbinary", self.cwd_path, command))
        name = command.removeprefix("RETR ")
        callback(self.files[self.cwd_path][name])

    def quit(self) -> None:
        self.calls.append(("quit",))

    def close(self) -> None:
        self.calls.append(("close",))


class RemoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.destination = Path(self.temp.name) / "snapshots"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_stopped_server_snapshot_is_trusted_when_all_accounts_valid(self) -> None:
        fake = FakeFTP()
        result = create_snapshot(
            host="ftp.example.test",
            port=21,
            user="admin",
            password="secret-not-to-store",
            destination=self.destination,
            server_stopped_attested=True,
            ftp_factory=lambda: fake,
        )
        self.assertEqual(result.account_count, 1)
        self.assertEqual(result.invalid_account_count, 0)
        self.assertTrue(result.trusted_for_editing)
        manifest_text = result.manifest_path.read_text(encoding="utf-8")
        self.assertNotIn("secret-not-to-store", manifest_text)
        self.assertNotIn("admin", manifest_text)
        operations = {call[0] for call in fake.calls}
        self.assertLessEqual(
            operations,
            {"connect", "login", "set_pasv", "cwd", "nlst", "retrbinary", "quit", "close"},
        )

    def test_online_snapshot_is_informational(self) -> None:
        result = create_snapshot(
            host="ftp.example.test",
            port=21,
            user="admin",
            password="secret",
            destination=self.destination,
            server_stopped_attested=False,
            ftp_factory=FakeFTP,
        )
        self.assertFalse(result.trusted_for_editing)

    def test_inconsistent_account_is_reported(self) -> None:
        fake = FakeFTP()
        bank = fake.files["/profile/TraderPlus/TraderPlusBankDatabase"]
        bank[f"Account_{OTHER_ID}.json"] = account_bytes(OTHER_ID, VALID_ID)
        result = create_snapshot(
            host="ftp.example.test",
            port=21,
            user="admin",
            password="secret",
            destination=self.destination,
            server_stopped_attested=True,
            ftp_factory=lambda: fake,
        )
        self.assertEqual(result.invalid_account_count, 1)
        self.assertFalse(result.trusted_for_editing)

    def test_changed_remote_listing_aborts_and_removes_partial_snapshot(self) -> None:
        with self.assertRaises(RemoteError):
            create_snapshot(
                host="ftp.example.test",
                port=21,
                user="admin",
                password="secret",
                destination=self.destination,
                ftp_factory=lambda: FakeFTP(change_listing=True),
            )
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_unsafe_remote_root_is_rejected(self) -> None:
        with self.assertRaises(RemoteError):
            create_snapshot(
                host="ftp.example.test",
                port=21,
                user="admin",
                password="secret",
                destination=self.destination,
                remote_root="/profile/../secrets",
                ftp_factory=FakeFTP,
            )


if __name__ == "__main__":
    unittest.main()
