"""Generate pydantic models from the shared JSON Schema; `--check` fails on drift.

The TypeScript contracts package is the source of truth (packages/contracts).
This turns its combined schema into one module the worker imports, so a field
added on one side of the seam cannot go unnoticed on the other.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT.parents[1] / "packages" / "contracts" / "schemas" / "contracts.json"
TARGET = ROOT / "src" / "temnia_pipeline" / "contracts.py"
REGENERATE = "pnpm --filter @temnia/pipeline contracts"
HEADER = (
    "# Generated from packages/contracts/schemas/contracts.json by scripts/gen_contracts.py.\n"
    f"# Do not edit; change the Zod schema and regenerate ({REGENERATE}).\n"
    "# ruff: noqa\n"
)


def render() -> str:
    """Run datamodel-codegen into a temp file and return its content with our header."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "contracts.py"
        subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(SCHEMA),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(out),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.13",
                "--use-standard-collections",
                "--use-union-operator",
                "--use-annotated",
                "--field-constraints",
                "--use-schema-description",
                "--disable-timestamp",
                "--use-double-quotes",
                "--formatters",
                "ruff-format",
            ],
            check=True,
        )
        return HEADER + out.read_text()


def main() -> int:
    """Write the module, or with `--check` compare it to what is committed."""
    rendered = render()
    if "--check" in sys.argv:
        current = TARGET.read_text() if TARGET.exists() else ""
        if current != rendered:
            sys.stderr.write(f"{TARGET.relative_to(ROOT)} is stale; run: {REGENERATE}\n")
            return 1
        sys.stdout.write("contracts module matches the schema\n")
        return 0
    TARGET.write_text(rendered)
    sys.stdout.write(f"wrote {TARGET.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
