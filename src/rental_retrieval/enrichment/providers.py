"""Vision provider interface, a deterministic stub, and a live adapter skeleton.

The interface is one method. Everything a provider needs (the prompt, the
vocabularies, the response shape) comes from `contract.py`, so a provider holds
only transport concerns: authentication, wire format, retries, cost accounting.

WHY THE STUB IS DETERMINISTIC
-----------------------------
`StubVisionProvider` seeds a PRNG from a hash of the image reference, so the
same input always produces the same output, forever, on any machine. That makes
the whole enrichment pipeline testable and lets the ranking phase be developed
and regression-tested with zero API spend.

It is a fixture, not a simulation. It has no idea what is in the image. Nothing
it produces is evidence about a real dress, and `EnrichedListing.provider`
records "stub" on every record it writes so synthetic data can never be mistaken
for measured data downstream.

PUBLIC-SAFE. No credentials; the live adapter reads its key from the environment.
"""

from __future__ import annotations

import hashlib
import os
import random
import time
from typing import Any, Protocol, runtime_checkable

from rental_retrieval.enrichment.contract import (
    FIELD_VOCABULARIES,
    PROMPT_VERSION,
    build_messages,
)
from rental_retrieval.schemas.visual import VIBE_KEYWORDS


class ProviderError(RuntimeError):
    """Transport-level failure. Normalization problems are not errors."""


@runtime_checkable
class VisionProvider(Protocol):
    """Anything that can turn an image reference into a raw attribute dict."""

    name: str
    model_version: str

    def extract(self, image_ref: str) -> dict[str, Any]:
        """Return the model's raw JSON response as a dict.

        Implementations must not normalize, coerce, or validate. Raw in, raw
        out; `normalize.py` is the single trust boundary.
        """
        ...


# --- Stub --------------------------------------------------------------------

class StubVisionProvider:
    """Deterministic synthetic provider. Same input, same output, always.

    `realism` shapes how hard the output is to parse, so the normalizer can be
    exercised against the failure modes real models actually produce:

      "clean"  every field populated, correct vocabulary
      "messy"  aliased spellings, percentage confidences, bare scalars,
               occasional nulls. The default, because this is what real
               responses look like.
      "hostile" adds out-of-vocabulary values, missing keys and prose-wrapped
               JSON. Used to prove the pipeline degrades rather than crashes.
    """

    name = "stub"
    model_version = "stub-v1"

    def __init__(self, realism: str = "messy", null_rate: float = 0.15) -> None:
        if realism not in {"clean", "messy", "hostile"}:
            raise ValueError("realism must be clean, messy or hostile")
        self.realism = realism
        self.null_rate = null_rate

    def _rng(self, image_ref: str) -> random.Random:
        seed = int(hashlib.sha256(f"{image_ref}|{PROMPT_VERSION}".encode()).hexdigest()[:16], 16)
        return random.Random(seed)

    def extract(self, image_ref: str) -> dict[str, Any]:
        rng = self._rng(image_ref)
        out: dict[str, Any] = {}

        for field, vocabulary in FIELD_VOCABULARIES.items():
            if rng.random() < self.null_rate:
                out[field] = {"value": None, "confidence": 0.0, "evidence": None}
                continue

            value = rng.choice(vocabulary)
            confidence = round(rng.uniform(0.55, 0.97), 2)

            if self.realism == "clean":
                out[field] = {"value": value, "confidence": confidence,
                              "evidence": f"visible {value.replace('_', ' ')}"}
                continue

            roll = rng.random()
            if roll < 0.12:                       # bare scalar, wrapper dropped
                out[field] = value
            elif roll < 0.24:                     # percentage confidence
                out[field] = {"value": value, "confidence": round(confidence * 100),
                              "evidence": f"visible {value.replace('_', ' ')}"}
            elif roll < 0.34:                     # aliased spelling
                out[field] = {"value": value.replace("_", " ").title(),
                              "confidence": confidence, "evidence": None}
            elif self.realism == "hostile" and roll < 0.44:
                out[field] = {"value": f"unlisted_{field}_value",
                              "confidence": confidence, "evidence": None}
            else:
                out[field] = {"value": value, "confidence": confidence,
                              "evidence": f"visible {value.replace('_', ' ')}"}

        out["vibe_keywords"] = rng.sample(VIBE_KEYWORDS, k=rng.randint(0, 3))
        out["image_quality"] = rng.choices(
            ["clear", "acceptable", "poor"], weights=[5, 4, 1]
        )[0]
        out["notes"] = None
        out["uncertainty_notes"] = (
            ["hem cropped out of frame"] if rng.random() < 0.2 else []
        )

        if self.realism == "hostile":
            if rng.random() < 0.25:
                out.pop("texture", None)          # missing key
            if rng.random() < 0.2:
                out["image_quality"] = "blurry"   # invalid enum

        return out


