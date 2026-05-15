# Auto-ship eligibility manifest

The safety boundary that gates which Blackstone Law skills the auto-ship
platform may operate on. Memo reference: [`docs/auto-ship-platform-v2-customization-on-agno.md`](https://github.com/ryanquadrel/blackstone-automations/blob/main/docs/auto-ship-platform-v2-customization-on-agno.md) §1.5.3 + §1.5.6.

## Files

| File | Purpose |
|---|---|
| `skills_eligible.yaml` | The manifest itself. Three sections: `auto_loop`, `probe_only`, `excluded`. |
| `validate_allowlist.py` | Structural validator. Run by GHA on every PR touching the manifest. |
| `test_validate_allowlist.py` | pytest unit tests for each validator rule. |

## Tiers

- **`auto_loop`** — full Detect → Improve → Hill Climb → Review cycle. The platform may open issues, dispatch fixes, and merge PRs for these skills (subject to per-stage HITL gates).
- **`probe_only`** — the platform watches for failures and surfaces a Telegram alert, but does NOT dispatch any auto-fix.
- **`excluded`** — never touched. Drafter skills and anything attorney-judgment-bearing belong here.

An entry must appear in **exactly one** section. The validator enforces this.

## Structural drafter detection

`auto_loop` and `probe_only` entries pass through five checks. If any check fails, the validator exits non-zero and CI blocks the PR.

| # | Rule | What it catches |
|---|---|---|
| 1 | `exists` | Skill directory + SKILL.md must exist in `blackstone-automations/skills/<name>/`. Catches typos and stale entries. |
| 2 | `exclusive` | Entry appears in exactly one section. Catches accidental promotion-without-removal. |
| 3 | `drafter-name` | Skill name must not match the hardcoded drafter regex (e.g. `*-iso-mtc`, `*-drafting`, `*-responses`, `mc-letter`, `mediation-brief`, …). Catches pattern-matched drafter additions. |
| 4 | `drafter-description` | SKILL.md frontmatter `description` field must not match drafter idiom patterns (`drafting`, `draft a/the/new/this`, `auto-draft`, `produces drafts`, `pleading`, `letterhead`, `motion (filing)`). Bare `draft` is NOT matched — Tier 1 orchestration skills legitimately mention "drafts" as a noun referring to other skills' output. |
| 5 | `python-docx-output` | AST scan of the skill's Python files. If the skill imports `docx` (python-docx) AND calls `Document(...)`, fail. Catches drafters that produce `.docx` output regardless of name or description. |
| 6 | `drafter-output-path` | String-literal scan for `Active Cases/`, `/Drafts/`, `/Pleadings/`. Catches code that writes drafter output paths even without python-docx. |

**Scoping:** rules 1, 2, and 3 apply to every entry in every section. Rules 4–6 (content-based detection) apply to `auto_loop` ONLY. The `probe_only` tier's contract is "watch and alert; never dispatch a fix" — a skill that quietly auto-drafts an email (e.g. `daily-case-briefing`'s cancellation-followup hook) is fine to monitor, just not to touch.

Conservative by design: false positives are tolerable on the safety side (a Tier 1 sync skill that legitimately needed `python-docx` could be hand-reviewed and added to a future "trusted-docx" allowlist). False negatives — a drafter slipping into `auto_loop` — are not.

## Why a regex AND structural checks AND CODEOWNERS

Defense in depth. The memo (Codex round 3 P2 #1) specifically noted that a keyword-scan would fail every Tier 1 skill, because dashboard/sync skills legitimately mention `motion`, `Bates`, `pleading` while talking about cases. The structural rules solve that — they care about what the code DOES (output paths, python-docx import, frontmatter description), not what the SKILL.md body says about case work.

CODEOWNERS adds a human gate on top: any PR editing the manifest requires `@ryanquadrel` review (configured in `/CODEOWNERS`).

## Running locally

```bash
# From the blackstone-platform repo root, with blackstone-automations
# checked out as a sibling directory:
python services/auto-ship/validate_allowlist.py \
    --manifest services/auto-ship/skills_eligible.yaml \
    --automations-root ../blackstone-automations
```

Tests:

```bash
cd services/auto-ship
pytest -q test_validate_allowlist.py
```

## CI

`.github/workflows/allowlist-validate.yml` runs the full check on every PR that touches the manifest, the validator, the tests, or the workflow itself. It needs a `BLACKSTONE_AUTOMATIONS_READ` repository secret — a fine-grained PAT with `contents:read` on `ryanquadrel/blackstone-automations`. Same-account access; no fork/cross-org PAT issues.

## Adding a skill to `auto_loop`

1. Verify it isn't a drafter (review the rules above).
2. Open a PR adding the entry to `skills_eligible.yaml`.
3. CI runs the validator. If structural checks fail, fix the categorization or fix the skill (rare).
4. Ryan reviews per CODEOWNERS.
5. Merge.

## Removing a skill

Move it to `excluded` rather than deleting. The audit trail lives in git history; the explicit list documents the firm's stance.
