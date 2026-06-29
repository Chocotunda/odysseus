# Relationship-Memory Layer — Decision Record

**Date:** 2026-06-29
**Status:** Direction settled; Slice 1 (server foundation) spec'd and approved for build.
**Parent direction:** `docs/ai-context/2026-06-22-life-os-direction-decision-record.md` (Odysseus = the brain / canonical graph). This record is downstream of it.
**Related:** `docs/superpowers/specs/2026-06-29-attendee-relationship-foundation-design.md` (Slice 1 spec); the management-hub entity-graph work (Link spine, reverse-Link aggregation).

## The idea

Grow the People graph from a hand-curated list into a **private relationship-memory layer**: a lightweight, local-first CRM that no SaaS can give you because none owns your whole graph. Calendar attendance, email history, and notes all **converge on an email-keyed `Person`**; the Person page *is* the dossier (it's already a reverse-Link aggregation); an LLM later reads that dossier to surface history and prep you before you interact with someone again ("you're meeting Wiggert in 30 min — last 3 times you discussed X, you owe him Y").

This started from a narrow ask — "Wiggert should show up in his real calendar meetings" — and the right response is to build the *ingestion seed* for the larger layer rather than a one-off meeting widget.

## Why it's feasible (the bones already exist)

- **The dossier is nearly free.** A Person page in the hub is a reverse-Link aggregation — everything linking to that node. Proven by the People/Area surfaces. Once a Person accrues edges (`attended_by` meetings, `about` notes, tasks, later emails), their page *is* their history; we don't build a CRM screen, the graph converges on the node.
- **Multiple ingestion sources are already wired.** Calendar (Slice 1). **Email IMAP already exists** (`EmailAccount`, `mcp_servers/email_server.py`) → matching sender/recipient address to `Person.email` gives "every thread with this person" (Slice 3), same pattern.
- **The LLM layer already exists.** `agent_loop` + RAG/memory + `deep_research` can summarize a Person's accumulated graph (Slice 4). This is exactly the "initiative/reach layer" the Hermes eval named as the missing piece — and it gets better the more graph we quietly accumulate.

## The model we're mirroring (harvested from the user's Tana, 2026-06-29)

Via `tana-local` MCP — a calendar-sourced `#Meeting` in the user's Tana:

```
Vervolggesprek … #Meeting
  Attendees:    [Wiggert Loonstra #Person]  [npdeweerd@gmail.com #Person]  [Rob Tijkorte #Person]
  Date:         Wed, 27 May, 11:00 → 12:00
  Source:       Calendar          ← provenance
  Location:     Microsoft Teams-vergadering
  Meeting link: https://teams.microsoft.com/…
```

Key facts: `Meeting.Attendees` is an **Instance of `#Person`** (a reference → bidirectional for free); `Person` has an **Email** field (the match key); a **`Source: Calendar`** field marks auto-synced rows; unmatched attendees (`npdeweerd@gmail.com`) were **promoted to their own Person node**, not dropped. Odysseus maps cleanly: `CalendarEvent` = the meeting; `Person.email` = the key; `Meeting —attended_by→ Person` = the reference; a `Person.source` flag = `Source: Calendar`.

## Decisions

1. **Identity = email.** Normalized (strip `mailto:`, lowercase, trim). `Person.email` is the dedup/match key. One email → one Person, across all meetings and re-syncs. (Multi-email people / merge UI is a known later concern — see Open questions.)
2. **Auto-create a Person per attendee email** (chosen over match-only / keep-raw) — maximum graph completeness, matching the Tana model.
3. **Tier by provenance** — this is the unlock that makes "ever-growing" = latent value, not clutter. New `Person.source` column: `manual` (default, hand-curated) vs `calendar` (auto-created). Auto-created people are dimmable / filterable / bulk-cleanable, and can later "graduate" to curated once they recur. Storage cost is irrelevant at SQLite scale; the only real cost is UI noise and identity fragmentation, both handled by the tier + email key.
4. **Self-skip.** The owner's own addresses (`ODYSSEUS_ADMIN_USER` email + connected `EmailAccount` addresses) never become a Person and never self-link.
5. **One `attended_by` fact.** Calendar-sourced `Meeting —attended_by→ Person` shares the same edge tuple as the meeting-note-sourced one (`src/meeting_notes.py`). They dedup to a single edge — a person attended regardless of how we learned it. Provenance lives on the Person, not the edge.
6. **Capture is gated and reversible.** A **global feature flag** (default **OFF** — safe rollout) plus a **per-calendar `link_attendees` boolean** (default **ON**). Flip the global flag on → all calendars participate → mute noisy ones (kids/family) individually. Capture-everything for the vision, but controllable and undoable.
   - **⚠️ Before enabling (`ODYSSEUS_CALENDAR_ATTENDEE_LINKING=1`):** self-skip gathers the owner's addresses from connected `EmailAccount`s (IMAP, e.g. Gmail) + the owner username + `ODYSSEUS_OWNER_EMAILS`. Addresses that are **not** an EmailAccount — notably the owner's **iCloud** address that appears as organizer/attendee on iCloud calendar events — are NOT auto-detected. Set `ODYSSEUS_OWNER_EMAILS` to a comma-separated list of *all* your own addresses first, or you'll be auto-created as your own `source=calendar` Person and self-linked to your meetings (harmless + tiered, but avoidable).
7. **Additive in the foundation.** Slice 1 adds attendance edges; it does not yet remove an `attended_by` edge when an attendee drops off a re-synced event (needs attendee-diffing / stored prior state). Rare; deferred.

## Phased program (each slice independently shippable)

- **Slice 1 — server foundation (NOW, server-only):** CalDAV `ATTENDEE` → email-keyed Person match/create (`source` tier + self-skip + dedup) → `attended_by` edges; expose attendees on the meeting-detail API. Verifiable via API. Spec: `2026-06-29-attendee-relationship-foundation-design.md`.
- **Slice 2 — Tide display:** Person page shows real attended meetings (`attended_by`, distinct from note-backed); meeting rows show their roster ("with: …"). Builds on the reverse-Link spine; provenance lets the UI dim auto-people.
- **Slice 3 — email → Person:** match IMAP sender/recipient addresses to `Person.email`, link threads; auto-create with `source=email`. Turns dossiers into real interaction history.
- **Slice 4 — LLM surfacing:** meeting-prep briefs, "history with this person," draft-with-context, over the accumulated graph. (Pull current model/pricing specifics at build time.)

Stopping after any slice leaves a coherent product; Slice 1 alone already delivers "Wiggert in his real meetings" (API-visible).

## Open questions (revisit at the relevant slice)

- **Multi-email / merge.** One person with several addresses fragments into several Persons. Slice 1 keys on a single email; a merge path (and possibly secondary-email storage) is a later concern.
- **Signal vs noise tail.** Even tiered, no-reply@/newsletter/one-off recruiter contacts accumulate. A "graduate on ≥N interactions" heuristic or a periodic prune is a future refinement, not a foundation requirement.
- **Per-attendee RSVP/role.** PARTSTAT (accepted/declined/tentative) and organizer-vs-attendee are richer than a bare edge; deferred until Slice 2/4 want them (likely an `EventAttendee` table then).
- **Context/Area tagging of auto-people.** Tana files people under Work/Personal/KM. Odysseus has Areas but no calendar→Area mapping; tagging auto-people by their calendar's context is deferred (would build on the per-calendar gating).
- **Retention/privacy.** Storing history of external people is the user's own local data, but a retention/forget policy is worth a later look.
