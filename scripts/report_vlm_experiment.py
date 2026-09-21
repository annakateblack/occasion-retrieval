#!/usr/bin/env python
"""Summarize a controlled caption-vs-vision experiment.

PUBLIC-SAFE. This script contains no real labels or private identifiers. It reads
private experiment outputs and anchor labels from data/local/ at runtime.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.eval.reporting import load_inputs, print_report, summarize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--event-key", required=True)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    experiment, anchors = load_inputs(args.experiment, args.anchors)
    summary = summarize(experiment, anchors, args.event_key, k=args.k)
    print_report(summary)

    if args.json_out is not None:
        if "local" not in args.json_out.resolve().parts:
            parser.error("Refusing to write a private report outside a path containing 'local'.")
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nwrote (gitignored): {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
