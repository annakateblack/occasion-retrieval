"""Loading and using the private anchor label set.

The anchor file contains real permalinks and a person's real judgements. It is
never committed. This module enforces that structurally rather than trusting a
convention:

- `DEFAULT_ANCHOR_PATH` points inside `data/local/eval/`, which is gitignored.
- `load_anchor_set` warns loudly when asked to read real labels from outside a
  private path, because that usually means the file is about to be committed.
- Nothing here writes anywhere. Callers choose output paths and the CLI refuses
  any path not under `data/local/`.

The public template at `config/seed/anchor_labels.template.json` documents the
shape with invented permalinks, so a reader of the repo can see the schema
without seeing anyone's labels.

PUBLIC-SAFE. Logic only; the data it reads is not.
"""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

from pydantic import BaseModel, Field

from rental_retrieval.schemas.anchors import AnchorSet, MeasurementGate
from rental_retrieval.schemas.visual import EnrichedListing

DEFAULT_ANCHOR_PATH = Path("data/local/eval/anchor_labels.json")
TEMPLATE_PATH = Path("config/seed/anchor_labels.template.json")

PRIVATE_PATH_MARKERS = ("local", "private")

STUB_PROVIDERS = frozenset({
    "stub", "failing", "synthetic_fixture", "none", "", "skeleton", "live",
})
"""Providers whose output is not evidence about any real image.

'skeleton' is the unimplemented vendor template. 'live' is listed because it is
a CLI alias rather than a provider that ever writes its own name: the
implemented provider records itself as 'anthropic'. If a record ever carries
provider='live', something constructed a provider without implementing it, and
treating that as measured would be wrong."""

SHORTCODE_RE = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")


class AnchorPrivacyWarning(UserWarning):
    """Raised as a warning when real labels are read from a committable path."""


def _is_private_path(path: Path) -> bool:
    parts = [part.lower() for part in Path(path).resolve().parts]
    return (
        any(parts[i:i + 2] == ["data", "local"] for i in range(len(parts) - 1))
        or "private" in parts
    )


def shortcode_from_permalink(permalink: str) -> str | None:
    """Extract the shortcode, tolerating query strings such as ?img_index=1."""
    if not permalink:
        return None
    match = SHORTCODE_RE.search(permalink.split("?")[0])
    return match.group(1) if match else None


def load_anchor_set(path: Path | None = None, *, require_private: bool = True) -> AnchorSet:
    """Load an anchor set, filling shortcodes from permalinks where absent.

    `require_private` warns rather than raises: a legitimate case is loading the
    committed template, which contains no real labels. The warning fires only
    when `is_real` is true AND the path looks committable, which is the
    combination that would leak.
    """
    path = Path(path or DEFAULT_ANCHOR_PATH)
    if not path.exists():
        raise FileNotFoundError(
            f"No anchor file at {path}. Real labels belong in "
            f"{DEFAULT_ANCHOR_PATH} (gitignored). See {TEMPLATE_PATH} for the shape."
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}

    for item in raw.get("items", []):
        for key in [k for k in item if k.startswith("_")]:
            item.pop(key)
        if not item.get("shortcode"):
            derived = shortcode_from_permalink(item.get("permalink", ""))
            if derived:
                item["shortcode"] = derived

    anchor_set = AnchorSet.model_validate(raw)

    if require_private and anchor_set.is_real and not _is_private_path(path):
        warnings.warn(
            f"Anchor set at {path} is marked is_real=true but is not under a private "
            f"path. Real labels must live in {DEFAULT_ANCHOR_PATH}, which is gitignored.",
            AnchorPrivacyWarning,
            stacklevel=2,
        )

    return anchor_set


def build_measurement_gate(anchor_set: AnchorSet | None, provider_name: str,
                           label_source: str) -> MeasurementGate:
    """Decide whether results from this configuration may be called measured."""
    return MeasurementGate(
        labels_are_real=bool(anchor_set and anchor_set.is_real),
        provider_is_live=provider_name.lower() not in STUB_PROVIDERS,
        provider_name=provider_name or "none",
        label_source=label_source,
    )


# --- Coverage of the anchors by the pipeline ---------------------------------

