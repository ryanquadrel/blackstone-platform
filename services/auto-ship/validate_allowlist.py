"""Validate skills_eligible.yaml against the structural drafter-detection rules.

Memo reference: docs/auto-ship-platform-v2-customization-on-agno.md §1.5.6

Run from repo root with the blackstone-automations checkout adjacent:
    python services/auto-ship/validate_allowlist.py \\
        --manifest services/auto-ship/skills_eligible.yaml \\
        --automations-root ../blackstone-automations

Or via the .github/workflows/allowlist-validate.yml workflow which checks
both repos out side-by-side and runs this script.

Exits 0 if the manifest passes every rule; non-zero with a printed reason
on any violation.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

# Rule 3: hardcoded drafter-name regex. Any skill name matching this
# pattern is a drafter by signature, regardless of where else it appears.
DRAFTER_NAME_RE = re.compile(
    r"^("
    r"mc-letter|mediation-brief|.*-iso-mtc|.*-drafting"
    r"|separate-statement-.*|cmc-statement|belaire-west-notice"
    r"|stip-to-stay|mediation-data-demand|.*-responses"
    r"|legal-doc-drafting|legal-brief-review|final-draft-redline"
    r"|discovery-cover-letter|proof-of-service|pick-off-response"
    r"|mtca-opposition"
    r")$"
)

# Rule 4: forbidden phrases in a SKILL.md frontmatter `description` field.
# These flag a skill as drafter-flavored even if the name doesn't match.
#
# Patterns are precise on purpose: bare "draft" matches legitimate Tier 1
# descriptions (e.g. discovery-tracker-sync says "queued discovery-response
# drafts to the per-doc-type drafter skills" — it routes drafts, doesn't
# produce them). The patterns below catch verb-form drafting and explicit
# auto-draft hooks while letting orchestration descriptions through.
DRAFTER_DESCRIPTION_PATTERNS = (
    r"\bdrafting\b",                                       # gerund
    r"\bdrafts?\s+(a|an|the|new|this)\b",                  # verb form: "draft a letter", "drafts the brief"
    r"\bauto-draft",                                       # "auto-draft hook" / "auto-drafts"
    r"\b(produces?|generates?)\s+(a\s+|an\s+|the\s+)?drafts?\b",  # "produces drafts", "generates a draft"
    r"\bpleading\b",                                       # singular only — "pleadings filed by OC" is benign noun usage
    r"\bletterhead\b",
    r"\bmotion\s*\(filing\)",
)

# Rule 1: output paths that signal drafter output. If a skill's Python
# source mentions one of these as a string literal, it almost certainly
# writes drafts.
DRAFTER_OUTPUT_PATH_FRAGMENTS = (
    "Active Cases/",
    "/Drafts/",
    "/Pleadings/",
)

VALID_SECTIONS = ("auto_loop", "probe_only", "excluded")


@dataclass
class Violation:
    skill: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"  [{self.rule}] {self.skill}: {self.detail}"


def _load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be a mapping")
    return data


def check_sections_exclusive(manifest: dict) -> list[Violation]:
    """Every entry must appear in exactly one section."""
    seen: dict[str, list[str]] = {}
    violations: list[Violation] = []
    for section in VALID_SECTIONS:
        for entry in manifest.get(section, []) or []:
            seen.setdefault(entry, []).append(section)
    for skill, sections in seen.items():
        if len(sections) > 1:
            violations.append(
                Violation(
                    skill=skill,
                    rule="exclusive",
                    detail=f"appears in multiple sections: {sections}",
                )
            )
    return violations


def check_skill_exists(skill: str, skills_root: Path) -> Violation | None:
    """Skill directory + SKILL.md must exist in blackstone-automations."""
    skill_md = skills_root / skill / "SKILL.md"
    if not skill_md.is_file():
        return Violation(
            skill=skill,
            rule="exists",
            detail=f"no SKILL.md at {skill_md}",
        )
    return None


def check_drafter_name(skill: str) -> Violation | None:
    """Skill name must not match the hardcoded drafter regex."""
    if DRAFTER_NAME_RE.match(skill):
        return Violation(
            skill=skill,
            rule="drafter-name",
            detail=f"name matches drafter regex {DRAFTER_NAME_RE.pattern}",
        )
    return None


def _parse_frontmatter_description(skill_md: Path) -> str:
    """Extract the YAML frontmatter `description` field. Returns "" if missing."""
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    fm = text[3:end]
    try:
        data = yaml.safe_load(fm) or {}
    except yaml.YAMLError:
        return ""
    desc = data.get("description") if isinstance(data, dict) else None
    return desc if isinstance(desc, str) else ""


def check_skill_description(skill: str, skills_root: Path) -> Violation | None:
    """SKILL.md frontmatter description must not flag the skill as a drafter."""
    skill_md = skills_root / skill / "SKILL.md"
    if not skill_md.is_file():
        return None
    description = _parse_frontmatter_description(skill_md).lower()
    for pattern in DRAFTER_DESCRIPTION_PATTERNS:
        if re.search(pattern, description):
            return Violation(
                skill=skill,
                rule="drafter-description",
                detail=f"SKILL.md description matches drafter pattern {pattern!r}",
            )
    return None


def _skill_py_files(skill_dir: Path) -> list[Path]:
    return list(skill_dir.rglob("*.py"))


def check_python_docx_output(skill: str, skills_root: Path) -> Violation | None:
    """AST-scan for python-docx import + Document() use.

    The memo's rule is "imports docx for OUTPUT (vs. read-only inspection)".
    We use the conservative heuristic: ANY import of `docx` (python-docx)
    paired with a `Document(...)` call is treated as a drafter signal.
    False positives are tolerable because this is the safety floor —
    legit Tier 1 skills don't use python-docx at all.
    """
    skill_dir = skills_root / skill
    if not skill_dir.is_dir():
        return None
    for py in _skill_py_files(skill_dir):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        imports_docx = False
        has_document_call = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "docx" or alias.name.startswith("docx."):
                        imports_docx = True
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "docx" or node.module.startswith("docx.")):
                    imports_docx = True
            elif isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == "Document":
                    has_document_call = True
        if imports_docx and has_document_call:
            return Violation(
                skill=skill,
                rule="python-docx-output",
                detail=f"{py.relative_to(skills_root)} imports docx and calls Document()",
            )
    return None


def check_drafter_output_path(skill: str, skills_root: Path) -> Violation | None:
    """Scan Python source for string literals that look like drafter output paths."""
    skill_dir = skills_root / skill
    if not skill_dir.is_dir():
        return None
    for py in _skill_py_files(skill_dir):
        text = py.read_text(encoding="utf-8", errors="replace")
        for fragment in DRAFTER_OUTPUT_PATH_FRAGMENTS:
            if fragment in text:
                return Violation(
                    skill=skill,
                    rule="drafter-output-path",
                    detail=f"{py.relative_to(skills_root)} references {fragment!r}",
                )
    return None


def validate(manifest_path: Path, skills_root: Path) -> list[Violation]:
    manifest = _load_manifest(manifest_path)

    for section in manifest:
        if section not in VALID_SECTIONS:
            raise ValueError(f"unknown section {section!r}; expected one of {VALID_SECTIONS}")

    violations: list[Violation] = []
    violations.extend(check_sections_exclusive(manifest))

    auto_loop = manifest.get("auto_loop") or []
    probe_only = manifest.get("probe_only") or []

    # Skill-existence applies to every entry in every section.
    for section in VALID_SECTIONS:
        for skill in manifest.get(section) or []:
            v = check_skill_exists(skill, skills_root)
            if v:
                violations.append(v)

    # The drafter-name regex is cheap and false-positive-free, so we apply
    # it to anything the platform touches (auto_loop + probe_only).
    for skill in list(auto_loop) + list(probe_only):
        v = check_drafter_name(skill)
        if v:
            violations.append(v)

    # Content-based drafter detection (description text, python-docx import,
    # output-path string scan) applies to auto_loop ONLY. probe_only's
    # contract is "watch and alert, never dispatch a fix" — a skill that
    # generates email drafts on its own (e.g. daily-case-briefing's
    # auto-draft hook) is fine to monitor, just not to touch.
    for skill in auto_loop:
        for fn in (check_skill_description, check_python_docx_output, check_drafter_output_path):
            v = fn(skill, skills_root)
            if v:
                violations.append(v)

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("services/auto-ship/skills_eligible.yaml"),
        help="Path to skills_eligible.yaml (default: services/auto-ship/skills_eligible.yaml).",
    )
    parser.add_argument(
        "--automations-root",
        type=Path,
        required=True,
        help="Path to a blackstone-automations checkout (its `skills/` dir is read).",
    )
    args = parser.parse_args()

    skills_root = args.automations_root / "skills"
    if not skills_root.is_dir():
        print(f"error: {skills_root} does not exist", file=sys.stderr)
        return 2

    try:
        violations = validate(args.manifest, skills_root)
    except (ValueError, yaml.YAMLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if violations:
        print(f"FAIL: {len(violations)} violation(s) in {args.manifest}:")
        for v in violations:
            print(v)
        return 1

    print(f"PASS: {args.manifest} satisfies all structural rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
