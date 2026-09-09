# ruff: noqa: SLF001
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from temnia_pipeline.modal_build import BUILD_ENV
from temnia_pipeline.settings import TranscriptionSettings
from temnia_pipeline.speech.client import (
    SpeechDeploymentError,
    SpeechDeploymentIdentity,
    SpeechModalClient,
    assert_checkpointed_deployment,
)
from temnia_pipeline.speech.contracts import ExecutionIdentity, StageTelemetry
from temnia_pipeline.speech.contracts_v2 import (
    AlignConfigV2,
    ArtifactRefV2,
    RecognizeConfigV2,
    SpeechStageJobV2,
    SpeechStageResultV2,
)
from temnia_pipeline.speech.progress_transport import ProgressPublishDiagnostics
from temnia_pipeline.speech.resources import SpeechModelManifest, SpeechResourceProfile
from temnia_pipeline.transcription.checkpointed import TranscriptionPlan

MODULE = "temnia_pipeline.modal_speech_v2_app"

if TYPE_CHECKING:
    from collections.abc import Generator


class FakeImage:
    def __init__(self) -> None:
        self.environment: dict[str, str] = {}
        self.functions: list[object] = []
        self.python_sources: list[str] = []

    @classmethod
    def from_dockerfile(cls, *_args: object, **_kwargs: object) -> FakeImage:
        return cls()

    def pip_install(self, *_args: object, **_kwargs: object) -> FakeImage:
        return self

    def run_function(self, function: object, **_kwargs: object) -> FakeImage:
        self.functions.append(function)
        return self

    def env(self, values: dict[str, str]) -> FakeImage:
        self.environment.update(values)
        return self

    def add_local_file(self, *_args: object, **_kwargs: object) -> FakeImage:
        return self

    def add_local_python_source(self, name: str) -> FakeImage:
        self.python_sources.append(name)
        return self


class FakeVolume:
    @classmethod
    def from_name(cls, *_args: object, **_kwargs: object) -> FakeVolume:
        return cls()

    def with_mount_options(self, **_kwargs: object) -> FakeVolume:
        return self


class FakeSecret:
    @staticmethod
    def from_name(name: str) -> str:
        return name


class FakeApp:
    def __init__(self, name: str, *, image: FakeImage) -> None:
        self.name = name
        self.image = image
        self.functions: list[dict[str, object]] = []

    def function(self, **options: object) -> Any:  # noqa: ANN401
        self.functions.append(options)

        def decorate(function: object) -> object:
            return function

        return decorate


def manifest(*, image_assets: str | None = "b" * 64) -> SpeechModelManifest:
    return SpeechModelManifest(
        sha256="a" * 64,
        file_count=3,
        total_bytes=4096,
        model_root=f"/models/frozen/{'a' * 64}",
        image_assets_sha256=image_assets,
    )


@contextmanager
def imported_v2(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile: SpeechResourceProfile | None = None,
    model_manifest: SpeechModelManifest | None = None,
    local: bool = False,
    actual_image_assets: str = "b" * 64,
) -> Generator[tuple[ModuleType, SimpleNamespace]]:
    resource_profile = profile or SpeechResourceProfile()
    frozen_manifest = model_manifest or manifest()
    image_models = importlib.import_module("temnia_pipeline.speech.image_models")
    modal_build = importlib.import_module("temnia_pipeline.modal_build")
    fake_modal = SimpleNamespace(
        App=FakeApp,
        Image=FakeImage,
        Secret=FakeSecret,
        Volume=FakeVolume,
        current_function_call_id=lambda: "call-id",
        is_local=lambda: local,
    )
    previous = sys.modules.pop(MODULE, None)
    try:
        with monkeypatch.context() as scoped:
            scoped.setitem(sys.modules, "modal", fake_modal)
            scoped.setattr(modal_build, "source_build_id", lambda: "local-build")
            scoped.setattr(
                image_models,
                "image_assets_sha256",
                lambda **_kwargs: actual_image_assets,
            )
            scoped.setenv("MODAL_TASK_ID", "task-id")
            scoped.setenv("MODAL_SPEECH_V2_APP", "temnia-speech-v2-test")
            scoped.setenv(
                "MODAL_SPEECH_RESOURCE_PROFILE",
                resource_profile.model_dump_json(by_alias=True),
            )
            scoped.setenv(
                "MODAL_SPEECH_MODEL_MANIFEST",
                frozen_manifest.model_dump_json(by_alias=True),
            )
            scoped.setenv("MODAL_SPEECH_MODEL_VOLUME", "temnia-speech-models-test")
            scoped.setenv(BUILD_ENV, "remote-build")
            yield importlib.import_module(MODULE), fake_modal
    finally:
        sys.modules.pop(MODULE, None)
        if previous is not None:
            sys.modules[MODULE] = previous


