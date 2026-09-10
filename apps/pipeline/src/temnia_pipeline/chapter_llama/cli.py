"""Explicit local inference and one-attempt Modal audition with retained handles."""

# ruff: noqa: EM101, TRY003
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

from temnia_pipeline.chapter_llama.client import ChapterLlamaModalClient
from temnia_pipeline.chapter_llama.contracts import ChapterLlamaInput, ChapterLlamaJob, canonical
from temnia_pipeline.chapter_llama.inference import TransformersEngine
from temnia_pipeline.contracts import TranscriptV1
from temnia_pipeline.substrate.chapter_llama import input_from_layers
from temnia_pipeline.substrate.factory import make_segmenter


def write_new(path: Path, payload: object) -> None:
    """Refuse to overwrite a prior attempt or its paid output."""
    with path.open("xb") as handle:
        handle.write(canonical(payload))


async def dispatch_once(
    client: ChapterLlamaModalClient,
    request: ChapterLlamaInput,
    *,
    organization_id: UUID,
    source_id: UUID,
    state_path: Path,
) -> None:
    """Persist intent before one spawn; an interrupted intent is never silently repeated."""
    identity = await client.deployment_identity()
    if identity.config != request.config:
        raise ValueError("requested model config differs from the deployed audition config")
    job = ChapterLlamaJob(
        organization_id=organization_id,
        source_id=source_id,
        operation_id=uuid4(),
        attempt_id=uuid4(),
        expected_build=identity.build,
        input=request,
    )
    state: dict[str, object] = {
        "state": "dispatch_intent",
        "job": job.model_dump(mode="json"),
        "deployment": identity.model_dump(mode="json"),
        "app_name": client.app_name,
        "environment": client.environment,
    }
    write_new(state_path, state)
    # A separate handle file is create-only too. If this window is interrupted,
    # inspect the persisted attempt's admission/outcome; do not dispatch again.
    handle = await client.spawn(job)
    write_new(state_path.with_suffix(state_path.suffix + ".handle"), {"handle": handle})


async def poll(state_path: Path, output: Path | None) -> str:
    """Read one known Modal handle and preserve the terminal envelope once."""
    state = json.loads(state_path.read_bytes())
    job = ChapterLlamaJob.model_validate(state["job"])
    handle_path = state_path.with_suffix(state_path.suffix + ".handle")
    if not handle_path.exists():
        return "outcome_unknown: dispatch intent exists without a retained handle; do not respawn"
    handle = json.loads(handle_path.read_bytes())["handle"]
    client = ChapterLlamaModalClient(app_name=state["app_name"], environment=state["environment"])
    observed = await client.status(handle, job=job)
    if observed.outcome is not None and output is not None:
        write_new(output, observed.outcome.model_dump(mode="json"))
    return observed.outcome.status if observed.outcome is not None else observed.status


def main() -> int:
    """Prepare timestamped input, infer locally, or dispatch/poll one remote attempt."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("input", help="freeze existing transcript sentence input")
    prepare.add_argument("transcript", type=Path)
    prepare.add_argument("--sentences-from", choices=("sat", "legacy"), default="sat")
    prepare.add_argument("--output", type=Path, required=True)
    local = commands.add_parser("local", help="infer once with prefetched local weights")
    local.add_argument("input", type=Path)
    local.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    local.add_argument("--output", type=Path, required=True)
    remote = commands.add_parser("spawn", help="record intent and dispatch one remote attempt")
    remote.add_argument("input", type=Path)
    remote.add_argument("--organization", type=UUID, required=True)
    remote.add_argument("--source", type=UUID, required=True)
    remote.add_argument("--state", type=Path, required=True)
    remote.add_argument("--environment", required=True)
    remote.add_argument("--app", default="temnia-chapter-llama")
    status = commands.add_parser("status", help="poll the retained handle without redispatch")
    status.add_argument("state", type=Path)
    status.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "input":
        transcript = TranscriptV1.model_validate_json(args.transcript.read_bytes())
        layers = make_segmenter(args.sentences_from).segment(transcript.words)
        request = input_from_layers(layers, duration_ms=transcript.durationMs)
        write_new(args.output, request.model_dump(mode="json"))
    elif args.command == "local":
        if args.output.exists():
            raise FileExistsError("refusing to repeat inference over an existing output")
        request = ChapterLlamaInput.model_validate_json(args.input.read_bytes())
        result = TransformersEngine(device=args.device).infer(request)
        write_new(args.output, result.model_dump(mode="json"))
    elif args.command == "spawn":
        asyncio.run(
            dispatch_once(
                ChapterLlamaModalClient(app_name=args.app, environment=args.environment),
                ChapterLlamaInput.model_validate_json(args.input.read_bytes()),
                organization_id=args.organization,
                source_id=args.source,
                state_path=args.state,
            )
        )
    else:
        print(asyncio.run(poll(args.state, args.output)))  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
