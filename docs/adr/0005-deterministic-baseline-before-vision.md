# ADR 005: Establish a deterministic personalized baseline before visual enrichment

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Anna Kate Black

## Context

The first working rental-dress ranker operates on the normalized Instagram export and the caption-derived attributes already available in the catalog. It applies hard filters for explicit incompatibility, then ranks surviving dresses with interpretable components for fit, event match, price, seasonality, style, and availability.

This baseline was built before adding image understanding so later model layers can be judged against a concrete reference rather than against intuition.

The real 157-dress inventory exposed the central limitation immediately. Captions carry size and price well, but visual attributes are sparse: length is available for only a minority of listings, and colour, sleeve, neckline, sparkle/texture, and fabric are much thinner. As a result, the baseline can enforce constraints and recover obvious event cues, but it cannot reliably distinguish visually different occasions.

In the My Tie September run, 157 candidates produced 150 hard-filter survivors and 10 results. The top results were dominated by mini length, fit evidence, and price. A separate comparison between My Tie and Christmas Cocktail showed 9/10 top-result overlap despite the events having different seasonal and palette intent.

## Decision

Treat the deterministic personalized ranker as **baseline 1b** between the non-ML ordering baseline and the future visual/taste models.

The sequence is now:

| Step | Model | Gate to proceed |
|---|---|---|
| 0 | Random within hard filter | Establish floor |
| 1 | Price-ascending within hard filter | Non-ML ordering baseline |
| 1b | **Deterministic personalized ranker** | Establish explainable fit/event baseline |
| 2 | Visual attribute enrichment + rerun of the same ranker | Must improve event discrimination and labelled-query metrics |
| 3 | Top-5 NN taste model | Must beat step 2 on the held-out evaluation set |
| 4+ | Recency, clustering, fusion, reranking | Each must beat the prior step |

The visual-enrichment experiment should preserve the downstream ranker and change only the attribute source. This isolates whether the improvement comes from better garment observability rather than from changing multiple ranking mechanisms simultaneously.

## Why this is useful

The baseline creates a measurable failure rather than a vague motivation for multimodal AI:

- sparse captions make style largely unobservable;
- event-specific queries collapse toward the same top-ranked minis;
- price becomes an outsized discriminator when visual signals are absent;
- the system can explain why it is uncertain.

Those are testable claims. The next phase must show that visual enrichment improves them.

## Evaluation gates for the visual phase

The visual layer should be evaluated on at least these dimensions:

1. **Attribute coverage:** increase observed colour, sleeve, neckline, silhouette, sparkle/texture, and fabric without inventing unsupported values.
2. **Event discrimination:** reduce inappropriate top-10 overlap between intentionally different events such as My Tie and Christmas Cocktail.
3. **Human-labelled relevance:** improve nDCG@10 on the local labelled query set.
4. **Stability:** preserve hard-filter behavior and deterministic ranking when enriched attributes are held constant.
5. **Auditability:** keep the model output structured, preserve uncertainty, and record which attributes came from vision rather than caption parsing.

No improvement means the visual step remains an experimental branch rather than becoming the default path.

## Alternatives considered

**Go directly to embeddings.** Deferred. Embeddings over sparse captions do not solve the missing-attribute problem. Adding a VLM attribute pass first isolates the value of visual understanding.

**Replace the rule-based ranker with a VLM judge.** Rejected for this stage. It would combine extraction, reasoning, personalization, and ranking into one opaque model call and destroy the baseline comparison.

**Hand-label visual attributes.** Rejected as the production path. It is acceptable for a small gold evaluation set, but not for enriching the catalog.

## Consequences

**Positive.** The repo now has a defensible experiment sequence: deterministic baseline, measured failure, controlled multimodal enrichment, then taste embeddings.

**Negative.** The visual phase adds model cost and a new source of extraction error. It also requires resolving usable images from Instagram permalinks or another sanctioned snapshot source.

**Accepted risk.** Image-derived attributes may be imperfect. They must therefore retain provenance and uncertainty rather than being treated as ground truth.

## What remains reversible

The ranker does not care whether an attribute came from caption text or a VLM as long as it conforms to the same normalized attribute contract. The visual provider can therefore be swapped or disabled without changing the rest of retrieval.