def job(module: ModuleType) -> SpeechStageJobV2:
    attempt = uuid4()
    prefix = f"org/{uuid4()}/source/{uuid4()}/"
    return SpeechStageJobV2(
        build=cast("str", module.BUILD_ID),
        operation_id=uuid4(),
        attempt_id=attempt,
        stage="recognize",
        artifact_prefix=prefix,
        audio_key=f"{prefix}audio/audio.m4a",
        audio_sha256="c" * 64,
        audio_size_bytes=100,
        duration_ms=1000,
        checkpoint_key=f"{prefix}transcript/v2/checkpoints/recognize/{attempt}.json",
        configuration=RecognizeConfigV2(),
        resource_profile=cast("SpeechResourceProfile", module.RESOURCE_PROFILE),
        model_manifest=cast("SpeechModelManifest", module.MODEL_MANIFEST),
        execution_topology="parallel",
    )


def reference(request: SpeechStageJobV2) -> ArtifactRefV2:
    return ArtifactRefV2(
        key=request.checkpoint_key,
        sha256="d" * 64,
        size_bytes=100,
        stage=request.stage,
        configuration_sha256=request.configuration_sha256,
    )


def align_job(*, language: str, model: str | None = None) -> SpeechStageJobV2:
    attempt = uuid4()
    prefix = f"org/{uuid4()}/source/{uuid4()}/"
    source = ArtifactRefV2(
        key=f"{prefix}transcript/v2/checkpoints/recognize/{uuid4()}.json",
        sha256="c" * 64,
        size_bytes=100,
        stage="recognize",
        configuration_sha256="d" * 64,
    )
    return SpeechStageJobV2(
        build="cache-test",
        operation_id=uuid4(),
        attempt_id=attempt,
        stage="align",
        artifact_prefix=prefix,
        audio_key=f"{prefix}audio/audio.m4a",
        audio_sha256="e" * 64,
        audio_size_bytes=100,
        duration_ms=1000,
        checkpoint_key=f"{prefix}transcript/v2/checkpoints/align/{attempt}.json",
        configuration=AlignConfigV2(language=language, model=model),
        input_checkpoint=source,
        resource_profile=SpeechResourceProfile(),
        model_manifest=manifest(),
        execution_topology="parallel",
    )


def diagnostics() -> ProgressPublishDiagnostics:
    return ProgressPublishDiagnostics(
        callback_count=0,
        accepted_callback_count=0,
        regressed_callback_count=0,
        invalid_callback_count=0,
        callback_after_close_count=0,
        callback_elapsed_seconds=0,
        publish_attempt_count=0,
        publish_success_count=0,
        publish_timeout_count=0,
        publish_error_count=0,
        publish_elapsed_seconds=0,
        elapsed_seconds=0,
        last_callback_seconds=None,
        last_publish_seconds=None,
        last_published_percent=None,
        pending_percent=None,
        last_error=None,
        final_flush_completed=True,
        shutdown_timed_out=False,
    )


