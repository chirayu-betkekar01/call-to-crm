"""Human-readable summary of a handoff envelope, shared by every agent's CLI."""
from __future__ import annotations

from pathlib import Path

from .models import HandoffEnvelope


def summary(env: HandoffEnvelope, path: Path | str) -> str:
    rows = [f"\n=== {env.source.title} ({env.source.type}) ===", f"handoff -> {path}"]
    rows.append(f"{'NAME':26} {'TITLE':26} {'COMPANY':18} {'SIDE':9} {'PRESENCE':15} {'LIST':4} REVIEW")
    for p in env.people:
        rows.append(
            f"{p.full_name[:26]:26} {(p.title or '-')[:26]:26} {(p.company or '-')[:18]:18} {p.side:9} "
            f"{p.presence:15} {'yes' if p.in_attendee_list else 'no':4} {'!' if p.needs_review else ''}"
        )
        if p.aliases:
            rows.append(f"{'':26}   aka: {', '.join(p.aliases)}")
        for r in p.buying_roles:
            rows.append(f"{'':26}   ROLE {r.role} ({r.confidence:.2f}): {r.rationale}")
        e = p.enrichment
        if e.get("status") and e["status"] != "out_of_scope":
            filled = [f for f, s in e.get("sources", {}).items() if s != "call"]
            mock = " [MOCK DATA]" if e.get("is_mock") else ""
            rows.append(f"{'':26}   ENRICH {e['status']} via {e.get('provider')}{mock} @ {e.get('domain')}"
                        + (f": +{', '.join(filled)}" if filled else ""))
        if p.crm.get("action"):
            deal = p.crm.get("deal") or {}
            lab = (f" | deal {deal.get('deal_name') or deal.get('deal_id')}: "
                   f"{', '.join(deal.get('labels') or []) or 'no label'}") if deal else ""
            rows.append(f"{'':26}   HUBSPOT {p.crm['action']} contact {p.crm.get('contact_id') or '(new)'}"
                        f" ({len(p.crm.get('properties_written') or {})} props){lab}")

    for r in env.unnamed_roles:
        rows.append(f"  unnamed: {r.description}")
    s = env.stats
    br, ce, hs = s.get("buying_roles"), s.get("contact_enrichment"), s.get("hubspot_sync")
    if br:
        rows.append(f"[agent 2] buying roles: {br.get('role_counts') or br.get('skipped')} "
                    f"(scored {br.get('scored_people')} buyer-side people)")
    if ce:
        rows.append(f"[agent 3] enrichment ({ce['provider']}): anchor={list(ce['anchor_domains'])} "
                    f"in_scope={ce['in_scope']} enriched={ce['enriched']} not_found={ce['not_found']} errors={ce['error']}")
    if hs:
        deal = hs.get("deal") or {}
        rows.append(f"[agent 3] hubspot [{hs['mode'].upper()}]: contacts={hs['contacts']} "
                    f"deal={deal.get('name') or deal.get('id') or 'none found (labels skipped)'} "
                    f"labels_created={hs['labels_created']} props={hs['properties_created']}")
    rows.append(
        f"[agent 1] people={s.get('total_people')} external={s.get('external')} mentioned_only={s.get('mentioned_only')} "
        f"not_on_list={s.get('not_on_attendee_list')} needs_review={s.get('needs_review')} next_stage={env.next_stage}"
    )
    latest = {}  # only report a stage's error if its most recent attempt failed
    for h in env.history:
        if h.status != "skipped":
            latest[h.stage] = h
    for h in latest.values():
        if h.status == "error":
            rows.append(f"ERROR in {h.stage}: {h.detail}")
    return "\n".join(rows)


def failed(env: HandoffEnvelope) -> bool:
    latest = {}
    for h in env.history:
        if h.status != "skipped":
            latest[h.stage] = h
    return any(h.status == "error" for h in latest.values())
