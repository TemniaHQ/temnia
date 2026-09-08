"""Install Temnia's pinned Silero ONNX asset after exact byte verification."""

from __future__ import annotations

import argparse
from pathlib import Path

from temnia_pipeline.speech.assets import install_asset


def main() -> None:
    """Fetch into an explicit build/setup destination; runtime code never calls this."""
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    installed = install_asset(args.destination)
    print(f"{'installed' if installed else 'verified'} {args.destination}")  # noqa: T201


if __name__ == "__main__":
    main()