@pytest.mark.parametrize(
    ("resource_profile", "expected_cpu", "expected_timeout"),
    [
        (SpeechResourceProfile(cpu_cores=4, stage_timeout_seconds=3600), (4, 4), 3600),
        (
            SpeechResourceProfile(
                cpu_cores=8,
                stage_timeout_seconds=900,
                progress_mode="synchronous_control",
            ),
            (8, 8),
            900,
        ),
    ],
)
def test_decorator_uses_the_exact_frozen_resource_profile(
    monkeypatch: pytest.MonkeyPatch,
    resource_profile: SpeechResourceProfile,
    expected_cpu: tuple[int, int],
    expected_timeout: int,
) -> None:
    with imported_v2(monkeypatch, profile=resource_profile) as (module, _modal):
        options = cast("dict[str, object]", module._FUNCTION)
        assert options["gpu"] == "L4"
        assert options["cpu"] == expected_cpu
        assert options["memory"] == (16_384, 16_384)
        assert options["timeout"] == expected_timeout
        assert options["startup_timeout"] == 120
        assert options["retries"] == 0
        assert options["single_use_containers"] is True
        assert (
            resource_profile.reservation_micros(resource_profile.minimum_rate_micros_per_hour)
            == (
                resource_profile.minimum_rate_micros_per_hour
                * (resource_profile.stage_timeout_seconds + 120)
                + 3599
            )
            // 3600
        )


def test_cold_remote_import_propagates_frozen_model_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = manifest()
    with imported_v2(monkeypatch, model_manifest=frozen, local=False) as (module, _modal):
        image = cast("FakeImage", module.image)
        assert module.BUILD_ID == "remote-build"
        assert image.environment[BUILD_ENV] == "remote-build"
        assert image.environment["HF_HOME"] == frozen.model_root
        assert image.environment["TORCH_HOME"] == frozen.model_root
        assert image.environment["HF_HUB_OFFLINE"] == "1"
        assert image.environment["TRANSFORMERS_OFFLINE"] == "1"
        assert image.environment["MODAL_SPEECH_V2_APP"] == "temnia-speech-v2-test"
        assert image.environment["MODAL_SPEECH_MODEL_VOLUME"] == "temnia-speech-models-test"
        assert (
            json.loads(image.environment["MODAL_SPEECH_MODEL_MANIFEST"])["imageAssetsSha256"]
            == "b" * 64
        )
        assert image.python_sources == ["temnia_pipeline"]


def test_cold_import_reports_actual_image_assets_for_predispatch_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with imported_v2(
        monkeypatch,
        model_manifest=manifest(image_assets="b" * 64),
        local=False,
        actual_image_assets="e" * 64,
    ) as (module, _modal):
        deployed = cast("SpeechModelManifest", module.MODEL_MANIFEST)
        assert deployed.image_assets_sha256 == "e" * 64
        assert module.deployment_identity()["model_manifest"]["imageAssetsSha256"] == "e" * 64


