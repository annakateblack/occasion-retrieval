"""Synthetic anchor fixtures shared by more than one test module.

WHY THIS FILE EXISTS
--------------------
`make_anchor_set` was defined in `test_anchors.py` and imported from
`test_live_provider.py` as `from tests.test_anchors import make_anchor_set`.
That works only when the repo root happens to be on `sys.path` AND `tests/` is
treated as a package; in an independent environment it fails with
`ModuleNotFoundError: No module named 'tests'`. Importing one test module from
another is also a collection-order hazard.

The helper now lives here and both modules import it the same way
`_work_contracts_double.py` is imported, by putting this directory on the path:

    sys.path.insert(0, str(Path(__file__).parent))
    from _anchor_fixtures import make_anchor_set

PRIVACY
-------
Every shortcode and permalink below is synthetic (`TEST...`). No real anchor,
permalink or label is ever placed in a committed file. The real labels live
only in `data/local/eval/anchor_labels.json`, which is gitignored.
"""

from __future__ import annotations

from rental_retrieval.schemas.anchors import AnchorLabel, AnchorSet, EventRelevance


def make_anchor_set(is_real: bool = False) -> AnchorSet:
    """Two synthetic anchors covering the grade range and the parsing edge case.

    `is_real` flips only the flag, not the data: it exists so measurement-gate
    tests can assert that a set *claiming* to be real still needs a live
    provider before `is_measured` becomes true.
    """
    return AnchorSet(
        name="test_anchors",
        description="synthetic",
        created_at="2026-09-20",
        is_real=is_real,
        labeller="test",
        items=[
            AnchorLabel(
                shortcode="TESTAAA1",
                permalink="https://www.instagram.com/p/TESTAAA1/",
                relevance=[
                    EventRelevance(event_key="my_tie", grade="strong"),
                    EventRelevance(event_key="date_night_versatile", grade="plausible"),
                ],
                label_confidence="medium",
                fit_notes="listed size may not be ideal",
            ),
            AnchorLabel(
                shortcode="TESTBBB2",
                permalink="https://www.instagram.com/p/TESTBBB2/?img_index=1",
                relevance=[
                    EventRelevance(event_key="christmas_cocktail", grade="weak"),
                    EventRelevance(event_key="my_tie", grade="plausible"),
                ],
                negative_associations=["sparkle_implies_holiday"],
            ),
        ],
    )
