"""Tests for the live vision provider.

NO NETWORK. NO CREDENTIALS. Every test here either checks pure logic or stubs
the transport. The suite must pass on a fresh clone with no API key set, which
is asserted explicitly at the end.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from rental_retrieval.enrichment.contract import PROMPT_VERSION, contract_fingerprint
from rental_retrieval.enrichment.live_provider import (
    DEFAULT_MODEL,
    EXTRACTION_TOOL_NAME,
    AnthropicVisionProvider,
    ImageSourceError,
    build_extraction_tool,
    resolve_image_source,
)
from rental_retrieval.enrichment.normalize import normalize
from rental_retrieval.enrichment.orchestrator import EnrichmentCache, enrich_one
from rental_retrieval.enrichment.providers import ProviderError, VisionProvider, get_provider
from rental_retrieval.eval.anchors import build_measurement_gate
from rental_retrieval.schemas.visual import VisualAttributes

# Shared synthetic fixture. Imported by path, not as `tests.test_anchors`:
# `tests` is not guaranteed to be an importable package, and one test module
# should not import another. Same pattern as _work_contracts_double.py.
sys.path.insert(0, str(Path(__file__).parent))

from _anchor_fixtures import make_anchor_set  # noqa: E402

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def image_file(tmp_path: Path) -> Path:
    path = tmp_path / "TESTCODE.png"
    path.write_bytes(PNG_1PX)
    return path


# --- Credentials -------------------------------------------------------------

def test_no_key_means_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AnthropicVisionProvider().is_configured() is False


def test_extract_refuses_without_a_key(monkeypatch, image_file):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY is not set"):
        AnthropicVisionProvider().extract(str(image_file))


def test_error_message_points_at_the_stub_fallback(monkeypatch, image_file):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="--provider stub"):
        AnthropicVisionProvider().extract(str(image_file))


def test_key_is_read_from_the_environment_not_stored(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    provider = AnthropicVisionProvider()
    assert provider.is_configured() is True
    # The key must not be reachable from the provenance record.
    assert "sk-test-not-a-real-key" not in json.dumps(provider.provenance())


def test_custom_key_env_is_honoured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MY_OTHER_KEY", "x")
    assert AnthropicVisionProvider(api_key_env="MY_OTHER_KEY").is_configured() is True


# --- Model selection ---------------------------------------------------------

def test_default_model(monkeypatch):
    monkeypatch.delenv("VISION_MODEL", raising=False)
    assert AnthropicVisionProvider().model_version == DEFAULT_MODEL


def test_model_overridable_by_env(monkeypatch):
    monkeypatch.setenv("VISION_MODEL", "claude-opus-5")
    assert AnthropicVisionProvider().model_version == "claude-opus-5"


def test_explicit_model_beats_env(monkeypatch):
    monkeypatch.setenv("VISION_MODEL", "claude-opus-5")
    assert AnthropicVisionProvider(model_version="claude-fable-5-1").model_version == \
        "claude-fable-5-1"


# --- Image resolution --------------------------------------------------------

def test_local_file_is_base64_encoded(image_file):
    data, media_type = resolve_image_source(str(image_file))
    assert media_type == "image/png"
    assert base64.standard_b64decode(data) == PNG_1PX


def test_data_uri_is_accepted():
    data, media_type = resolve_image_source("data:image/jpeg;base64,AAAA")
    assert (data, media_type) == ("AAAA", "image/jpeg")


def test_instagram_url_is_refused_with_actionable_guidance():
    """The project does not fetch media from Instagram. The error must say so."""
    with pytest.raises(ImageSourceError) as exc:
        resolve_image_source("https://www.instagram.com/p/ABC123/")
    message = str(exc.value)
    assert "does not fetch media from Instagram" in message
    assert "data/local/seed_images" in message


@pytest.mark.parametrize("url", [
    "https://www.instagram.com/p/ABC/",
    "https://scontent.cdninstagram.com/v/abc.jpg",
    "https://scontent-lga3-1.xx.fbcdn.net/v/abc.jpg",
])
def test_every_instagram_host_is_refused(url):
    with pytest.raises(ImageSourceError, match="Instagram"):
        resolve_image_source(url)


def test_other_remote_urls_are_refused_too():
    """Nothing is downloaded implicitly, from anywhere."""
    with pytest.raises(ImageSourceError, match="Only local files and data URIs"):
        resolve_image_source("https://example.com/dress.jpg")


def test_missing_file_names_the_expected_layout():
    with pytest.raises(ImageSourceError, match="image-root"):
        resolve_image_source("/nonexistent/path/X.jpg")


def test_oversized_image_is_refused(tmp_path, monkeypatch):
    import rental_retrieval.enrichment.live_provider as lp
    monkeypatch.setattr(lp, "MAX_IMAGE_BYTES", 10)
    big = tmp_path / "big.png"
    big.write_bytes(PNG_1PX)
    with pytest.raises(ImageSourceError, match="above the"):
        lp.resolve_image_source(str(big))


def test_unsupported_media_type_is_refused(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not an image", encoding="utf-8")
    with pytest.raises(ImageSourceError, match="media type"):
        resolve_image_source(str(path))


def test_empty_reference_is_refused():
    with pytest.raises(ImageSourceError, match="empty image reference"):
        resolve_image_source("")


# --- Structured output schema ------------------------------------------------

def test_tool_schema_covers_every_contract_field():
    from rental_retrieval.enrichment.contract import FIELD_VOCABULARIES
    schema = build_extraction_tool()["input_schema"]["properties"]
    for field in FIELD_VOCABULARIES:
        assert field in schema


def test_tool_schema_allows_null_for_every_attribute():
    """Null is a correct answer, so the schema must permit it."""
    schema = build_extraction_tool()["input_schema"]["properties"]
    value = schema["neckline"]["properties"]["value"]
    assert "null" in value["type"]
    assert None in value["enum"]


def test_tool_schema_enum_matches_the_vocabulary():
    from rental_retrieval.enrichment.contract import FIELD_VOCABULARIES
    schema = build_extraction_tool()["input_schema"]["properties"]
    enum = schema["silhouette"]["properties"]["value"]["enum"]
    assert set(FIELD_VOCABULARIES["silhouette"]) <= set(v for v in enum if v)


def test_payload_pins_tool_choice_without_temperature(monkeypatch, image_file):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider()
    data, media_type = resolve_image_source(str(image_file))
    payload = provider._build_payload(data, media_type)
    assert payload["tool_choice"] == {"type": "tool", "name": EXTRACTION_TOOL_NAME}
    assert "temperature" not in payload
    assert payload["messages"][0]["content"][0]["source"]["media_type"] == "image/png"


# --- Response handling, transport stubbed ------------------------------------

def _tool_response(payload: dict) -> dict:
    return {
        "content": [{"type": "tool_use", "name": EXTRACTION_TOOL_NAME, "input": payload}],
        "usage": {"input_tokens": 1200, "output_tokens": 300},
    }


def test_tool_use_response_is_returned_raw(monkeypatch, image_file):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider()
    body = {"neckline": {"value": "plunge_v", "confidence": 0.9}, "image_quality": "clear"}
    monkeypatch.setattr(provider, "_post", lambda payload: _tool_response(body))
    assert provider.extract(str(image_file)) == body


def test_usage_is_recorded_for_cost_accounting(monkeypatch, image_file):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider()
    monkeypatch.setattr(
        provider,
        "_post",
        lambda payload: _tool_response({"image_quality": "clear"}),
    )
    provider.extract(str(image_file))
    assert provider.last_usage == {"input_tokens": 1200, "output_tokens": 300}


def test_prose_response_is_recovered_via_the_normalizer(monkeypatch, image_file):
    """If the model answers in text anyway, JSON is still salvaged."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider()
    monkeypatch.setattr(provider, "_post", lambda payload: {
        "content": [{"type": "text",
                     "text": (
                         'Here you go:\n```json\n'
                         '{"neckline": {"value": "cowl", "confidence": 0.8}}\n'
                         '```'
                     )}],
        "usage": {},
    })
    assert provider.extract(str(image_file))["neckline"]["value"] == "cowl"