def test_module_entrypoint_resolves_the_real_pipeline_build_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with imported_v2(monkeypatch, local=True) as (module, _modal):
        assert cast("Path", module.SPEECH_DOCKERFILE).is_file()
        assert cast("Path", module.PIPELINE_ROOT).name == "pipeline"
        assert "temnia_pipeline" in Path(cast("str", module.__file__)).parts


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["profile", "manifest"])
async def test_request_identity_mismatch_fails_before_admission_or_model_work(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    with imported_v2(monkeypatch) as (module, _modal):
        request = job(module)
        if mismatch == "profile":
            request = request.model_copy(
                update={"resource_profile": SpeechResourceProfile(cpu_cores=8)}
            )
        else:
            request = request.model_copy(update={"model_manifest": manifest(image_assets="e" * 64)})
        admission_called = False
        model_called = False

        async def prepare(*_args: object, **_kwargs: object) -> tuple[None, None]:
            nonlocal admission_called
            admission_called = True
            return None, None

        def load(_stage: str) -> object:
            nonlocal model_called
            model_called = True
            return object()

        monkeypatch.setattr(module, "prepare_execution", prepare)
        monkeypatch.setattr(module, "_load_whisperx", load)
        result = SpeechStageResultV2.model_validate(
            await module._execute(request.model_dump(mode="json", by_alias=True), "recognize")
        )
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.retry_class == "terminal"
        assert not admission_called
        assert not model_called


@pytest.mark.asyncio
async def test_malformed_job_fails_without_storage_admission_or_model_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with imported_v2(monkeypatch) as (module, _modal):
        touched: list[str] = []
        monkeypatch.setattr(module, "make_store", lambda _settings: touched.append("storage"))
        monkeypatch.setattr(module, "prepare_execution", lambda *_args: touched.append("admission"))
        monkeypatch.setattr(module, "_load_whisperx", lambda _stage: touched.append("model"))
        result = SpeechStageResultV2.model_validate(await module._execute({}, "recognize"))
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.type == "ValidationError"
        assert touched == []


class FakeRecorder:
    def __init__(self, **_kwargs: object) -> None:
        self.inference_completed = False
        self.attach_count = 0

    def set_progress_callback(self, _callback: object) -> None:
        return

    def mark_unavailable(self, _reason: str) -> None:
        return

    def attach_progress_diagnostics(self, _diagnostics: ProgressPublishDiagnostics) -> None:
        self.attach_count += 1

    def phase(self, _name: str) -> Any:  # noqa: ANN401
        return nullcontext()

    def start(self, _torch: object) -> None:
        return

    def finish(self, *, complete: bool) -> StageTelemetry:
        return StageTelemetry(complete=complete)


class LifecyclePublisher:
    def __init__(self) -> None:
        self.start_count = 0
        self.close_count = 0

    def start(self) -> None:
        self.start_count += 1

    def callback(self, _value: object) -> None:
        return

    def close(self) -> ProgressPublishDiagnostics:
        self.close_count += 1
        return diagnostics()


def stub_admitted_execution(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    reused: ArtifactRefV2 | None,
) -> None:
    monkeypatch.setattr(
        module.StorageSettings,
        "require_env",
        classmethod(lambda _cls, _context: object()),
    )
    monkeypatch.setattr(module, "make_store", lambda _settings: object())
    monkeypatch.setattr(module, "ObstoreCheckpointStore", lambda _store: object())

    async def prepare(
        *_args: object, **_kwargs: object
    ) -> tuple[ArtifactRefV2 | None, ExecutionIdentity]:
        return reused, ExecutionIdentity(
            modal_call_id="call-id",
            modal_task_id="producer-task" if reused is not None else "task-id",
            admission_key="source/transcript/v2/admissions/recognize/admission.json",
        )

    monkeypatch.setattr(module, "prepare_execution", prepare)


@pytest.mark.asyncio
async def test_checkpoint_reuse_never_starts_a_progress_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with imported_v2(monkeypatch) as (module, _modal):
        request = job(module)
        stub_admitted_execution(module, monkeypatch, reused=reference(request))
        publisher_called = False

        def publisher(**_kwargs: object) -> LifecyclePublisher:
            nonlocal publisher_called
            publisher_called = True
            return LifecyclePublisher()

        monkeypatch.setattr(module, "progress_publisher", publisher)
        result = SpeechStageResultV2.model_validate(
            await module._execute(request.model_dump(mode="json", by_alias=True), "recognize")
        )
        assert result.status == "ok"
        assert result.checkpoint == reference(request)
        assert result.checkpoint_reused
        assert result.modal_task_id == "task-id"
        assert result.execution_identity is not None
        assert result.execution_identity.modal_task_id == "producer-task"
        assert not result.telemetry.complete
        assert (
            "checkpoint_reused_without_original_container_metrics"
            in result.telemetry.metrics_unavailable
        )
        assert result.telemetry.progress_transport is None
        assert not publisher_called


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "error", "cancel"])
async def test_every_started_publisher_is_closed(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    with imported_v2(monkeypatch) as (module, _modal):
        request = job(module)
        stub_admitted_execution(module, monkeypatch, reused=None)
        recorder = FakeRecorder()
        publisher = LifecyclePublisher()
        monkeypatch.setattr(module, "TelemetryRecorder", lambda **_kwargs: recorder)
        monkeypatch.setattr(module, "progress_publisher", lambda **_kwargs: publisher)

        async def download(*_args: object, **_kwargs: object) -> None:
            if outcome == "error":
                message = "download failed"
                raise RuntimeError(message)
            if outcome == "cancel":
                raise asyncio.CancelledError

        async def run_recognize(*_args: object, **_kwargs: object) -> ArtifactRefV2:
            return reference(request)

        monkeypatch.setattr(module, "download", download)
        monkeypatch.setattr(module, "_sha256_file", lambda _path: request.audio_sha256)
        monkeypatch.setattr(module, "verify_model_files", lambda _manifest: None)
        monkeypatch.setattr(
            module,
            "image_assets_sha256",
            lambda **_kwargs: request.model_manifest.image_assets_sha256,
        )
        monkeypatch.setattr(module, "_load_whisperx", lambda _stage: object())
        monkeypatch.setattr(module, "import_module", lambda _name: SimpleNamespace(cuda=object()))
        monkeypatch.setattr(module, "run_recognize", run_recognize)

        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await module._execute(request.model_dump(mode="json", by_alias=True), "recognize")
        else:
            result = SpeechStageResultV2.model_validate(
                await module._execute(request.model_dump(mode="json", by_alias=True), "recognize")
            )
            assert result.status == outcome.replace("error", "failed")
            assert recorder.attach_count == 1
        assert publisher.start_count == 1
        assert publisher.close_count >= 1


@pytest.mark.asyncio
async def test_boot_identity_mismatch_refuses_before_a_client_can_spawn() -> None:
    profile = SpeechResourceProfile()
    configured_manifest = manifest()
    settings = TranscriptionSettings(
        provider="modal-checkpointed",
        modal_app="temnia-media",
        modal_environment="test",
        progress_dict="unused",
        recordings_dir=Path("recordings"),
        recording=None,
        speech_modal_app="temnia-speech-v2-test",
        speech_protocol="temnia-speech/2",
        speech_expected_build="build",
        speech_rate_micros_per_hour=profile.minimum_rate_micros_per_hour,
        speech_resource_profile=profile,
        speech_model_manifest=configured_manifest,
    )
    spawned = False

    class Client(SpeechModalClient):
        async def deployment_identity(self) -> SpeechDeploymentIdentity:
            return SpeechDeploymentIdentity(
                protocol="temnia-speech/2",
                build="build",
                resource_profile=profile.model_copy(update={"cpu_cores": 8}),
                model_manifest=configured_manifest,
            )

        async def spawn(self, *_args: object, **_kwargs: object) -> str:
            nonlocal spawned
            spawned = True
            return "unexpected"

    with pytest.raises(SpeechDeploymentError, match="does not match"):
        await assert_checkpointed_deployment(Client(settings), settings)
    assert not spawned


def test_settings_freeze_v2_deadlines_rate_topology_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SpeechResourceProfile(cpu_cores=8)
    frozen = manifest()
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "modal-checkpointed")
    monkeypatch.setenv("MODAL_SPEECH_PROTOCOL", "temnia-speech/2")
    monkeypatch.setenv("MODAL_SPEECH_APP", "temnia-speech-v2-test")
    monkeypatch.setenv("MODAL_SPEECH_BUILD", "build")
    monkeypatch.setenv("MODAL_SPEECH_RESOURCE_PROFILE", profile.model_dump_json(by_alias=True))
    monkeypatch.setenv("MODAL_SPEECH_MODEL_MANIFEST", frozen.model_dump_json(by_alias=True))
    monkeypatch.setenv("SPEECH_STAGE_TIMEOUT_SECONDS", str(profile.stage_timeout_seconds))
    monkeypatch.setenv("SPEECH_STARTUP_TIMEOUT_SECONDS", str(profile.startup_timeout_seconds))
    monkeypatch.setenv("SPEECH_RATE_MICROS_PER_HOUR", str(profile.minimum_rate_micros_per_hour))
    monkeypatch.setenv("SPEECH_EXECUTION_TOPOLOGY", "parallel")
    settings = TranscriptionSettings.from_env()
    assert settings.speech_modal_app == "temnia-speech-v2-test"
    assert settings.speech_stage_timeout_seconds == profile.stage_timeout_seconds
    assert settings.speech_startup_timeout_seconds == profile.startup_timeout_seconds
    assert settings.speech_rate_micros_per_hour == profile.minimum_rate_micros_per_hour
    assert settings.speech_resource_profile == profile
    assert settings.speech_model_manifest == frozen
    assert settings.speech_execution_topology == "parallel"
    monkeypatch.delenv("SPEECH_EXECUTION_TOPOLOGY")
    with pytest.raises(ValueError, match="explicitly qualified SPEECH_EXECUTION_TOPOLOGY"):
        TranscriptionSettings.from_env()


