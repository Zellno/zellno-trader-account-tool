"""PyInstaller entry point for the Windows standalone executable."""

from zellno_trader.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