class FailingVisionProvider:
    """Always raises. Used to prove batch isolation: one failure must not stop a run."""

    name = "failing"
    model_version = "failing-v1"

    def __init__(self, message: str = "simulated provider outage") -> None:
        self.message = message

    def extract(self, image_ref: str) -> dict[str, Any]:
        raise ProviderError(f"{self.message} for {image_ref}")


# --- Live adapter skeleton ---------------------------------------------------

class LiveVisionProvider:
    """Generic live-provider skeleton for a vendor other than the implemented one.

    The working live provider is `live_provider.AnthropicVisionProvider`. This
    class stays as the template for adding a second vendor: implement
    `_call_api` and nothing else changes, because the prompt, the vocabulary and
    the response shape all come from `contract.py`.

    It raises rather than silently degrading when no key is configured, because a
    pipeline that quietly produced synthetic data for a real run would be worse
    than one that stopped.
    """

    name = "live"

    def __init__(self, model_version: str, api_key_env: str = "MODEL_PROVIDER_API_KEY",
                 max_retries: int = 3, timeout_s: float = 60.0) -> None:
        self.model_version = model_version
        self.api_key_env = api_key_env
        self.max_retries = max_retries
        self.timeout_s = timeout_s

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def extract(self, image_ref: str) -> dict[str, Any]:
        if not self.is_configured():
            raise ProviderError(
                f"{self.api_key_env} is not set. Configure it in .env, or run with "
                "--provider stub for synthetic data."
            )

        messages = build_messages(image_ref)
        last: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                return self._call_api(messages)
            except Exception as exc:                      # noqa: BLE001
                last = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)              # exponential backoff
        raise ProviderError(f"failed after {self.max_retries} attempts: {last}")

    def _call_api(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Implement per vendor.

        The work is: turn `messages` into the vendor's wire format (the image
        usually needs base64 or a file upload), request JSON output, and return
        the parsed object. `normalize.extract_json_block` handles a response
        wrapped in prose, so returning the raw text body is acceptable too.
        """
        raise NotImplementedError(
            "LiveVisionProvider._call_api is a deliberate stub. "
            "See docs/adr/0006-image-enrichment.md for the integration checklist."
        )


def get_provider(name: str, **kwargs: Any) -> VisionProvider:
    """Factory. Keeps provider selection in one place for the CLI and tests.

    'anthropic' and 'live' both resolve to the implemented live provider;
    'live' is kept as an alias so existing commands and docs stay valid.
    """
    if name == "stub":
        return StubVisionProvider(**kwargs)
    if name == "failing":
        return FailingVisionProvider(**kwargs)
    if name in ("live", "anthropic"):
        # Imported lazily: the live provider reads environment configuration at
        # construction, and nothing that only uses stubs should pay for that.
        from rental_retrieval.enrichment.live_provider import AnthropicVisionProvider
        model_version = kwargs.pop("model_version", None)
        return AnthropicVisionProvider(model_version=model_version, **kwargs)
    if name == "skeleton":
        return LiveVisionProvider(model_version=kwargs.pop("model_version", "unset"), **kwargs)
    raise ValueError(
        f"unknown provider {name!r}; expected stub, failing, live/anthropic or skeleton"
    )
