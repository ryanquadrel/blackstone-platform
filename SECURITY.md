# Security & Confidentiality Policy

**This repository is PUBLIC.** It is a fork of
[agno-agi/agent-platform-railway](https://github.com/agno-agi/agent-platform-railway)
and is scoped to host **only** the Agno-based auto-ship platform code. Because a
fork of a public upstream cannot itself be made private, everything committed
here is world-readable — permanently, including anything later "deleted," since
git history persists.

## Hard rule: never commit confidential content here

Do **not** commit, in current files **or** history:

- **Credentials of any kind** — API keys, JWTs, Supabase keys
  (`sb_secret_*` / `sb_publishable_*` / legacy `eyJ…` anon/service tokens),
  GitHub PATs, Slack tokens, private keys, passwords.
- **Firm infrastructure identifiers** — Supabase project refs, internal
  hostnames, or any value that lets an outsider reach firm systems.
- **Client or matter content** — party names, case numbers, settlement figures,
  privileged communications, work product, or any attorney-client material.

Secrets belong in environment variables / the runtime `.env` (git-ignored) or in
`~/.blackstone-secrets/` on the host — never inline. Use placeholders in docs and
examples (e.g. `db.<ref>.supabase.co`, `${SUPABASE_KEY}`).

## How the rule is enforced

A CI gate (`.github/workflows/secret-scan.yml`, config in `.gitleaks.toml`) runs
[gitleaks](https://github.com/gitleaks/gitleaks) on every push and pull request.
It scans the **entire git history** and **fails the build** on any match —
gitleaks' built-in credential rules plus firm-specific rules (Supabase refs,
privilege markers, California case-number patterns). This binds every committer
equally: humans, Claude, and Codex.

The scanner catches patterned content. It **cannot** catch free-text confidential
prose (e.g. a client's name in a paragraph). Treat the rule above as binding
regardless of what the scanner can mechanically detect, and rely on CODEOWNERS
review as the second layer.

## If a secret is committed anyway

1. **Rotate the exposed credential immediately** — assume it is compromised the
   moment it lands in a public repo.
2. Scrub it from history (`git filter-repo`) and force-push.
3. Note the rotation in the firm's incident record.