def test_alignment_aliases_resolve_only_declared_offline_cache_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stages = importlib.import_module("temnia_pipeline.speech.stages_v2")
    frozen_root = manifest().model_root
    real_path = Path
    monkeypatch.setattr(
        stages,
        "Path",
        lambda value: tmp_path if str(value) == frozen_root else real_path(value),
    )

    english = align_job(language="en")
    with pytest.raises(FileNotFoundError, match="English alignment model is missing"):
        stages.alignment_model_cache(english)
    checkpoint_dir = tmp_path / "hub" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "wav2vec2_fairseq_base_ls960_asr_ls960.pth").write_bytes(b"model")
    assert stages.alignment_model_cache(english) == checkpoint_dir

    explicit_english = align_job(language="en", model="WAV2VEC2_ASR_BASE_960H")
    assert stages.alignment_model_cache(explicit_english) == checkpoint_dir
    with pytest.raises(FileNotFoundError, match="not declared for fr"):
        stages.alignment_model_cache(align_job(language="fr"))
    with pytest.raises(FileNotFoundError, match="explicit torchaudio"):
        stages.alignment_model_cache(align_job(language="en", model="WAV2VEC2_OTHER"))
    assert stages.alignment_model_cache(
        align_job(language="en", model="organization/frozen-aligner")
    ) == (tmp_path / "hub")


