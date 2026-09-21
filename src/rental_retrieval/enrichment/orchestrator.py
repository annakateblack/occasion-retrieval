"""Enrichment orchestration: cache, batch, isolate failures, report coverage.

Responsibilities, in the order they matter:

1. **Never pay twice.** Extractions are cached on disk, keyed by the contract
   fingerprint and the model version. A re-run costs nothing; a prompt change
   correctly invalidates everything, because the old answers came from a
   different question.

2. **One failure must not sink a batch.** Every item is isolated. A provider
   outage on item 3 leaves items 1, 2 and 4 intact and recorded as errors.

3. **Report coverage, not success.** "8 of 8 succeeded" is close to meaningless
   if the model returned nulls throughout. `field_coverage` says which
   attributes were actually obtained, which is the number that decides whether
   enrichment is worth its cost.

PUBLIC-SAFE. The cache directory is supplied by the caller and lives under
data/local/, which is gitignored.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rental_retrieval.enrichment.contract import contract_fingerprint
from rental_retrieval.enrichment.normalize import normalize
from rental_retrieval.enrichment.providers import ProviderError, VisionProvider
from rental_retrieval.schemas.visual import (
    AttributeValue,
    EnrichedListing,
    EnrichmentRun,
    VisualAttributes,
)


class EnrichmentCache:
    """Content-addressed JSON cache for raw provider responses.

    Raw responses are cached rather than normalized objects, so improving the
    normalizer means re-running normalization over the cache rather than paying
    the model again. This is the same reasoning that keeps `raw_payload` on the
    ingestion rows.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def key(self, image_ref: str, model_version: str) -> str:
        payload = f"{image_ref}|{contract_fingerprint()}|{model_version}"
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def path_for(self, image_ref: str, model_version: str) -> Path:
        return self.directory / f"{self.key(image_ref, model_version)}.json"

    def get(self, image_ref: str, model_version: str) -> dict[str, Any] | None:
        path = self.path_for(image_ref, model_version)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None                          # a corrupt entry is a miss

    def put(self, image_ref: str, model_version: str, response: dict[str, Any]) -> None:
        path = self.path_for(image_ref, model_version)
        path.write_text(json.dumps(response, indent=2, ensure_ascii=False), encoding="utf-8")

    def __len__(self) -> int:
        return len(list(self.directory.glob("*.json")))


def enrich_one(
    shortcode: str,
    permalink: str,
    image_ref: str,
    provider: VisionProvider,
    cache: EnrichmentCache | None = None,
    image_sha256: str | None = None,
) -> tuple[EnrichedListing, bool]:
    """Enrich a single listing. Returns (listing, came_from_cache).

    Never raises. A provider failure produces a valid record carrying the error,
    so a batch always returns one row per input and nothing is lost silently.
    """
    from rental_retrieval.enrichment.contract import PROMPT_VERSION

    started = time.perf_counter()
    errors: list[str] = []
    from_cache = False
    raw: dict[str, Any] | None = None

    if cache is not None:
        raw = cache.get(image_ref, provider.model_version)
        from_cache = raw is not None

    if raw is None:
        try:
            raw = provider.extract(image_ref)
            if cache is not None and isinstance(raw, dict):
                cache.put(image_ref, provider.model_version, raw)
        except ProviderError as exc:
            errors.append(f"provider error: {exc}")
            raw = None
        except Exception as exc:                          # noqa: BLE001
            errors.append(f"unexpected provider failure: {type(exc).__name__}: {exc}")
            raw = None

    if raw is None:
        attributes = VisualAttributes(image_quality="unusable")
    else:
        attributes, normalize_errors = normalize(raw)
        errors.extend(normalize_errors)

    latency_ms = int((time.perf_counter() - started) * 1000)

    return (
        EnrichedListing(
            shortcode=shortcode,
            permalink=permalink,
            image_ref=image_ref,
            image_sha256=image_sha256,
            attributes=attributes,
            provider=provider.name,
            model_version=provider.model_version,
            prompt_version=PROMPT_VERSION,
            extracted_at=datetime.now(UTC),
            latency_ms=0 if from_cache else latency_ms,
            raw_response=raw,
            errors=errors,
        ),
        from_cache,
    )


def enrich_batch(
    items: list[dict[str, str]],
    provider: VisionProvider,
    cache: EnrichmentCache | None = None,
) -> tuple[list[EnrichedListing], EnrichmentRun]:
    """Enrich many listings.

    `items` are dicts with shortcode, permalink, image_ref, and optionally
    image_sha256. Order is preserved so output lines up with input.
    """
    from rental_retrieval.enrichment.contract import PROMPT_VERSION

    results: list[EnrichedListing] = []
    cached = 0

    for item in items:
        listing, was_cached = enrich_one(
            shortcode=item["shortcode"],
            permalink=item["permalink"],
            image_ref=item["image_ref"],
            provider=provider,
            cache=cache,
            image_sha256=item.get("image_sha256"),
        )
        results.append(listing)
        cached += int(was_cached)

    succeeded = [r for r in results if r.attributes.coverage > 0]
    failed = [r for r in results if r.attributes.coverage == 0]

    run = EnrichmentRun(
        provider=provider.name,
        model_version=provider.model_version,
        prompt_version=PROMPT_VERSION,
        requested=len(items),
        succeeded=len(succeeded),
        failed=len(failed),
        from_cache=cached,
        total_latency_ms=sum(r.latency_ms or 0 for r in results),
        total_cost_usd=sum(r.cost_usd or 0.0 for r in results),
        mean_coverage=round(
            sum(r.attributes.coverage for r in results) / len(results), 4
        ) if results else 0.0,
        field_coverage=compute_field_coverage(results),
        errors=[e for r in results for e in r.errors][:50],
    )
    return results, run


def compute_field_coverage(listings: list[EnrichedListing],
                           min_confidence: float = 0.0) -> dict[str, float]:
    """Share of listings for which each attribute was obtained.

    This is the number that justifies or condemns the whole enrichment phase.
    Caption extraction gave 2.5% neckline coverage; if vision does not decisively
    beat that, vision is not earning its cost.
    """
    if not listings:
        return {}

    counts: dict[str, int] = {}
    for listing in listings:
        for name, value in listing.attributes:
            if isinstance(value, AttributeValue):
                counts.setdefault(name, 0)
                if value.at_least(min_confidence):
                    counts[name] += 1

    total = len(listings)
    return {name: round(hits / total, 4) for name, hits in sorted(counts.items())}


def write_enriched(path: Path, listings: list[EnrichedListing]) -> None:
    """Write JSONL. Caller is responsible for keeping the path under data/local/."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for listing in listings:
            fh.write(listing.model_dump_json() + "\n")


def read_enriched(path: Path) -> dict[str, EnrichedListing]:
    """Read JSONL into {shortcode: EnrichedListing}."""
    out: dict[str, EnrichedListing] = {}
    if not Path(path).exists():
        return out
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                listing = EnrichedListing.model_validate_json(line)
                out[listing.shortcode] = listing
    return out
