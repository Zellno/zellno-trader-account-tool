from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from zellno_trader import __version__


class PackagingTests(unittest.TestCase):
    def test_project_metadata_matches_runtime_version(self) -> None:
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["project"]["version"], __version__)


if __name__ == "__main__":
    unittest.main()