def test_old_v1_result_and_plan_payloads_still_validate_without_v2_fields() -> None:
    old_plan = {
        "backend": "modal-checkpointed",
        "app": "temnia-speech",
        "environment": None,
        "protocol": "temnia-speech/1",
        "build": "old-build",
        "budgetMicros": 6_500_000,
        "rateMicrosPerHour": 1_250_000,
        "stageTimeoutSeconds": 3600,
        "startupTimeoutSeconds": 120,
        "dispatchLimit": 5,
        "audioSha256": "a" * 64,
        "audioSizeBytes": 123,
        "detector": "silero",
        "detectorRevision": "revision",
        "detectorSha256": "b" * 64,
    }
    parsed_plan = TranscriptionPlan.model_validate(old_plan)
    assert parsed_plan.protocol == "temnia-speech/1"
    assert parsed_plan.resource_profile is None
    assert parsed_plan.model_manifest is None
    assert parsed_plan.execution_topology is None
    assert parsed_plan.recognize_v2 is None
    assert parsed_plan.speaker_turns is None
    assert parsed_plan.allow_oom_recovery is None

    old_telemetry = {
        "elapsedSeconds": 1.5,
        "progress": {"percent": 80, "source": "whisperx_callback"},
        "sampleIntervalSeconds": 1,
        "sampleCount": 2,
        "complete": True,
    }
    parsed = StageTelemetry.model_validate(old_telemetry)
    assert parsed.progress.percent == 80
    assert parsed.progress_transport is None
    assert parsed.process_cpu_seconds is None
    assert parsed.gpu_utilization_peak_percent is None
    assert asdict(diagnostics())["last_error"] is None