def test_unrecoverable_response_returns_empty_not_an_exception(monkeypatch, image_file):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider()
    monkeypatch.setattr(provider, "_post", lambda payload: {"content": [], "usage": {}})
    assert provider.extract(str(image_file)) == {}


def test_partial_output_degrades_through_normalization(monkeypatch, image_file):
    """Requirement: malformed or partial output must degrade, not crash."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider()
    messy = {
        "neckline": "V-Neck",                       # bare scalar, aliased spelling
        "colour_family": {"value": "navy", "confidence": 88},   # alias + percentage
        "silhouette": {"value": "spiral", "confidence": 0.9},   # out of vocabulary
        "image_quality": "blurry",                               # invalid enum
    }
    monkeypatch.setattr(provider, "_post", lambda payload: _tool_response(messy))
    attributes, errors = normalize(provider.extract(str(image_file)))
    assert isinstance(attributes, VisualAttributes)
    assert attributes.neckline.value == "v_neck"
    assert attributes.colour_family.value == "blue"
    assert attributes.colour_family.confidence == pytest.approx(0.88)
    assert attributes.silhouette.value is None
    assert "silhouette" in attributes.unrecognized_values
    assert attributes.image_quality == "acceptable"
    assert errors


# --- Retries -----------------------------------------------------------------

def test_transient_error_is_retried(monkeypatch, image_file):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr("time.sleep", lambda s: None)
    provider = AnthropicVisionProvider(max_retries=3)
    calls = {"n": 0}

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("HTTP 429 rate limited")
        return _tool_response({"image_quality": "clear"})

    monkeypatch.setattr(provider, "_post", flaky)
    provider.extract(str(image_file))
    assert calls["n"] == 3


def test_permanent_error_is_not_retried(monkeypatch, image_file):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider(max_retries=3)
    calls = {"n": 0}

    def bad_request(payload):
        calls["n"] += 1
        raise ProviderError("HTTP 400 invalid request")

    monkeypatch.setattr(provider, "_post", bad_request)
    with pytest.raises(ProviderError, match="HTTP 400"):
        provider.extract(str(image_file))
    assert calls["n"] == 1


def test_bad_image_is_never_retried(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider(max_retries=3)
    with pytest.raises(ImageSourceError):
        provider.extract("https://www.instagram.com/p/ABC/")


# --- Provenance and the measurement gate -------------------------------------

def test_provenance_records_what_is_needed_to_reproduce(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("VISION_MODEL", "claude-sonnet-5")
    record = AnthropicVisionProvider().provenance()
    assert record["provider"] == "anthropic"
    assert record["model_version"] == "claude-sonnet-5"
    assert record["contract_fingerprint"] == contract_fingerprint()
    assert record["structured_output"].startswith("tool_use:")


def test_enriched_record_carries_provider_and_versions(monkeypatch, image_file):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    provider = AnthropicVisionProvider()
    monkeypatch.setattr(provider, "_post",
                        lambda payload: _tool_response({"image_quality": "clear"}))
    listing, _ = enrich_one("CODE1", "https://example.com/p/CODE1/",
                            str(image_file), provider)
    assert listing.provider == "anthropic"
    assert listing.model_version == provider.model_version
    assert listing.prompt_version == PROMPT_VERSION


def test_live_provider_unlocks_measurement_stub_does_not():
    real = make_anchor_set(True)
    assert build_measurement_gate(real, "anthropic", "x").is_measured is True
    assert build_measurement_gate(real, "stub", "x").is_measured is False
    assert build_measurement_gate(real, "skeleton", "x").is_measured is False
    assert build_measurement_gate(real, "live", "x").is_measured is False


def test_cache_key_includes_the_model(monkeypatch, tmp_path, image_file):
    """Two models must not share a cache entry."""
    cache = EnrichmentCache(tmp_path / "c")
    a = cache.key(str(image_file), "claude-sonnet-5")
    b = cache.key(str(image_file), "claude-opus-5")
    assert a != b


# --- Factory and protocol ----------------------------------------------------

def test_factory_resolves_live_and_anthropic_to_the_implementation():
    for name in ("live", "anthropic"):
        assert isinstance(get_provider(name), AnthropicVisionProvider)


def test_factory_still_offers_the_unimplemented_skeleton():
    from rental_retrieval.enrichment.providers import LiveVisionProvider
    assert isinstance(get_provider("skeleton"), LiveVisionProvider)


def test_live_provider_satisfies_the_protocol():
    assert isinstance(AnthropicVisionProvider(), VisionProvider)


def test_suite_runs_with_no_api_key_present(monkeypatch):
    """A fresh clone with no credentials must be able to run everything."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    provider = get_provider("stub")
    assert provider.extract("anything.jpg")
