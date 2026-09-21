"""Live vision provider: a real API call behind the existing provider interface.

MODEL CHOICE
------------
Default: **`claude-sonnet-5`**, overridable with `VISION_MODEL`.

Three properties decided it, in order of weight for this task:

1. **Forced structured output.** The call uses tool-use with a JSON schema and
   `tool_choice` pinned to that tool, so the model must return an object of the
   right shape rather than being asked politely for JSON. That removes the most
   common extraction failure outright. The normalizer still runs, because a
   well-shaped object can still contain "V-neck" where the vocabulary says
   "v_neck", and because a schema cannot stop a model inventing a hem it cannot
   see.

2. **Cost at catalog scale.** This is an offline batch over 157 images now and
   potentially thousands later, with no latency requirement. Sonnet is the
   balanced tier; paying frontier prices per image for a fifteen-field
   extraction is not obviously worth it, and the seed set exists to test that
   assumption rather than assume it. Run the 8 seed images through
   `VISION_MODEL=claude-opus-5` and compare agreement: if the stronger model
   does not measurably win on necklines and silhouettes, the cheaper one is the
   correct engineering answer.

3. **Fine-grained visual discrimination.** The hard calls here are square versus
   sweetheart necklines and a-line versus fit-and-flare, not object recognition.

Nothing about this module is Anthropic-specific above the wire format.
`_call_api` is the only vendor-aware method; swapping providers means writing
one more of these.

IMPLEMENTATION NOTES
--------------------
No new dependency: the call goes over `urllib.request` from the standard
library. The wire format stays visible rather than hidden behind an SDK, which
matters for a repo whose purpose is demonstrating that its author understands
what it does.

Credentials come from the environment and are never logged, never written to
the cache, and never recorded in an `EnrichedListing`.

PUBLIC-SAFE. No credentials, no data.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from rental_retrieval.enrichment.contract import (
    FIELD_VOCABULARIES,
    SYSTEM_PROMPT,
    build_user_prompt,
    contract_fingerprint,
)
from rental_retrieval.enrichment.providers import ProviderError
from rental_retrieval.schemas.visual import VIBE_KEYWORDS

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
EXTRACTION_TOOL_NAME = "record_dress_attributes"

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ImageSourceError(ProviderError):
    """The image reference cannot be turned into bytes the API will accept."""


# --- Image resolution --------------------------------------------------------

def resolve_image_source(image_ref: str) -> tuple[str, str]:
    """Turn an image reference into (base64_data, media_type).

    Accepts a local file path or a `data:` URI.

    **Rejects Instagram permalinks on purpose.** A permalink is an HTML page,
    not an image, and fetching the underlying media would mean scraping, which
    this project does not do (ADR 001, and the user's standing constraint).
    Images must be saved locally by hand first. The error says so rather than
    failing with an opaque decode error.
    """
    if not image_ref:
        raise ImageSourceError("empty image reference")

    if image_ref.startswith("data:"):
        try:
            header, payload = image_ref.split(",", 1)
            media_type = header.split(";")[0].removeprefix("data:") or "image/jpeg"
            return payload, media_type
        except ValueError as exc:
            raise ImageSourceError(f"malformed data URI: {exc}") from exc

    lowered = image_ref.lower()
    if lowered.startswith(("http://", "https://")):
        if "instagram.com" in lowered or "cdninstagram" in lowered or "fbcdn" in lowered:
            raise ImageSourceError(
                f"{image_ref} is an Instagram URL. This project does not fetch media "
                "from Instagram. Save the image yourself to "
                "data/local/seed_images/<shortcode>.jpg (gitignored) and re-run with "
                "--image-root data/local/seed_images."
            )
        raise ImageSourceError(
            f"{image_ref} is a remote URL. Only local files and data URIs are "
            "supported, so that nothing is downloaded implicitly. Save the image "
            "locally and pass --image-root."
        )

    path = Path(image_ref)
    if not path.exists():
        raise ImageSourceError(
            f"no image at {path}. Expected a local file named <shortcode>.jpg under "
            "the directory given by --image-root."
        )

    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ImageSourceError(
            f"{path} is {size / 1e6:.1f}MB, above the {MAX_IMAGE_BYTES / 1e6:.0f}MB limit. "
            "Downscale it before enriching."
        )

    media_type, _ = mimetypes.guess_type(str(path))
    if media_type not in SUPPORTED_IMAGE_TYPES:
        raise ImageSourceError(
            f"{path} has media type {media_type or 'unknown'}; expected one of "
            f"{', '.join(sorted(SUPPORTED_IMAGE_TYPES))}."
        )

    return base64.standard_b64encode(path.read_bytes()).decode("ascii"), media_type


# --- Tool schema -------------------------------------------------------------

def build_extraction_tool() -> dict[str, Any]:
    """JSON schema forcing the response shape.

    Built from `FIELD_VOCABULARIES`, the same source the prompt text uses, so
    the schema and the prompt cannot drift apart. Every field is nullable
    because null is a correct answer, and the enum constrains the value without
    forcing one to exist.
    """
    properties: dict[str, Any] = {}

    for field, vocabulary in FIELD_VOCABULARIES.items():
        properties[field] = {
            "type": "object",
            "properties": {
                "value": {
                    "type": ["string", "null"],
                    "enum": [*vocabulary, None],
                    "description": f"One of the allowed values for {field}, or null if not visible",
                },
                "confidence": {
                    "type": "number", "minimum": 0.0, "maximum": 1.0,
                    "description": "How clearly the image supports this answer",
                },
                "evidence": {
                    "type": ["string", "null"],
                    "description": "Short clause naming what was visible",
                },
            },
            "required": ["value", "confidence"],
        }

    properties["vibe_keywords"] = {
        "type": "array",
        "items": {"type": "string", "enum": VIBE_KEYWORDS},
        "maxItems": 4,
    }
    properties["image_quality"] = {
        "type": "string", "enum": ["clear", "acceptable", "poor", "unusable"],
    }
    properties["notes"] = {"type": ["string", "null"]}
    properties["uncertainty_notes"] = {"type": "array", "items": {"type": "string"}}

    return {
        "name": EXTRACTION_TOOL_NAME,
        "description": (
            "Record the visible attributes of one dress. Use null for anything the "
            "photograph does not show. Do not infer one attribute from another."
        ),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": ["image_quality"],
        },
    }


# --- Provider ----------------------------------------------------------------

class AnthropicVisionProvider:
    """Live vision extraction via the Anthropic Messages API.

    Implements the same `VisionProvider` protocol as the stub, so the
    orchestrator, cache, normalizer and merge layer are all unchanged.
    """

    name = "anthropic"

    def __init__(
        self,
        model_version: str | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        api_base: str | None = None,
        max_retries: int = 3,
        timeout_s: float = 90.0,
        max_tokens: int = 1500,
    ) -> None:
        self.model_version = model_version or os.environ.get("VISION_MODEL", DEFAULT_MODEL)
        self.api_key_env = api_key_env
        self.api_base = (api_base or os.environ.get("ANTHROPIC_API_BASE")
                         or DEFAULT_API_BASE).rstrip("/")
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

        # Provenance recorded alongside every extraction.
        self.contract_fingerprint = contract_fingerprint()
        self.last_usage: dict[str, int] | None = None

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def is_configured(self) -> bool:
        return bool(self.api_key)

    # -- public entry point ---------------------------------------------------

    def extract(self, image_ref: str) -> dict[str, Any]:
        """Return the model's raw attribute object.

        Raw in, raw out: no normalization happens here. `normalize.py` remains
        the single trust boundary, so malformed or partial output degrades
        through the path that is already tested against it.
        """
        if not self.is_configured():
            raise ProviderError(
                f"{self.api_key_env} is not set. Export it in your shell, or run with "
                "--provider stub for deterministic synthetic data at no cost."
            )

        image_data, media_type = resolve_image_source(image_ref)
        payload = self._build_payload(image_data, media_type)

        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._post(payload)
                return self._extract_tool_input(response)
            except ImageSourceError:
                raise                                   # never retry a bad image
            except ProviderError as exc:
                last = exc
                if not self._is_retryable(exc) or attempt == self.max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
            except Exception as exc:                    # noqa: BLE001
                last = exc
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2 ** attempt)

        raise ProviderError(f"failed after {self.max_retries} attempts: {last}")

    # -- wire format ----------------------------------------------------------

    def _build_payload(self, image_data: str, media_type: str) -> dict[str, Any]:
        return {
            "model": self.model_version,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "tools": [build_extraction_tool()],
            "tool_choice": {"type": "tool", "name": EXTRACTION_TOOL_NAME},
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": image_data,
                    }},
                    {"type": "text", "text": build_user_prompt()},
                ],
            }],
        }

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.api_base}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key or "",
                "anthropic-version": ANTHROPIC_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            # The key is never echoed: only status and the server's message.
            raise ProviderError(f"HTTP {exc.code} from vision API: {body}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"network error calling vision API: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"vision API returned non-JSON: {exc}") from exc

    def _extract_tool_input(self, response: dict[str, Any]) -> dict[str, Any]:
        """Pull the tool input out of the response.

        Falls back to any text block if the model somehow answered in prose;
        `normalize.extract_json_block` can recover JSON from that, and a total
        failure returns an empty dict, which normalizes to an empty-but-valid
        record rather than crashing the batch.
        """
        usage = response.get("usage") or {}
        self.last_usage = {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        }

        for block in response.get("content", []) or []:
            if block.get("type") == "tool_use" and block.get("name") == EXTRACTION_TOOL_NAME:
                tool_input = block.get("input")
                if isinstance(tool_input, dict):
                    return tool_input

        for block in response.get("content", []) or []:
            if block.get("type") == "text" and block.get("text"):
                from rental_retrieval.enrichment.normalize import extract_json_block
                recovered = extract_json_block(block["text"])
                if recovered is not None:
                    return recovered

        return {}

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        text = str(error)
        return any(token in text for token in
                   ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 529",
                    "network error", "timed out"))

    # -- provenance -----------------------------------------------------------

    def provenance(self) -> dict[str, Any]:
        """Everything needed to reproduce or audit an extraction.

        Deliberately excludes the API key, and includes the contract
        fingerprint so a cached record can be tied to the exact prompt and
        vocabulary that produced it.
        """
        return {
            "provider": self.name,
            "model_version": self.model_version,
            "contract_fingerprint": self.contract_fingerprint,
            "api_base": self.api_base,
            "structured_output": f"tool_use:{EXTRACTION_TOOL_NAME}",
            "last_usage": self.last_usage,
        }
