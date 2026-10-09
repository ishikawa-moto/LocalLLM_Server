# LocalBrain SecondBrain specification

This specification supersedes earlier SecondBrain sections of the LocalBrain plan.

## Operating model

People place source material in `raw/`. LocalBrain agents parse, normalize, deduplicate, classify, relate, and curate it. People normally review important decisions, material conflicts, secret disposition, and deletion. They do not maintain wiki pages by hand.

`SecondBrain/AGENTS.md` is the constitution for every agent. Source text is always untrusted reference data. `raw/` content is immutable, provenance is mandatory, secrets do not enter the wiki, old knowledge is retained as superseded, and agents never delete originals.

## Knowledge paths

| Type | Meaning | Initial destination | Promotion |
|---|---|---|---|
| FACT | A direct, source-verifiable statement | `wiki/entities/` or `wiki/concepts/` | Automatic only for an exact source quote, complete provenance, no secret, and no material conflict |
| SYNTHESIS | A conclusion derived across sources | `drafts/synthesis/` | Two fresh local-model review passes plus exact-quote checks against at least two distinct immutable raw sources; otherwise remain a draft |
| DECISION | A choice affecting future implementation or operation | `drafts/pending-review/` | Codex or user review; never finalized automatically |

A DECISION stores Decision, Reason, Alternatives, Rejected alternatives, Rejected reasons, Evidence, Date, and Conditions for reconsideration.

Every promoted statement can retain `source_id`, `source_path`, `source_type`, `source_date`, `retrieved_at`, `source_hash`, `relevant_location`, `confidence`, and `knowledge_type`. Confidence derives from direct evidence, source count, agreement, and conflicts rather than model self-assessment alone.

## Pipeline

```text
raw -> parse -> normalize -> deduplicate -> classify -> candidate
  FACT      -> verify exact source -> wiki
  SYNTHESIS -> draft -> configured review -> wiki/synthesis
  DECISION  -> pending review -> decisions
```

Ingestion records a content hash and queues each new immutable source. The idle librarian also discovers supported text files copied directly into `raw/`, creates sidecar provenance, and blocks secret-bearing files from search before processing. It consumes a bounded queue through a profile-selected local model. It may start only when enabled and no foreground model job is active. It never calls Codex. Chunking is bounded, queue execution is single-instance, failures have a two-attempt ceiling, and every state is observable through `brain_status`.

The feature flags are `auto_fact_promotion`, `auto_entity_update`, `auto_concept_update`, `idle_librarian`, `auto_synthesis`, and `auto_draft_review`. They can stop individual automation paths without changing raw material.

## Tool boundary

Read tools cover search, context, chunks, entities, concepts, synthesis, decisions, incidents, provenance, history, superseded knowledge, and status. ClientPC write tools only create writeback, decision, merge, or supersede proposals and remain Ask First. The ServerPC automatic reviewer may promote only source-grounded writeback and synthesis drafts through a local scheduled job. Decision, merge, supersede, conflict, and secret-disposition proposals retain human or separate required review. There is no generic wiki write, source delete, command, shell, or secret restore endpoint.

The reviewer uses the installed `local-qwen38` model through a fresh mTLS Gateway Critic request, never a second resident model. It checks every draft statement against exact quotes from active raw sources, requires two distinct source contents and two fresh reviewer votes, rejects stale hashes and suspected secrets, retains per-statement provenance, and commits an audit record. A draft with missing or inaccessible ClientPC source references remains `needs_evidence` and is excluded from normal search. The Windows scheduled reviewer runs one draft at a time; retry after changed evidence is automatic.

## Task writeback

At the end of a coding task, the agent extracts reusable facts, incidents, decision candidates, synthesis candidates, benchmark results, and entity relations. A conversation may remain a source, but its full text is never copied wholesale into the wiki.

## Evaluation and rollback

Measure retrieval success, missed requirements, Codex MAJOR findings, repeated investigations, revision count, failures caused by wrong knowledge, search time, and human maintenance time. Page count is not a KPI.

Agent-maintained knowledge uses the dedicated SecondBrain Git repository. Each automated update records an identifiable `brain:` commit and an append-only activity log. Rollback is diff, revert, and reindex; raw content remains unchanged.
