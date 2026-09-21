# Professor brief: occasion retrieval from fragmented social-commerce inventory

## Project question

Can image-derived garment attributes improve an explainable, deterministic dress-rental ranker when Instagram listing captions omit most visual details?

The system is deliberately not an end-to-end black-box recommender. The vision model observes garments; the deterministic ranker remains the decision layer.

## What I built

The project has four main layers:

1. **Local ingestion and normalization.** Official Instagram export data is parsed locally into structured rental listings while preserving unknowns rather than inventing missing facts.
2. **Deterministic caption baseline.** Hard filters remove explicit incompatibilities, then interpretable fit, event, style, seasonality, price, and availability signals produce a reproducible ranking.
3. **Provider-neutral visual enrichment.** A live Anthropic vision provider extracts structured garment attributes behind a stable interface. Vision may fill only previously unknown visual fields above a confidence floor. Caption-derived transactional facts remain authoritative, and conflicts are recorded instead of silently overwritten.
4. **Controlled evaluation.** Caption-only and enriched arms use the same inventory, event, ranker, weights, thresholds, filters, and sorting logic. The intended experimental difference is attribute observability.

The repo currently has 296 passing tests.

## Why vision was necessary

The caption baseline was useful for size, price, and availability, but many style-relevant attributes were absent from captions. That made the system highly observable on transactional facts and poorly observable on garment construction.

The visual layer was added to answer a narrow engineering question: whether filling those unknowns changes rankings without changing the ranking algorithm itself.

## First live experiment

The live run processed 7 reachable anchor images.

- 7/7 live enrichments succeeded.
- The model filled 91.4% of requested visual-attribute slots across those seven processed listings.
- Only 7 of 157 catalog candidates were enriched, so treatment coverage was 4.5%.
- There were 3 positive My Tie anchors in the human label set; 2 were reachable by the dress-ranking pipeline.
- Those two reachable positives moved from ranks 90 and 89 to ranks 35 and 44.
- Across the 6 anchors comparable in both arms, 5 moved up, 1 moved down one place, and mean rank movement was +20.5 places.
- nDCG@10 was 0.0000 in both arms because no labelled positive appeared in the top 10.
- At k=50, where the labelled positives enter the ranking, nDCG increased from 0.0000 to 0.1846.

## What the result means

The experiment does **not** demonstrate catalog-wide top-10 improvement.

It does show three useful things:

1. the live multimodal path works operationally;
2. the visual signal materially changed the ranks of the treated positive examples;
3. the original top-10 metric had no resolution on this small, deep label set.

The correct interpretation is therefore: **promising treated-item movement under a controlled ranker, with insufficient catalog and label coverage to claim top-of-ranking lift.**

That distinction is important to the project. The system is designed to surface when an experiment cannot support the stronger claim.

## Methodological safeguards

- Real metrics are gated on both real human labels and a live provider.
- Vision does not overwrite known caption values.
- Size, price, availability, and stated dates remain outside vision authority.
- Provider/model version and extraction-contract fingerprint are retained for provenance and cache identity.
- Private images, captions, usernames, permalinks, labels, caches, and API credentials stay under gitignored local paths.
- The baseline remains deterministic and runnable without network access.

## Limitations

The label set is small, with only three positive My Tie anchors. One positive was classified as a set and never entered the dress inventory, so candidate-generation loss and ranking loss must be separated. Only 4.5% of catalog candidates were enriched, and richer visual fields are not all consumed by the current scoring model.

The reported +20.5 mean movement is over the 6 of 8 anchors comparable in both arms, not the full label set.

## What I would do next

The next experiment should keep the same ranker and expand evidence rather than immediately add model complexity:

1. predeclare broader enrichment coverage for the candidate pool;
2. add relevance labels nearer the top of the ranking so nDCG@10 has statistical resolution;
3. report both full-qrel and reachable-qrel nDCG to separate candidate-generation loss from ranking quality;
4. preserve the same weights, filters, confidence floor, and merge policy.

Longer term, embeddings or learned reranking should only be added if they beat this interpretable baseline on a better-powered evaluation.
