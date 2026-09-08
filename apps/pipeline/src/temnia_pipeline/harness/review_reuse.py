"""Checked-media reuse for review-only chapter revisions."""

# Public refusal messages live beside the invariant that rejects unsafe reuse.
# ruff: noqa: EM101, PLR0912, TRY003

from __future__ import annotations

import asyncio
from fractions import Fraction
from typing import TYPE_CHECKING, Any, cast

import obstore as obs

from temnia_pipeline import db
from temnia_pipeline.contracts import (
    ChapterChecks,
    ChapterEditSpec,
    ChapterRender,
    ChapterRenders,
    HarnessArtifactKind,
    HarnessArtifactRef,
    HarnessEvidence,
    Kind,
    ReviewState,
    Scope,
)
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.rendering import (
    build_render_descriptor,
    checks_metadata,
    descriptor_fingerprint,
    descriptor_metadata,
)
from temnia_pipeline.harness.runtime_types import (
    RenderRevisionResult,
    ReuseRevisionRenderOutcome,
    ReuseRevisionRenderRequest,
)
from temnia_pipeline.media.chapter_checks import technical_checks_pass
from temnia_pipeline.media.chapters import RENDERER_VERSION, MediaTimelineFacts

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from obstore.store import S3Store


class UnsafeReviewReuseError(RuntimeError):
    """A claimed metadata-only revision changed render-affecting content."""


def _artifact_ref(row: Mapping[str, Any]) -> HarnessArtifactRef:
    return HarnessArtifactRef(
        id=row["id"],
        kind=HarnessArtifactKind(str(row["kind"])),
        fingerprint=str(row["fingerprint"]),
        sha256=str(row["sha256"]),
        sizeBytes=int(row["size_bytes"]),
        storageKey=str(row["storage_key"]),
    )


def _render_content(edit: ChapterEditSpec) -> dict[str, object]:
    """Remove exactly the human review fields that cannot affect media bytes."""
    raw = cast("dict[str, object]", edit.model_dump(mode="json"))
    sections = cast("list[dict[str, object]]", raw["sections"])
    for section in sections:
        section.pop("reviewState")
        flags = cast("list[str]", section["flags"])
        section["flags"] = [
            flag for flag in flags if not flag.startswith(("accepted_reason:", "rejected_reason:"))
        ]
    return raw


def _fraction(value: object, name: str, *, optional: bool = False) -> Fraction | None:
    if value is None and optional:
        return None
    if not isinstance(value, dict):
        message = f"frozen source timeline {name} is invalid"
        raise UnsafeReviewReuseError(message)
    parts = cast("dict[str, object]", value)
    return Fraction(int(str(parts["numerator"])), int(str(parts["denominator"])))


def _timeline(evidence: HarnessEvidence) -> MediaTimelineFacts:
    raw = evidence.config.get("sourceTimeline")
    if not isinstance(raw, dict):
        raise UnsafeReviewReuseError("evidence has no frozen source timeline")
    value = cast("dict[str, object]", raw)
    return MediaTimelineFacts(
        duration=cast("Fraction", _fraction(value.get("duration"), "duration")),
        container_start=cast("Fraction", _fraction(value.get("containerStart"), "containerStart")),
        source_start=cast("Fraction", _fraction(value.get("sourceStart"), "sourceStart")),
        has_video=bool(value.get("hasVideo")),
        has_audio=bool(value.get("hasAudio")),
        video_stream_index=cast("int | None", value.get("videoStreamIndex")),
        audio_stream_index=cast("int | None", value.get("audioStreamIndex")),
        video_start=_fraction(value.get("videoStart"), "videoStart", optional=True),
        audio_start=_fraction(value.get("audioStart"), "audioStart", optional=True),
        video_duration=_fraction(value.get("videoDuration"), "videoDuration", optional=True),
        audio_duration=_fraction(value.get("audioDuration"), "audioDuration", optional=True),
        frame_rate=_fraction(value.get("frameRate"), "frameRate", optional=True),
        video_time_base=_fraction(value.get("videoTimeBase"), "videoTimeBase", optional=True),
        audio_time_base=_fraction(value.get("audioTimeBase"), "audioTimeBase", optional=True),
        sample_rate=cast("int | None", value.get("sampleRate")),
        width=cast("int | None", value.get("width")),
        height=cast("int | None", value.get("height")),
        rotation=cast("int | None", value.get("rotation")),
        audio_channels=cast("int | None", value.get("audioChannels")),
        audio_layout=cast("str | None", value.get("audioLayout")),
        variable_frame_rate=bool(value.get("variableFrameRate")),
        video_codec=cast("str | None", value.get("videoCodec")),
        audio_codec=cast("str | None", value.get("audioCodec")),
        sample_aspect_ratio=_fraction(
            value.get("sampleAspectRatio"), "sampleAspectRatio", optional=True
        ),
    )


