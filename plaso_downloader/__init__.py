"""plaso-downloader package."""

__all__ = ["main"]


def main() -> None:  # pragma: no cover - convenience re-export
    from .main import main as entry_main

    entry_main()