class AnchorCoverage(BaseModel):
    """Whether each anchor is actually reachable by the system.

    An anchor the pipeline never sees cannot be ranked, so it silently drops out
    of any metric computed over rankings. Reporting this separately is what
    turns an invisible recall failure into a visible one.
    """

    total_anchors: int
    in_inventory: list[str] = Field(default_factory=list)
    missing_from_inventory: list[str] = Field(default_factory=list)
    enriched: list[str] = Field(default_factory=list)
    missing_enrichment: list[str] = Field(default_factory=list)

    @property
    def inventory_rate(self) -> float:
        return round(len(self.in_inventory) / self.total_anchors, 4) if self.total_anchors else 0.0

    @property
    def enrichment_rate(self) -> float:
        return round(len(self.enriched) / self.total_anchors, 4) if self.total_anchors else 0.0

    def notes(self) -> list[str]:
        out: list[str] = []
        if self.missing_from_inventory:
            out.append(
                f"{len(self.missing_from_inventory)} anchor(s) are not in the ranked "
                f"inventory and cannot appear in any result: "
                f"{', '.join(self.missing_from_inventory)}. Check the garment-type "
                "filter and the classifier before trusting recall numbers."
            )
        if self.missing_enrichment:
            out.append(
                f"{len(self.missing_enrichment)} anchor(s) have no visual enrichment; "
                "they will be scored on captions alone."
            )
        return out


def compute_anchor_coverage(
    anchor_set: AnchorSet,
    inventory_shortcodes: set[str],
    enriched_by_code: dict[str, EnrichedListing] | None = None,
) -> AnchorCoverage:
    """Check which anchors the pipeline can actually reach."""
    enriched_by_code = enriched_by_code or {}
    codes = [item.shortcode for item in anchor_set.items]

    return AnchorCoverage(
        total_anchors=len(codes),
        in_inventory=[c for c in codes if c in inventory_shortcodes],
        missing_from_inventory=[c for c in codes if c not in inventory_shortcodes],
        enriched=[c for c in codes if c in enriched_by_code],
        missing_enrichment=[c for c in codes if c not in enriched_by_code],
    )


# --- Anti-rule checks --------------------------------------------------------

class AntiRuleResult(BaseModel):
    """Outcome of one testable anti-rule drawn from the labels."""

    shortcode: str
    rule: str
    passed: bool
    detail: str


KNOWN_ANTI_RULES = {
    "sparkle_implies_holiday": (
        "A sparkly item must not be graded strong for a holiday event purely "
        "because it sparkles, when the labeller placed it elsewhere."
    ),
}


def check_anti_rules(anchor_set: AnchorSet,
                     ranked_by_event: dict[str, list[str]],
                     top_k: int = 5) -> list[AntiRuleResult]:
    """Evaluate the labeller's anti-rules against actual rankings.

    Currently implements `sparkle_implies_holiday`: an item carrying that
    anti-rule must not land in a holiday event's top-k unless the labeller
    actually graded it for that event. This is the one anti-rule the real label
    set asserts, and it is checked rather than assumed.
    """
    results: list[AntiRuleResult] = []
    holiday_keys = [k for k in ranked_by_event if "christmas" in k or "holiday" in k]

    for item in anchor_set.items:
        for rule in item.negative_associations:
            if rule != "sparkle_implies_holiday":
                results.append(AntiRuleResult(
                    shortcode=item.shortcode, rule=rule, passed=True,
                    detail="no checker implemented for this rule; recorded only",
                ))
                continue

            violated: list[str] = []
            for key in holiday_keys:
                top = ranked_by_event.get(key, [])[:top_k]
                labelled = item.grade_for(key)
                if item.shortcode in top and labelled in (None, "weak", "not_relevant"):
                    violated.append(key)

            results.append(AntiRuleResult(
                shortcode=item.shortcode,
                rule=rule,
                passed=not violated,
                detail=(
                    f"appeared in top-{top_k} for {', '.join(violated)} without a "
                    "supporting label"
                    if violated else
                    f"absent from the top-{top_k} of every holiday event, or labelled for it"
                ),
            ))

    return results
