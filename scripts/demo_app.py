#!/usr/bin/env python
"""Tiny local product demo for the occasion-retrieval engine.

Runs entirely on local data and Python stdlib. Open http://127.0.0.1:8000,
type a request such as "I need a My Tie dress for spring under $100", and
see ranked cards from the existing deterministic engine.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.enrichment.orchestrator import read_enriched  # noqa: E402
from rental_retrieval.enrichment.work_adapter import make_vision_attribute_provider  # noqa: E402
from rental_retrieval.retrieval.baseline import rank  # noqa: E402
from rental_retrieval.schemas.event import EventContext  # noqa: E402
from rental_retrieval.schemas.instagram import InstagramSavedPost  # noqa: E402
from rental_retrieval.schemas.profile import PersonalFitProfile  # noqa: E402

PRESETS = {
    "my tie": "config/events/my_tie_september.json",
    "christmas cocktail": "config/events/christmas_cocktail.json",
    "date night": "config/events/date_night_generic.json",
    "parents weekend": "config/events/parents_weekend_formal.json",
    "fun cocktail": "config/events/fun_cocktail.json",
}


def load_inventory(path: Path) -> list[InstagramSavedPost]:
    return [InstagramSavedPost.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_event(path: Path) -> EventContext:
    raw = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
           if not k.startswith("_")}
    return EventContext.model_validate(raw)


def event_from_query(query: str) -> EventContext:
    q = query.lower().strip()
    preset_path = None
    for key, path in PRESETS.items():
        if key in q and Path(path).exists():
            preset_path = Path(path)
            break
    if preset_path is None:
        preset_path = Path("config/events/date_night_generic.json")
    event = load_event(preset_path)

    for season in ("spring", "summer", "fall", "winter"):
        if season in q:
            event = event.model_copy(update={"season": season})
            break

    budget = re.search(r"(?:under|below|max(?:imum)?|budget(?: of)?)\s*\$?\s*(\d{2,3})", q)
    if budget:
        cents = int(budget.group(1)) * 100
        event = event.model_copy(update={
            "price_rule": event.price_rule.model_copy(update={"max_rental_price_cents": cents})
        })

    length_updates = {}
    if "mini" in q:
        length_updates = {"mini": 1.0, "midi": 0.4, "maxi": 0.15, "unknown": 0.4}
    elif "midi" in q:
        length_updates = {"mini": 0.4, "midi": 1.0, "maxi": 0.5, "unknown": 0.4}
    elif "maxi" in q or "gown" in q:
        length_updates = {"mini": 0.15, "midi": 0.6, "maxi": 1.0, "unknown": 0.4}
    if length_updates:
        event = event.model_copy(update={"length_preference": event.length_preference.model_copy(update=length_updates)})

    # Keep the user request visible to the deterministic vibe parser.
    event = event.model_copy(update={"vibe_text": query})
    return event


def money(cents: int | None) -> str:
    return f"${cents / 100:.0f}" if cents is not None else "Price not stated"


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def image_src(shortcode: str, image_root: Path) -> str:
    """Prefer a private local image, otherwise ask Instagram for the public post image.

    The fallback does not download or persist anything; the browser requests the
    image directly from Instagram at render time. If Instagram blocks the request,
    the card's onerror handler falls back to the neutral placeholder.
    """
    for suffix in (".jpg", ".jpeg", ".png", ".webp"):
        p = image_root / f"{shortcode}{suffix}"
        if p.exists():
            return f"/image/{p.name}"
    return f"https://www.instagram.com/p/{shortcode}/media/?size=l"


def card(row, image_root: Path) -> str:
    src = image_src(row.shortcode, image_root)
    visual = (
        f'<img src="{html.escape(src, quote=True)}" alt="dress image" '
        'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
        '<div class="placeholder fallback-placeholder" style="display:none">✦</div>'
    )
    owner = html.escape(row.owner_username or "rental listing")
    explanation = html.escape(row.explanation)
    flags = " · ".join(html.escape(f) for f in row.flags)
    return f"""
    <article class="card">
      <div class="visual">{visual}</div>
      <div class="content">
        <div class="eyebrow">#{row.rank} · @{owner}</div>
        <h2>{money(row.rental_price_cents)} · {html.escape(row.dress_length.title())}</h2>
        <div class="score"><strong>{row.overall_score:.3f}</strong> match score</div>
        <div class="chips">
          <span>Fit {pct(row.fit_score)}</span><span>Style {pct(row.style_score)}</span>
          <span>Event {pct(row.event_score)}</span><span>Season {pct(row.seasonality_score)}</span>
        </div>
        <p>{explanation}</p>
        {f'<p class="flags">{flags}</p>' if flags else ""}
        <a class="button" href="{html.escape(row.permalink)}" target="_blank" rel="noopener">Open listing ↗</a>
      </div>
    </article>
    """


def page(query: str, run, image_root: Path, enriched_count: int) -> str:
    cards = "".join(card(r, image_root) for r in run.results)
    note = (f"{enriched_count} cached vision-enriched listings available" if enriched_count
            else "Caption-only demo mode")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anna Kate's Dress Search</title>
<style>
:root{{--bg:#fff7fa;--ink:#4a2f39;--muted:#8f7480;--card:#ffffff;--accent:#d98fa5;--accent-dark:#b96983;--line:#f0dfe6;--chip:#fbeef3;--placeholder:#f7e9ee}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}}
.shell{{max-width:1180px;margin:auto;padding:48px 24px 80px}}
.brand{{font-family:Georgia,serif;font-size:48px;letter-spacing:-1.2px;margin:0;color:#8f4f67}}
.sub{{color:var(--muted);max-width:760px;margin:10px 0 28px;line-height:1.5;font-size:17px}}
form{{display:flex;gap:10px;max-width:900px;margin-bottom:12px}}
input{{flex:1;padding:17px 18px;border:1px solid var(--line);border-radius:16px;background:white;font-size:16px;box-shadow:0 6px 22px rgba(185,105,131,.08)}}
button,.button{{background:linear-gradient(135deg,var(--accent),var(--accent-dark));color:white;border:0;border-radius:14px;padding:15px 20px;font-weight:700;text-decoration:none;display:inline-block;cursor:pointer;box-shadow:0 8px 20px rgba(185,105,131,.18)}}
.meta{{color:var(--muted);font-size:13px;margin-bottom:26px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:22px;overflow:hidden;box-shadow:0 12px 32px rgba(185,105,131,.10)}}
.visual{{height:270px;background:var(--placeholder);display:flex;align-items:center;justify-content:center}}
.visual img{{width:100%;height:100%;object-fit:cover}} .placeholder{{font-size:54px;color:#d7aabd;width:100%;height:100%;align-items:center;justify-content:center}}
.content{{padding:18px}} .eyebrow{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}}
h2{{font-family:Georgia,serif;font-size:24px;margin:8px 0}} .score{{margin:7px 0 12px}}
.chips{{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:12px}} .chips span{{font-size:12px;background:var(--chip);padding:6px 8px;border-radius:999px;color:#7f5365}}
p{{line-height:1.45}} .flags{{color:#8a5c3c;font-size:13px}}
@media(max-width:650px){{form{{flex-direction:column}}.brand{{font-size:36px}}}}
</style></head><body><main class="shell">
<h1 class="brand">Anna Kate&#39;s Dress Search</h1>
<p class="sub">Finding a date is hard enough, so finding a dress should be the least stressful part.</p>
<form method="get"><input name="q" value="{html.escape(query, quote=True)}" placeholder="I need a My Tie dress for spring under $100"><button type="submit">Find dresses</button></form>
<div class="meta">{html.escape(run.event_name)} · {run.total_candidates} candidates · {run.returned} shown · {html.escape(note)}</div>
<section class="grid">{cards}</section>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--enriched", type=Path, default=None)
    parser.add_argument("--image-root", type=Path, default=Path("data/local/seed_images"))
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    inventory = load_inventory(args.inventory)
    profile = PersonalFitProfile.model_validate_json(args.profile.read_text(encoding="utf-8"))
    provider = None
    enriched_count = 0
    if args.enriched and args.enriched.exists():
        enriched = read_enriched(args.enriched)
        enriched_count = len(enriched)
        provider = make_vision_attribute_provider(enriched)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path.startswith("/image/"):
                name = Path(parsed.path.removeprefix("/image/")).name
                target = args.image_root / name
                if not target.exists():
                    self.send_error(404)
                    return
                content_type = "image/png" if target.suffix.lower() == ".png" else "image/jpeg"
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            params = parse_qs(parsed.query)
            query = params.get("q", ["I need a My Tie dress for spring under $100"])[0]
            event = event_from_query(query)
            # Demo-only rerank: for visually specific requests, prefer candidates
            # with actual style/season evidence instead of letting price/fit dominate.
            run = rank(inventory, profile, event, top_k=60, attribute_provider=provider)
            q_lower = query.lower()
            visual_request = any(term in q_lower for term in (
                "christmas", "holiday", "festive", "spark", "sequin", "glam",
                "spring", "summer", "bright", "playful", "romantic", "my tie",
            ))
            if visual_request:
                def demo_score(row):
                    event_score = row.event_score if row.event_score is not None else 0.5
                    season_score = row.seasonality_score if row.seasonality_score is not None else 0.35
                    style_score = row.style_score if row.style_score is not None else 0.35
                    evidence_bonus = 0.05 * sum(
                        value is not None
                        for value in (row.style_score, row.seasonality_score)
                    )
                    return (
                        row.overall_score
                        + 0.18 * (event_score - 0.5)
                        + 0.12 * (season_score - 0.5)
                        + 0.08 * (style_score - 0.5)
                        + evidence_bonus
                    )

                run.results.sort(key=lambda row: (-demo_score(row), row.rank, row.shortcode))
            run.results = run.results[:8]
            run.returned = len(run.results)
            for index, row in enumerate(run.results, start=1):
                row.rank = index
            body = page(query, run, args.image_root, enriched_count).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *values):
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Occasion demo running at http://127.0.0.1:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
