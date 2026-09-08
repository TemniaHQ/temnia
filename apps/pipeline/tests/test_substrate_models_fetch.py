"""The image's model list is the code's model list.

`scripts/fetch_models.py` imports nothing from the package, so that the layer
that downloads a gigabyte of weights depends on the lock file alone and editing
Python does not re-download them. That costs three duplicated constants. These
assertions are what stops them drifting: a model renamed in the code and not in
the script would otherwise ship an image that downloads at run time on staging,
which is exactly what baking them in is meant to prevent.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from temnia_pipeline.settings import DEFAULT_MODELS_DIR
from temnia_pipeline.substrate import backends
from temnia_pipeline.substrate.changepoint import DEFAULT_EMBEDDING_MODEL
from temnia_pipeline.substrate.sat import DEFAULT_SAT_MODEL

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_models.py"


def _script() -> ModuleType:
    """Import the script by path; it is not on the package's import path."""
    spec = importlib.util.spec_from_file_location("fetch_models", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_fetch_script_names_the_models_the_code_loads() -> None:
    script = _script()
    assert script.SAT_MODEL == DEFAULT_SAT_MODEL
    assert script.EMBEDDING_MODEL == DEFAULT_EMBEDDING_MODEL
    assert script.DEFAULT_MODELS_DIR == DEFAULT_MODELS_DIR
    assert script.PINNED_REVISIONS == backends.PINNED_REVISIONS
    assert script.DEFAULT_SAT_TOKENIZER == backends.DEFAULT_SAT_TOKENIZER


def test_the_dockerfile_bakes_the_models_in_before_the_source() -> None:
    """The layering is the point; a COPY of src above it would undo it."""
    dockerfile = (SCRIPT.parents[1] / "Dockerfile").read_text()
    fetch = dockerfile.index("scripts/fetch_models.py")
    assert dockerfile.index("COPY src ./src") > fetch
    assert "TEMNIA_MODELS_DIR" in dockerfile


def _snapshots(root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for repo, revision in backends.PINNED_REVISIONS.items():
        path = root / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots" / revision
        path.mkdir(parents=True)
        paths[repo] = path
    return paths


def test_setup_and_runtime_use_the_same_effective_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared, ignored = tmp_path / "shared", tmp_path / "ignored"
    monkeypatch.setenv("HF_HOME", str(shared))
    monkeypatch.setenv("TEMNIA_MODELS_DIR", str(ignored))
    assert _script().configure_model_cache() == shared
    assert backends.configure_model_cache(ignored) == shared
    assert not ignored.exists()


def test_fetch_uses_explicit_revisions_and_checks_the_returned_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _script()
    paths = _snapshots(tmp_path)
    requests: list[tuple[str, str, Path]] = []

    def download(*, repo_id: str, revision: str, cache_dir: Path, allow_patterns: list[str]) -> str:
        requests.append((repo_id, revision, cache_dir))
        path = paths[repo_id]
        for name in allow_patterns:
            file = path / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture")
        return str(path)

    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    assert script.fetch_snapshots(tmp_path) == paths
    assert len(requests) == len(backends.PINNED_REVISIONS)
    for repo, revision, cache in requests:
        assert revision == backends.PINNED_REVISIONS[repo]
        assert cache == tmp_path / "hub"

    def wrong_snapshot(**kwargs: object) -> str:
        _ = kwargs
        return str(tmp_path / "wrong")

    monkeypatch.setattr("huggingface_hub.snapshot_download", wrong_snapshot)
    with pytest.raises(RuntimeError, match="expected the pinned snapshot"):
        script.fetch_snapshots(tmp_path)


def test_an_incomplete_snapshot_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _snapshots(tmp_path)

    def incomplete_snapshot(*, repo_id: str, **kwargs: object) -> str:
        _ = kwargs
        return str(paths[repo_id])

    monkeypatch.setattr("huggingface_hub.snapshot_download", incomplete_snapshot)
    with pytest.raises(RuntimeError, match="incomplete: missing"):
        _script().fetch_snapshots(tmp_path)


def test_mutable_or_absent_models_fail_before_any_loader(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="immutable revision"):
        backends.model_snapshot("org/challenger", tmp_path)
    with pytest.raises(ValueError, match="immutable revision"):
        backends.model_snapshot("org/challenger@main", tmp_path)
    with pytest.raises(FileNotFoundError, match="prefetch"):
        backends.model_snapshot("org/challenger@" + "a" * 40, tmp_path)


def test_runtime_loads_local_weights_tokenizer_and_adapter_and_captures_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.setattr(backends, "prime_skops", lambda: None)
    paths = _snapshots(tmp_path)
    calls: list[tuple[str, dict[str, object]]] = []
    sentinel = object()

    def constructor(path: str, **kwargs: object) -> object:
        calls.append((path, kwargs))
        return sentinel

    for module_name, class_name in (
        ("wtpsplit", "SaT"),
        ("sentence_transformers", "SentenceTransformer"),
    ):
        module = ModuleType(module_name)
        setattr(module, class_name, constructor)
        monkeypatch.setitem(sys.modules, module_name, module)
    sat_path = paths["segment-any-text/sat-3l-sm"]
    adapter = sat_path / "loras" / "ted" / "en"
    adapter.mkdir(parents=True)
    sat = backends.load_sat("sat-3l-sm", style_or_domain="ted", language="en")
    encoder = backends.load_encoder(DEFAULT_EMBEDDING_MODEL)
    assert sat.value is sentinel
    assert encoder.value is sentinel
    assert calls[0] == (
        str(sat_path),
        {
            "tokenizer_name_or_path": str(paths[backends.DEFAULT_SAT_TOKENIZER]),
            "from_pretrained_kwargs": {"local_files_only": True},
            "style_or_domain": "ted",
            "language": "en",
            "lora_path": str(adapter),
        },
    )
    assert calls[1] == (
        str(paths[DEFAULT_EMBEDDING_MODEL]),
        {"local_files_only": True, "device": "cpu"},
    )
    assert sat.revision == sat_path.name
    assert sat.tokenizer_revision == paths[backends.DEFAULT_SAT_TOKENIZER].name
    assert encoder.revision == paths[DEFAULT_EMBEDDING_MODEL].name
    # A different process moving a cache ref cannot rewrite an instance's identity.
    for snapshot in paths.values():
        refs = snapshot.parent.parent / "refs"
        refs.mkdir()
        (refs / "main").write_text("f" * 40)
    assert sat.revision == sat_path.name
    assert encoder.revision == paths[DEFAULT_EMBEDDING_MODEL].name