async def _require_objects(store: S3Store, refs: tuple[HarnessArtifactRef, ...]) -> None:
    """Bounded HEAD checks prove reused media and captions still exist at exact sizes."""
    unique = {item.id: item for item in refs}
    for item in unique.values():
        try:
            head = await obs.head_async(store, item.storageKey)
        except FileNotFoundError as error:
            raise UnsafeReviewReuseError("a previously checked render object is missing") from error
        if int(head["size"]) != item.sizeBytes:  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            raise UnsafeReviewReuseError("a previously checked render object changed size")


async def reuse_review_render(  # noqa: C901, PLR0915
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    request: ReuseRevisionRenderRequest,
) -> ReuseRevisionRenderOutcome:
    """Rebind immutable checked bytes only when the edit changed review metadata."""
    if request.revision != request.predecessor_revision + 1:
        raise UnsafeReviewReuseError("review reuse requires the immediate predecessor revision")
    if request.edit.kind != HarnessArtifactKind.edit:
        raise UnsafeReviewReuseError("review reuse requires an edit artifact")
    async with db.scoped(database_url, scope) as conn:
        source = await (
            await conn.execute(
                "SELECT deletion_requested_at FROM source WHERE id = %s FOR UPDATE",
                (source_id,),
            )
        ).fetchone()
        if source is None or source["deletion_requested_at"] is not None:
            raise UnsafeReviewReuseError("source is absent or fenced for deletion")
        run = await (
            await conn.execute(
                "SELECT * FROM harness_run WHERE id = %s AND source_id = %s FOR UPDATE",
                (request.run.run_id, source_id),
            )
        ).fetchone()
        if (
            run is None
            or int(run["current_revision"]) != request.revision
            or run["stage"] != "render"
            or run["status"] != "running"
        ):
            raise UnsafeReviewReuseError("review reuse is stale for the active run revision")
        revisions = await (
            await conn.execute(
                """
                SELECT r.revision, r.base_revision, a.*
                  FROM chapter_revision r
                  JOIN harness_artifact a ON a.id = r.artifact_id
                 WHERE r.run_id = %s AND r.source_id = %s
                   AND r.revision IN (%s, %s)
                """,
                (
                    request.run.run_id,
                    source_id,
                    request.predecessor_revision,
                    request.revision,
                ),
            )
        ).fetchall()
        by_revision = {int(row["revision"]): row for row in revisions}
        predecessor_row = by_revision.get(request.predecessor_revision)
        current_row = by_revision.get(request.revision)
        if predecessor_row is None or current_row is None:
            raise UnsafeReviewReuseError("review reuse revisions are incomplete")
        if (
            current_row["base_revision"] != request.predecessor_revision
            or _artifact_ref(current_row) != request.edit
        ):
            raise UnsafeReviewReuseError(
                "review reuse artifact is not the committed child revision"
            )
        descriptor_row = await (
            await conn.execute(
                """
                SELECT * FROM harness_artifact
                 WHERE source_id = %s AND kind = 'render'
                   AND metadata->>'format' = 'chapter-renders/1'
                   AND metadata->>'runId' = %s
                   AND metadata->>'editSha256' = %s
                 ORDER BY created_at DESC LIMIT 1
                """,
                (source_id, str(request.run.run_id), str(predecessor_row["sha256"])),
            )
        ).fetchone()
        if descriptor_row is None:
            if request.required:
                raise UnsafeReviewReuseError("metadata-only review has no predecessor render")
            return ReuseRevisionRenderOutcome()
        evidence_artifact_id = run["evidence_artifact_id"]

    predecessor_raw, current_raw, descriptor_raw, evidence_raw = await asyncio.gather(
        artifacts.read_artifact_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            artifact_id=predecessor_row["id"],
        ),
        artifacts.read_artifact_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            artifact_id=current_row["id"],
        ),
        artifacts.read_artifact_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            artifact_id=descriptor_row["id"],
        ),
        artifacts.read_artifact_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            artifact_id=evidence_artifact_id,
        ),
    )
    predecessor = ChapterEditSpec.model_validate(predecessor_raw)
    current = ChapterEditSpec.model_validate(current_raw)
    if _render_content(predecessor) != _render_content(current):
        if request.required:
            raise UnsafeReviewReuseError("review changed render-affecting chapter content")
        return ReuseRevisionRenderOutcome()
    descriptor = ChapterRenders.model_validate(descriptor_raw)
    if descriptor.editSha256 != str(predecessor_row["sha256"]):
        raise UnsafeReviewReuseError("predecessor descriptor names a different edit")
    expected_sections = {section.id for section in current.sections if section.kind == Kind.keep}
    if {render.sectionId for render in descriptor.renders} != expected_sections:
        raise UnsafeReviewReuseError(
            "predecessor descriptor does not cover the current keep sections"
        )
    evidence = HarnessEvidence.model_validate(evidence_raw)
    timeline = _timeline(evidence)

    all_refs = tuple(
        item
        for render in descriptor.renders
        for item in (render.media, render.captions, render.checks)
        if item is not None
    )
    expected_by_id = {item.id: item for item in all_refs}
    async with db.scoped(database_url, scope) as conn:
        rows = await (
            await conn.execute(
                "SELECT * FROM harness_artifact WHERE source_id = %s AND id = ANY(%s)",
                (source_id, list(expected_by_id)),
            )
        ).fetchall()
        actual_by_id = {row["id"]: _artifact_ref(row) for row in rows}
        if actual_by_id != expected_by_id:
            raise UnsafeReviewReuseError("predecessor descriptor component identity changed")
        dependency_rows = await (
            await conn.execute(
                """
                SELECT input_artifact_id FROM harness_artifact_dependency
                 WHERE artifact_id = %s
                """,
                (descriptor_row["id"],),
            )
        ).fetchall()
        descriptor_dependencies = {row["input_artifact_id"] for row in dependency_rows}
        if not {predecessor_row["id"], *expected_by_id}.issubset(descriptor_dependencies):
            raise UnsafeReviewReuseError("predecessor descriptor lineage is incomplete")
    component_refs = tuple(
        item
        for render in descriptor.renders
        for item in (render.media, render.captions)
        if item is not None
    )
    await _require_objects(store, component_refs)
    cloned_renders: list[ChapterRender] = []
    cloned_reports: list[dict[str, object]] = []
    for render in descriptor.renders:
        if render.checks is None:
            raise UnsafeReviewReuseError("predecessor render has no technical checks")
        checks_raw = await artifacts.read_artifact_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            artifact_id=render.checks.id,
        )
        checks = ChapterChecks.model_validate(checks_raw)
        if checks.editSha256 != str(predecessor_row["sha256"]) or not technical_checks_pass(
            checks, source=timeline
        ):
            raise UnsafeReviewReuseError("predecessor technical checks are not eligible for reuse")
        rebound = checks.model_copy(update={"editSha256": request.edit.sha256})
        identity = artifacts.ArtifactIdentity(
            kind="checks",
            fingerprint=artifacts.fingerprint_for(
                kind="chapter_checks_review_reuse",
                inputs={
                    "editArtifactId": str(request.edit.id),
                    "editSha256": request.edit.sha256,
                    "predecessorChecksId": str(render.checks.id),
                    "predecessorChecksSha256": render.checks.sha256,
                    "sectionId": render.sectionId,
                },
                config={"format": "chapter-checks-review-reuse/1"},
            ),
        )
        dependencies = [request.edit.id, render.checks.id, render.media.id]
        if render.captions is not None:
            dependencies.append(render.captions.id)
        accepted_checks = await artifacts.publish_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            identity=identity,
            content=rebound.model_dump(mode="json"),
            metadata=checks_metadata(
                section_id=render.sectionId,
                edit_sha256=request.edit.sha256,
            ),
            dependency_ids=tuple(dependencies),
        )
        checks_ref = HarnessArtifactRef(
            id=accepted_checks.id,
            kind=HarnessArtifactKind.checks,
            fingerprint=accepted_checks.fingerprint,
            sha256=accepted_checks.sha256,
            sizeBytes=accepted_checks.size_bytes,
            storageKey=accepted_checks.storage_key,
        )
        cloned_renders.append(
            render.model_copy(update={"checks": checks_ref, "editSha256": request.edit.sha256})
        )
        cloned_reports.append(cast("dict[str, object]", rebound.model_dump(mode="json")))

    new_descriptor = build_render_descriptor(
        run_id=request.run.run_id,
        edit_sha256=request.edit.sha256,
        renders=cloned_renders,
    )
    descriptor_identity = artifacts.ArtifactIdentity(
        kind="render",
        fingerprint=descriptor_fingerprint(
            edit_sha256=request.edit.sha256,
            renders=cloned_renders,
            renderer_version=RENDERER_VERSION,
        ),
    )
    descriptor_dependencies = [
        request.edit.id,
        descriptor_row["id"],
        *(render.media.id for render in cloned_renders),
        *(render.captions.id for render in cloned_renders if render.captions is not None),
        *(render.checks.id for render in cloned_renders if render.checks is not None),
    ]
    accepted_descriptor = await artifacts.publish_json(
        database_url,
        scope=scope,
        source_id=source_id,
        store=store,
        identity=descriptor_identity,
        content=new_descriptor.model_dump(mode="json"),
        metadata=descriptor_metadata(
            run_id=request.run.run_id,
            edit_sha256=request.edit.sha256,
            render_count=len(cloned_renders),
        ),
        dependency_ids=tuple(dict.fromkeys(descriptor_dependencies)),
    )
    accepted_descriptor_ref = HarnessArtifactRef(
        id=accepted_descriptor.id,
        kind=HarnessArtifactKind.render,
        fingerprint=accepted_descriptor.fingerprint,
        sha256=accepted_descriptor.sha256,
        sizeBytes=accepted_descriptor.size_bytes,
        storageKey=accepted_descriptor.storage_key,
    )
    async with db.scoped(database_url, scope) as conn:
        verification_rows = await (
            await conn.execute(
                """
                SELECT a.* FROM harness_artifact a
                JOIN harness_artifact_dependency d
                  ON d.artifact_id = a.id AND d.input_artifact_id = %s
                 WHERE a.source_id = %s AND a.kind = 'checks'
                   AND a.metadata->>'format' = 'chapter-verification/1'
                   AND a.metadata->>'runId' = %s
                   AND a.metadata->>'editSha256' = %s
                   AND a.metadata->>'revision' = %s
                 ORDER BY a.created_at DESC LIMIT 2
                """,
                (
                    descriptor_row["id"],
                    source_id,
                    str(request.run.run_id),
                    str(predecessor_row["sha256"]),
                    str(request.predecessor_revision),
                ),
            )
        ).fetchall()
    if len(verification_rows) > 1:
        raise UnsafeReviewReuseError("predecessor has ambiguous editorial verification evidence")
    if verification_rows:
        predecessor_verification = verification_rows[0]
        verification_raw = await artifacts.read_artifact_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            artifact_id=predecessor_verification["id"],
        )
        if not isinstance(verification_raw, dict):
            raise UnsafeReviewReuseError("predecessor editorial verification is invalid")
        verification_body = cast("dict[str, object]", verification_raw)
        if verification_body.get("format") != "chapter-verification/1":
            raise UnsafeReviewReuseError("predecessor editorial verification format is invalid")
        async with db.scoped(database_url, scope) as conn:
            dependencies = await (
                await conn.execute(
                    """
                    SELECT a.* FROM harness_artifact_dependency d
                    JOIN harness_artifact a ON a.id = d.input_artifact_id
                     WHERE d.artifact_id = %s
                    """,
                    (predecessor_verification["id"],),
                )
            ).fetchall()
        dependency_by_kind = {str(row["kind"]): row for row in dependencies}
        model_response = dependency_by_kind.get("model_response")
        if (
            model_response is None
            or predecessor_row["id"] not in {row["id"] for row in dependencies}
            or descriptor_row["id"] not in {row["id"] for row in dependencies}
        ):
            raise UnsafeReviewReuseError("predecessor editorial verification lineage is incomplete")
        verifier_family = predecessor_verification["metadata"].get("verifierFamily")
        if not isinstance(verifier_family, str) or not verifier_family:
            raise UnsafeReviewReuseError("predecessor verifier family is invalid")
        verification_identity = artifacts.ArtifactIdentity(
            kind="checks",
            fingerprint=artifacts.fingerprint_for(
                kind="checks",
                inputs={
                    "descriptorArtifactId": str(accepted_descriptor.id),
                    "descriptorSha256": accepted_descriptor.sha256,
                    "editArtifactId": str(request.edit.id),
                    "editSha256": request.edit.sha256,
                    "modelResponseArtifactId": str(model_response["id"]),
                    "modelResponseSha256": str(model_response["sha256"]),
                },
                config={"format": "chapter-verification/1"},
            ),
        )
        await artifacts.publish_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            identity=verification_identity,
            content=verification_body,
            metadata={
                "editSha256": request.edit.sha256,
                "format": "chapter-verification/1",
                "reusedFromArtifactId": str(predecessor_verification["id"]),
                "revision": request.revision,
                "runId": str(request.run.run_id),
                "verifierFamily": verifier_family,
            },
            dependency_ids=(
                request.edit.id,
                accepted_descriptor.id,
                model_response["id"],
                predecessor_verification["id"],
            ),
        )
    return ReuseRevisionRenderOutcome(
        reused=RenderRevisionResult(
            descriptor=accepted_descriptor_ref,
            has_kept_sections=bool(cloned_renders),
            technical_report=tuple(cloned_reports),
            technical_passed=True,
        ),
        all_sections_accepted=all(
            section.reviewState == ReviewState.accepted for section in current.sections
        ),
    )
