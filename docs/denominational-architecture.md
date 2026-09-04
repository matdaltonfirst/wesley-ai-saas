# Multi-denominational theology architecture

Wesley AI serves churches from different denominations from one codebase. Each
church's theological answers come from exactly one denominational profile, and a
church assigned one denomination must never receive another denomination's prompt
instructions or retrieved denominational sources.

```
USER QUESTION
  → Wesley AI Core                  denominationally neutral behaviour
  → Selected Denominational Profile exactly one, chosen by the church
  → Approved Local Church Context   this congregation's own approved material
  → ANSWER
```

## Layers

### 1. Wesley AI Core — `helpers.py`

Neutral. Controls behaviour only, never theology:

- helpful ministry-assistant behaviour
- accuracy and honesty (no invented doctrine, quotations, policy, positions, URLs)
- privacy and safety
- citation behaviour
- pastoral referral
- language matching
- treatment of time-sensitive information
- distinguishing church documents, calendars, sermons, and approved Q&A
- the authority order below

The core never calls a church United Methodist and never describes the assistant
as Wesleyan. Bot branding and denominational identity are separate concepts: a
church may keep the name Wesley without being Wesleyan.

The platform-wide, super-admin-editable prompt (`SystemPrompt` row 1) is shared by
every tenant and was originally authored for United Methodist churches. It is
withheld from a church whose denomination differs when it mentions terminology
owned by another profile — see `helpers._platform_prompt_for` and
`denominations.registry.contains_foreign_denomination_text`.

### 2. Denominational profile — `denominations/`

The selected profile is the only place a denomination is named. It controls
theological identity, scripture and theological authority, salvation, baptism,
Communion, membership, clergy and ordination, marriage and sexuality, governance
and polity, women in ministry, social teaching, other distinguishing doctrines,
and official terminology.

| File | Role |
|------|------|
| `base.py` | `DenominationProfile`, `KnowledgeSection`, prompt/chunk rendering |
| `registry.py` | key → profile, validation, options, foreign-term detection |
| `retrieval.py` | `load_denomination_chunks`, `score_denomination_chunks` |
| `local_practice.py` | local-practice schema, validation, prompt rendering |
| `matrix.py` | reusable isolation question matrix and profile markers |
| `umc.py` `sbc.py` `gmc.py` `non_denominational.py` `custom.py` | the profiles |

`umc_facts.py` remains as a thin back-compatible shim over `denominations/umc.py`.

### 3. Approved local church context

Per-church, and never mistaken for denominational teaching:

- approved Q&A (`QnAPair`) and text snippets (`TextSnippet`)
- uploaded documents and crawled website content
- structured local practices (`churches.local_practices`, validated JSON)
- a local statement of faith (`churches.statement_of_faith`)

## Authority order

Highest first. Stated in the assembled prompt itself:

1. Wesley AI core safety and truthfulness rules
2. Verified local factual information
3. Pastor-approved local practice or approved Q&A
4. Selected denominational profile
5. General model knowledge

Local practice may clarify or narrow what a congregation does. It must never
silently rewrite objective denominational facts.

> A church may say: "This congregation's pastor does not perform same-sex
> weddings."
> The assistant must **not** turn that into: "The denomination prohibits same-sex
> weddings."

When local practice differs from or narrows a denominational default, the answer
distinguishes what this congregation practices from what the denomination
officially teaches or permits. When sources conflict and the conflict cannot be
resolved safely, the assistant names the uncertainty and recommends contacting
church leadership — it never silently picks or blends positions.

## Adding a denomination

1. Add `denominations/<key>.py` exporting `PROFILE`.
2. Add the module to `_MODULES` in `denominations/registry.py`.
3. Add its distinctive markers to `PROFILE_MARKERS` in `denominations/matrix.py`.

No route, prompt-assembly, or retrieval change is needed, and no route contains a
denomination conditional.

## Profiles awaiting reviewed content

`sbc` and `gmc` are structurally complete and theologically empty. Nothing in this
repository has been reviewed for their doctrine, polity, confessional documents,
publication dates, or official URLs, and the platform must not invent them. Those
profiles carry no knowledge chunks and instruct the model to avoid definitive
denominational claims, answering from approved local material or referring to
church leadership.

To complete one: add reviewed `KnowledgeSection` entries in the platform's own
words with verified source URLs, replace the identity/doctrinal/polity guidance,
populate `source_urls`, then set `content_status = REVIEWED` and bump `VERSION`.
Never copy content between profiles — shared heritage is not evidence of a shared
current position.

`denominations/matrix.py` also holds `ALLOWED_CROSS_REFERENCES`: the narrow set of
factual, doctrine-free mentions one profile may make of another denomination's
existence. Never add an entry there to make a leak test pass.

## Data model

`churches.denomination` (stable internal key, default `umc`),
`denomination_profile_version`, `denomination_updated_at`, `local_practices`,
`statement_of_faith`. Migrations are additive and live in `app._run_migrations`;
existing churches backfill to `umc` so no current customer experiences a
theological change.

## Deterministic isolation tests

`tests/test_denominations.py` asserts separation against assembled prompts,
retrieval candidates, and citations — never against live model output. The reusable
question matrix is `denominations.ISOLATION_QUESTIONS`, and every profile also
declares its own `evaluation_questions`.
