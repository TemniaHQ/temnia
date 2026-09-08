"""Generate pydantic models from the shared JSON Schema; `--check` fails on drift.

The TypeScript contracts package is the source of truth (packages/contracts).
This turns its combined schema into one module the worker imports, so a field
added on one side of the seam cannot go unnoticed on the other.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT.parents[1] / "packages" / "contracts" / "schemas" / "contracts.json"
TARGET = ROOT / "src" / "temnia_pipeline" / "contracts.py"
REGENERATE = "pnpm --filter @temnia/pipeline contracts"
HEADER = (
    "# Generated from packages/contracts/schemas/contracts.json by scripts/gen_contracts.py.\n"
    f"# Do not edit; change the Zod schema and regenerate ({REGENERATE}).\n"
    # constr(...) mapping keys are runtime-valid Pydantic annotations, but
    # Pyright does not permit call expressions in type positions.
    "# pyright: reportInvalidTypeForm=false\n"
    "# ruff: noqa\n"
)
ENUM_MEMBER = re.compile(r"^    ([A-Za-z_]\w*) = ")


def seeded_scope_constant() -> str:
    """Render the pre-identity scope from the SeededScope schema constants."""
    schema = json.loads(SCHEMA.read_text())
    properties = schema["$defs"]["SeededScope"]["properties"]
    organization_id = str(UUID(properties["organizationId"]["const"]))
    user_id = str(UUID(properties["userId"]["const"]))
    return (
        "\n\nSEEDED_SCOPE = Scope(\n"
        f'    organizationId=UUID("{organization_id}"),\n'
        f'    userId=UUID("{user_id}"),\n'
        ")\n"
    )


def python_input_schema() -> dict[str, object]:
    """Keep wire regexes out of fields already parsed as typed datetimes.

    Zod emits both ``format: date-time`` and its RFC 3339 string regex. The
    generator correctly maps the format to Pydantic's ``AwareDatetime``, but
    also attaches the string regex as a post-parse constraint. Pydantic then
    attempts to apply that regex to a ``datetime`` object and raises a
    ``TypeError``. ``AwareDatetime`` retains the semantic offset requirement;
    the original JSON Schema retains the exact wire regex for TypeScript and
    other schema consumers.
    """
    schema = cast("dict[str, object]", json.loads(SCHEMA.read_text()))

    def normalize(value: object) -> None:
        if isinstance(value, dict):
            mapping = cast("dict[str, object]", value)
            if mapping.get("type") == "string" and mapping.get("format") == "date-time":
                mapping.pop("pattern", None)
            for nested in mapping.values():
                normalize(nested)
        elif isinstance(value, list):
            for nested in cast("list[object]", value):
                normalize(nested)

    normalize(schema)
    return schema


def suppress_strenum_member_conflicts(body: str) -> str:
    """Ignore only generated enum members whose wire names shadow ``str`` methods."""
    inside_strenum = False
    rendered: list[str] = []
    for line in body.splitlines():
        if line.startswith("class "):
            inside_strenum = line.endswith("(StrEnum):")
        elif line and not line.startswith((" ", "@")):
            inside_strenum = False
        match = ENUM_MEMBER.match(line) if inside_strenum else None
        output_line = line
        if match is not None and match.group(1) in str.__dict__:
            output_line += "  # pyright: ignore[reportAssignmentType]"
        rendered.append(output_line)
    return "\n".join(rendered)


def render() -> str:
    """Run datamodel-codegen into a temp file and return its content with our header."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "contracts.py"
        input_schema = Path(tmp) / "contracts.json"
        input_schema.write_text(json.dumps(python_input_schema()))
        subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(input_schema),
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
                "--collapse-root-models",
                "--disable-timestamp",
                "--use-double-quotes",
                "--formatters",
                "ruff-format",
            ],
            check=True,
        )
        body = suppress_strenum_member_conflicts(out.read_text().rstrip())
        return HEADER + body + seeded_scope_constant()


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
