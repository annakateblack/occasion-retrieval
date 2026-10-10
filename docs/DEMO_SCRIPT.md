# Five-minute demo script

## 0:00–0:30 — Open with the problem

**Show:** README.md

**Say:**
“I started with a very practical retrieval problem: rental inventory lives in Instagram posts, but the captions are inconsistent. They often tell me size or price and omit the visual things that actually matter for an event, like neckline, silhouette, sleeve, fabric, or colour.”

Then state the project question: whether image-derived attributes can improve an explainable ranker without turning the system into a black box.

## 0:30–1:30 — Show the baseline and architecture

**Show:** docs/ARCHITECTURE.md

Walk through:

private source -> normalized listing -> caption resolution -> optional vision enrichment -> same deterministic ranker -> controlled evaluation

**Say:**
“The model never ranks the dresses. It only supplies structured observations. The same deterministic ranker makes the final decision in both arms.”

Call out the three boundaries:

- unknowns are preserved instead of guessed;
- known caption facts are not silently overwritten;
- transactional facts such as availability remain outside vision authority.

## 1:30–2:30 — Explain the controlled A/B

**Show:** the “One ranker, two experiment arms” section in docs/ARCHITECTURE.md.

**Say:**
“The main thing I wanted to avoid was comparing two different systems and then attributing the difference to vision. Both arms use the same inventory, event, weights, filters, threshold, and sorting. The enriched arm only changes previously unknown visual attributes.”

Mention that the current test suite has 296 passing tests and the baseline path remains deterministic.

## 2:30–3:45 — Show the live result

**Show:** docs/VLM_EXPERIMENT_RESULTS.md

Lead with the exact result:

- 7/7 reachable images enriched successfully;
- 91.4% of requested visual-attribute slots filled on those seven listings;
- 7/157 catalog candidates enriched;
- 3 positive My Tie anchors, 2 reachable;
- reachable positives moved 90 -> 35 and 89 -> 44.

Then say:

“nDCG@10 is 0 in both arms. I initially thought of that as simply flat, but the deeper diagnostic matters: none of my positive labels were near the top 10. At k=50, nDCG goes from 0 to 0.1846 because vision moves both reachable positives into that range.”

Do **not** say “vision improved the overall system.” Say:

“The evidence is positive at the treated-item level, but the experiment is not powered to establish catalog-wide top-10 lift.”

## 3:45–4:30 — Show the engineering judgment

**Show:** privacy/provenance section of README.md or docs/ARCHITECTURE.md.

**Say:**
“The part I care about most is that the system can tell me when a number is not evidence. Real measurement requires real labels and a live provider. Private row-level data never enters the repo. Provider version, confidence, evidence, and contract fingerprint are retained so I can audit where a value came from.”

## 4:30–5:00 — End with the next experiment

**Say:**
“I would not add more model complexity next. I would improve the evaluation: label more items near the top of the ranking, expand enrichment coverage using a predeclared sample, and separate candidate-generation loss from ranking loss with full-qrel and reachable-qrel nDCG. Only after that would I test embeddings or learned reranking.”

## If asked “what was the hardest part?”

“Designing the experiment so that a model improvement could not be confused with a ranker change. The provider seam and fill-only policy let me change observability while holding the decision layer fixed.”

## If asked “what surprised you?”

“The top-10 metric was technically valid but not informative. My positive labels were too deep in the ranking, so the more interesting finding appeared in the depth curve and per-anchor movement. That forced me to distinguish a bad model result from a poorly powered evaluation.”

## If asked “what would you change?”

“I would design the relevance set earlier and make sure it contains enough positive examples near the cutoff I plan to report. The engineering pipeline was ready before the evaluation set had enough resolution.”
