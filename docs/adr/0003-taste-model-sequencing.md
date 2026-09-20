# ADR 003: Start the taste model at top-k nearest neighbor, earn every addition

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** Anna Kate Black

> **Sequencing note (ADR 005):** ADR 005 later inserted a deterministic
> personalized baseline and a controlled visual-attribute enrichment experiment
> before the top-5 NN taste model. That experiment keeps the same ranker and is
> not the VLM reranking rejected/deferred here. ADR 005 supersedes the step
> numbers below; this ADR still governs taste-model sequencing.

## Context

The system must score an arbitrary rental dress against the user's learned taste. The available supervision is a corpus of saved products from a fashion save app: **positives only**, no negatives, no ratings, no record of what was rejected. This is a positive-unlabeled setting.

Several representations are plausible: a single mean vector, a recency-weighted mean, clustered style modes, an attribute-frequency profile, or a learned preference classifier. An earlier design pass in this project recommended starting with clustered taste modes plus attribute PMI scoring plus learned fusion weights plus VLM reranking, all in V1.

That recommendation was wrong for this project, for a reason that has nothing to do with whether those techniques work. A stated goal is that every design decision can be defended in a technical interview. "I used clustered taste profiles" is only defensible if the answer to "compared to what?" is a number. Building the sophisticated version first destroys the ability to ever produce that number, because there is no baseline to compare against and no appetite to go back and build one.

There is also a real risk that the sophisticated version is worse. Clustering a few hundred items can produce unstable clusters. Averaging within a cluster discards exactly the fine distinctions that make a garment feel like the user's taste. Nearest-neighbor methods have neither problem.

## Decision

**V1 taste score is the mean cosine similarity to the top-k most similar items in the user's saved dress corpus, with k = 5.**

```
taste_score(x) = mean of the k highest values of cos(x_img, v_i)
                 over all v_i in the user's saved dress embeddings
```

No clustering. No attribute PMI. No learned fusion weights. No VLM reranking. Those are **deferred, not rejected**, and each one is gated on a measured improvement against this baseline on the Phase 5 evaluation set.

The build order is:

| Step | Taste model | Gate to proceed |
|---|---|---|
| 0 | Random within hard filter | Establishes the floor |
| 1 | Price-ascending within hard filter | An honest non-ML baseline |
| 2 | **Top-5 NN against saved items (V1)** | Must beat step 1 |
| 3 | Recency weighting on the same NN | Must beat step 2 |
| 4 | Clustered / multi-mode profile | Must beat step 3 |
| 5 | Attribute component, fusion weights | Must beat step 4 |
| 6 | Reranking | Must beat step 5 on quality per unit cost |

Any step that does not beat its predecessor is reported as a negative result in the evaluation report and not merged into the default path. A negative result documented honestly is a stronger portfolio artifact than an unmeasured feature.

## Alternatives considered

**Single mean vector (centroid).** Rejected as the default, retained as an evaluation arm. Taste is multimodal in the statistical sense: formal, game day, and everyday saves may be close to orthogonal in embedding space, and their mean lands in a region where nothing real lives. It is also a low-variance point, so most candidates score moderately similar and ranking loses discriminative power. Worth building precisely to demonstrate this failure.

**Clustered multi-mode profile.** Deferred to step 4. It is likely the right eventual answer and it has a genuine product advantage, since a cluster label gives a natural explanation string. But it introduces `k` as a hyperparameter requiring justification, risks thin clusters, and cannot be shown to help without the baseline this ADR establishes.

**Attribute-frequency (PMI) profile.** Deferred to step 5. Interpretable, cheap, and valuably domain-invariant, since it is unaffected by the visual gap between retail product photography and user-generated Instagram images. Its failure modes differ from embedding similarity, which makes it a good complement rather than a replacement.

**Learned preference classifier.** Rejected for V1 and probably beyond. A few hundred positives in over a thousand dimensions overfits even a regularized linear model, and the "negatives" would be unlabeled catalog items that include genuine positives. The defensible version of this idea is fitting the four or five fusion weights in step 5, which is the same intuition with two orders of magnitude fewer parameters.

## Consequences

**Positive.** The first working system is roughly forty lines and fully explainable. Explanation comes free and is arguably better than a cluster label: the interface can show the three saved items that drove the match, which is concrete evidence rather than a generated description. Every later addition arrives with a measured justification. Being able to say "clustering improved nDCG@10 by 0.04 over top-5 NN" is a materially stronger interview answer than "I used clustering."

**Negative.** Nearest-neighbor scoring is sensitive to outliers in the corpus, so a single unrepresentative save can distort a region of the space. It produces no interpretable structure describing the user's style overall. It is O(n) per candidate, which is irrelevant at this corpus size.

**Accepted risk.** Raw cosine similarity is not calibrated and is not comparable across encoders or queries. Before any score fusion occurs (step 5 onward), scores must be converted to percentiles against the catalog's own score distribution. This is recorded here so it is not forgotten when fusion is introduced.

**Dependency.** The saved-item corpus must be filtered to dresses before use. The user's saves include a substantial volume of non-apparel items, including a home goods collection that is the single largest collection in the corpus. An unfiltered taste model would place meaningful weight on furniture. This filter is a Phase 1 requirement, not an optimization.

## What remains reversible

All of it. The taste model is a single function with the signature `(candidate_embedding, taste_profile) -> float`. Every deferred approach implements the same signature. Swapping implementations changes no schema, no retrieval pipeline, and no evaluation code. The `taste_profiles` table carries a `method` column and a `params` JSONB column specifically so that multiple approaches can coexist and be compared on the same catalog without a migration.
