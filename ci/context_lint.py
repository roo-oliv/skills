#!/usr/bin/env python3
"""Gate on the CONTEXT SURFACES an agent reads: the instructions file, rules, skills and docs.

What it enforces (full table + rationale: docs/context-toolkit.md; the directive an author follows
lives in the vendored `.claude/rules/context.md`):

  * absolute ceilings  — instructions-file line count, the always-on package in bytes, rule frontmatter;
  * ratchets           — a rule already over its ceiling may not grow;
  * reference checks   — every path, markdown link, migration version, source symbol and premise
                         `**Tests:**` class named in those surfaces must still resolve;
  * premise ids        — every premise carries a unique `**Id:** p-xxxxxxxx` (C17), so the handle
                         `premise.py <id>` resolves to a single section;
  * premises index     — the committed `premises-index.md` equals what this script generates (C15);
                         `--write-indices` regenerates all of them.

DELTA SEMANTICS, BY STRING. Reference checks compare two trees: head (the WORKTREE, untracked files
included, so the gate can run before a commit) and base (`git merge-base $CONTEXT_LINT_BASE_REF HEAD`).
A reference that does not resolve on head is classified by its STRING alone — no file/line identity,
so a rename, a split or a reflow of the surrounding doc changes nothing:

  new         the string is absent from the base surfaces          -> FAIL (WARN for the softer checks)
  regression  present on base AND it resolved there                -> FAIL
  legacy      present on base and already broken there             -> WARN

So the gate is GREEN BY CONSTRUCTION on the day it lands: everything already broken is grandfathered
as a warning, and only what this branch breaks or adds fails. Ephemeral docs, generated indices and
the excluded globs are never surfaces; fenced blocks are ignored everywhere.

Every finding is printed and counted, but GitHub ANNOTATIONS are emitted only for files this branch
touched — otherwise every PR would carry the whole legacy backlog as inline comments on code its
author never opened. With no base ref, everything is annotated.

Run it locally exactly as CI does:

    python3 .github/scripts/context_lint.py --base origin/main

Without a base ref the absolute ceilings still fail, and every delta check degrades to a warning.
Paths, globs and ceilings come from `docs/agents/skills-config.md` (see `skills_config.py`), never
from a constant in here. Python 3 stdlib only; `git` is the one external tool.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skills_config  # noqa: E402

FAIL = "FAIL"
WARN = "WARN"

# A measure, not a ceiling — changing it regenerates every index (C15 then fails until they are committed).
INDEX_ESSENCE_CHARS = 160


# ── surfaces ──────────────────────────────────────────────────────────────────────────────────────


class Surfaces:
    """The predicates that decide what this linter looks at, all derived from the repo's config."""

    def __init__(self, config: skills_config.Config):
        self.config = config
        self.root = tuple(config.root_surfaces)
        self.rules_dir = config.rules_dir.rstrip("/") + "/"
        self.skills_dir = config.skills_dir.rstrip("/") + "/"
        self.docs_root = config.docs_root.rstrip("/") + "/"

    def is_surface(self, path: str) -> bool:
        if path in self.root:
            return True
        if not path.endswith(".md") or self.config.is_excluded(path):
            return False
        if path.startswith(self.rules_dir):
            return True
        if path.startswith(self.skills_dir):
            return True
        if self.is_index(path):
            return False
        if path.startswith(self.docs_root):
            return not self.config.is_ephemeral(path)
        return False

    def is_rule(self, path: str) -> bool:
        return path.startswith(self.rules_dir) and path.endswith(".md")

    def is_skill(self, path: str) -> bool:
        return (
            path.startswith(self.skills_dir)
            and path.endswith("/SKILL.md")
            and not self.config.is_excluded(path)
        )

    def is_premises(self, path: str) -> bool:
        return self.config.is_premises(path)

    def is_index(self, path: str) -> bool:
        """The generated per-domain index — derived, so neither a surface (C6–C10) nor a premises file."""
        return self.config.is_premises_index(path)


# ── git plumbing ──────────────────────────────────────────────────────────────────────────────────


def git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False
    )
    return proc.stdout


IDENT_RE = r"(^|[^A-Za-z0-9_])[A-Z][A-Za-z0-9]+"


class Tree:
    """A snapshot of the repository — either the worktree or a commit."""

    def __init__(self, repo: str, rev: str | None, surfaces: Surfaces):
        self.repo = repo
        self.rev = rev
        self.surf = surfaces
        self.config = surfaces.config
        self.files = self._list_files()
        self._dirs = {""}
        for path in self.files:
            parts = path.split("/")
            for i in range(1, len(parts)):
                self._dirs.add("/".join(parts[:i]))
        self.surfaces = sorted(p for p in self.files if surfaces.is_surface(p))
        self.top_level = {p.split("/", 1)[0] for p in self.files}
        migration_dirs = tuple(d.rstrip("/") + "/" for d in self.config.migration_dirs)
        self.migrations = {os.path.basename(p) for p in self.files if migration_dirs and p.startswith(migration_dirs)}
        self._contents: dict[str, str] = {}
        self._identifiers: set[str] | None = None
        self._ignored: dict[str, bool] = {}

    def _list_files(self) -> set[str]:
        if self.rev is None:
            out = git(self.repo, "ls-files", "-z") + git(self.repo, "ls-files", "-z", "--others", "--exclude-standard")
            paths = {p for p in out.split("\0") if p}
            return {p for p in paths if os.path.exists(os.path.join(self.repo, p))}
        out = git(self.repo, "ls-tree", "-r", "-z", "--name-only", self.rev)
        return {p for p in out.split("\0") if p}

    def exists(self, path: str, directory: bool = False) -> bool:
        path = path.rstrip("/")
        if not path or path == ".":
            return True
        if directory:
            return path in self._dirs
        return path in self.files or path in self._dirs

    def read(self, path: str) -> str:
        if path not in self._contents:
            if self.rev is None:
                try:
                    with open(os.path.join(self.repo, path), encoding="utf-8", errors="replace") as handle:
                        self._contents[path] = handle.read()
                except OSError:
                    self._contents[path] = ""
            else:
                self._contents[path] = git(self.repo, "show", f"{self.rev}:{path}")
        return self._contents[path]

    def load_surfaces(self) -> None:
        """Read every surface in one `git cat-file --batch` pass (one fork instead of one per file)."""
        if self.rev is None:
            for path in self.surfaces:
                self.read(path)
            return
        missing = [p for p in self.surfaces if p not in self._contents]
        if not missing:
            return
        proc = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=self.repo,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdin and proc.stdout
        payload = "".join(f"{self.rev}:{p}\n" for p in missing).encode()
        proc.stdin.write(payload)
        proc.stdin.close()
        for path in missing:
            header = proc.stdout.readline().decode("utf-8", "replace").strip()
            parts = header.split()
            if len(parts) != 3:
                self._contents[path] = ""
                continue
            size = int(parts[2])
            blob = proc.stdout.read(size)
            proc.stdout.read(1)
            self._contents[path] = blob.decode("utf-8", "replace")
        proc.stdout.close()
        proc.wait()

    def load_ignored(self, paths: list[str]) -> None:
        """Batch-classify paths as gitignored — one `git check-ignore` call instead of one per ref."""
        pending = sorted({p for p in paths if p not in self._ignored})
        if not pending:
            return
        out = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=self.repo,
            input="\n".join(pending) + "\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ).stdout
        matched = {line.strip() for line in out.split("\n") if line.strip()}
        for path in pending:
            self._ignored[path] = path in matched

    def is_ignored(self, path: str) -> bool:
        return self._ignored.get(path, False)

    def identifiers(self) -> set[str]:
        """PascalCase tokens of every configured source glob UNION the extensionless basename of every file.

        With no `Source globs` configured the index is basenames only — enough for C13/C16 (a test class
        usually owns its file) and the reason C10 stays off until the repo declares its source globs.
        """
        if self._identifiers is None:
            found: set[str] = set()
            globs = self.config.source_globs
            if globs:
                cmd = ["git", "grep"]
                if self.rev is None:
                    cmd.append("--untracked")
                cmd += ["-h", "-o", "-E", IDENT_RE]
                if self.rev is not None:
                    cmd.append(self.rev)
                cmd += ["--", *globs]
                out = subprocess.run(
                    cmd, cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False
                ).stdout
                for token in out.split("\n"):
                    if not token:
                        continue
                    if not (token[0].isalnum() or token[0] == "_"):
                        token = token[1:]
                    if token:
                        found.add(token)
            for path in self.files:
                base = os.path.basename(path)
                found.add(base.split(".", 1)[0] if "." in base else base)
            found.discard("")
            self._identifiers = found
        return self._identifiers


# ── markdown pre-processing ───────────────────────────────────────────────────────────────────────

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
SPAN_RE = re.compile(r"(`+)(.+?)\1")


@dataclass
class Span:
    line: int
    col: int
    text: str
    before: str


@dataclass
class Doc:
    path: str
    lines: list[str]
    fenced: list[bool]
    spans: list[Span]
    raw_text: str  # non-fenced lines, code spans intact
    clean_text: str  # non-fenced lines, code spans blanked out


def parse_doc(path: str, content: str) -> Doc:
    lines = content.split("\n")
    fenced: list[bool] = []
    marker: str | None = None
    for line in lines:
        match = FENCE_RE.match(line)
        if marker is None:
            if match:
                marker = match.group(1)
                fenced.append(True)
                continue
            fenced.append(False)
        else:
            fenced.append(True)
            if match and match.group(1)[0] == marker[0] and len(match.group(1)) >= len(marker):
                marker = None

    spans: list[Span] = []
    raw: list[str] = []
    clean: list[str] = []
    for index, line in enumerate(lines):
        if fenced[index]:
            raw.append("")
            clean.append("")
            continue
        raw.append(line)
        blanked = line
        for match in SPAN_RE.finditer(line):
            start = match.start()
            spans.append(Span(line=index + 1, col=start, text=match.group(2), before=line[start - 1] if start else ""))
            blanked = blanked[:start] + " " * (match.end() - start) + blanked[match.end() :]
        clean.append(blanked)
    return Doc(path=path, lines=lines, fenced=fenced, spans=spans, raw_text="\n".join(raw), clean_text="\n".join(clean))


def parse_docs(tree: Tree) -> dict[str, Doc]:
    tree.load_surfaces()
    return {path: parse_doc(path, tree.read(path)) for path in tree.surfaces}


# ── frontmatter (YAML subset — no PyYAML on the runner) ───────────────────────────────────────────


def parse_frontmatter(content: str) -> tuple[dict[str, object] | None, str | None]:
    """Return (mapping, error). Values are str or list[str]; `error` is set when the block is absent."""
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, "no `---` frontmatter block at the top of the file"
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None, "frontmatter block is never closed with `---`"

    data: dict[str, object] = {}
    key: str | None = None
    index = 1
    while index < end:
        line = lines[index]
        head = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if head:
            key, value = head.group(1), head.group(2).strip()
            if value in ("", "|", ">", "|-", ">-"):
                data[key] = ""
            elif value.startswith("["):
                items = re.findall(r"""["']([^"']*)["']|([^,\[\]\s][^,\[\]]*)""", value[1:].rsplit("]", 1)[0])
                data[key] = [(a or b).strip() for a, b in items if (a or b).strip()]
            else:
                data[key] = value.strip("\"'")
        elif key is not None:
            item = re.match(r"^\s*-\s*(.+?)\s*$", line)
            if item:
                current = data.get(key)
                if not isinstance(current, list):
                    data[key] = [] if current in (None, "") else [str(current)]
                assert isinstance(data[key], list)
                data[key].append(item.group(1).strip("\"'"))  # type: ignore[union-attr]
        index += 1
    return data, None


# ── findings ──────────────────────────────────────────────────────────────────────────────────────


@dataclass
class Finding:
    level: str
    code: str
    path: str
    line: int
    message: str


@dataclass
class Result:
    findings: list[Finding] = field(default_factory=list)
    always_on_bytes: int = 0
    base: str | None = None
    changed_paths: set[str] | None = None
    config: skills_config.Config | None = None

    @property
    def annotations(self) -> list[Finding]:
        """Findings the author of this branch can act on — the only ones worth an inline annotation.

        Every finding still prints and counts; annotating the legacy backlog too would put dozens of
        inline comments on files the PR never opened. Without a base, everything is actionable.
        """
        if self.changed_paths is None:
            return list(self.findings)
        return [f for f in self.findings if f.path in self.changed_paths]

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.level == FAIL]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == WARN]

    @property
    def codes(self) -> set[str]:
        return {f.code for f in self.findings}

    @property
    def failure_codes(self) -> set[str]:
        return {f.code for f in self.failures}

    @property
    def warning_codes(self) -> set[str]:
        return {f.code for f in self.warnings}


# ── absolute checks (C1–C5) ───────────────────────────────────────────────────────────────────────

# Leading `.`/`~` are legal import roots (`@.claude/rules/x.md`, `@~/notes.md`); `~` paths are outside the tree.
IMPORT_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_.~][A-Za-z0-9_./~-]*)")
RULE_KEYS = {"description", "paths"}
SKILL_KEYS = {
    "name",
    "description",
    "argument-hint",
    "allowed-tools",
    "model",
    "disable-model-invocation",
    "user-invocable",
}


def line_count(content: str) -> int:
    return len(content.split("\n")) - 1 if content.endswith("\n") else len(content.split("\n"))


def check_c1(tree: Tree, docs: dict[str, Doc], out: list[Finding]) -> None:
    kernel = tree.config.agent_instructions
    if kernel not in tree.files:
        return
    count = line_count(tree.read(kernel))
    ceiling = tree.config.ceilings["agent_instructions_lines"]
    if count > ceiling:
        out.append(Finding(FAIL, "C1", kernel, count, f"{count} lines > {ceiling} — {kernel} is a thin index"))


def collect_always_on(tree: Tree, docs: dict[str, Doc]) -> list[str]:
    package: list[str] = []
    kernel = tree.config.agent_instructions
    if kernel in tree.files:
        package.append(kernel)
        frontier, hop = [kernel], 0
        seen = {kernel}
        while frontier and hop < 4:
            nxt: list[str] = []
            for path in frontier:
                doc = docs.get(path) or parse_doc(path, tree.read(path))
                for match in IMPORT_RE.finditer(doc.clean_text):
                    target = match.group(1).rstrip(".,;:)")
                    if target in tree.files and target not in seen:
                        seen.add(target)
                        package.append(target)
                        nxt.append(target)
            frontier, hop = nxt, hop + 1
    # A rule without `paths:` is always-on whether or not the kernel also @imports it — count it once.
    for path in sorted(tree.files):
        if tree.surf.is_rule(path) and path not in package:
            data, err = parse_frontmatter(tree.read(path))
            if err is not None or "paths" not in (data or {}):
                package.append(path)
    return package


def check_c2(tree: Tree, docs: dict[str, Doc], out: list[Finding]) -> int:
    total = sum(len(tree.read(p).encode("utf-8")) for p in collect_always_on(tree, docs))
    ceiling = tree.config.ceilings["always_on_bytes"]
    if total > ceiling:
        out.append(
            Finding(
                FAIL,
                "C2",
                tree.config.agent_instructions,
                1,
                f"always-on package is {total} B > {ceiling} B "
                f"({tree.config.agent_instructions} + @imports + rules without `paths:`)",
            )
        )
    return total


def check_c3(tree: Tree, base: Tree | None, out: list[Finding]) -> None:
    ceiling = tree.config.ceilings["rule_lines"]
    for path in sorted(p for p in tree.files if tree.surf.is_rule(p)):
        count = line_count(tree.read(path))
        if count <= ceiling:
            continue
        if base is None:
            out.append(Finding(WARN, "C3", path, count, f"{count} lines > {ceiling} (no base ref — not ratcheted)"))
            continue
        if path not in base.files:
            out.append(Finding(FAIL, "C3", path, count, f"new rule with {count} lines > {ceiling}"))
            continue
        was = line_count(base.read(path))
        if was <= ceiling:
            out.append(Finding(FAIL, "C3", path, count, f"crossed the ceiling: {was} -> {count} lines > {ceiling}"))
        elif count > was:
            out.append(Finding(WARN, "C3", path, count, f"already over the ceiling and grew: {was} -> {count} lines"))


def frontmatter_line(doc: Doc, key: str) -> int:
    for index, line in enumerate(doc.lines[:60]):
        if line.startswith(f"{key}:"):
            return index + 1
    return 1


def check_c4(tree: Tree, docs: dict[str, Doc], out: list[Finding]) -> None:
    for path in sorted(p for p in tree.files if tree.surf.is_rule(p)):
        doc = docs[path]
        data, err = parse_frontmatter(tree.read(path))
        if err is not None or data is None:
            out.append(Finding(FAIL, "C4", path, 1, f"rule frontmatter: {err}"))
            continue
        if not str(data.get("description") or "").strip():
            out.append(Finding(FAIL, "C4", path, 1, "rule frontmatter is missing a `description`"))
        for key in sorted(set(data) - RULE_KEYS):
            out.append(
                Finding(
                    FAIL,
                    "C4",
                    path,
                    frontmatter_line(doc, key),
                    f"unknown rule frontmatter key `{key}:` — the harness reads `paths:`; "
                    "`globs:`/`alwaysApply:` are silently ignored and make the rule always-on",
                )
            )
        if "paths" not in data:
            continue
        paths = data["paths"]
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
            out.append(
                Finding(
                    FAIL,
                    "C4",
                    path,
                    frontmatter_line(doc, "paths"),
                    '`paths:` must be a list of strings (inline `["a", "b"]` or a `- "a"` block)',
                )
            )
            continue
        for glob in paths:
            # `docs/**/premises*.md` -> `docs`; `**/accounts/**` -> "" (not rooted, skipped).
            literal = re.split(r"[*{?]", glob, maxsplit=1)[0].rstrip("/")
            if not literal:
                continue
            if not tree.exists(literal):
                out.append(
                    Finding(
                        WARN,
                        "C4",
                        path,
                        frontmatter_line(doc, "paths"),
                        f"`paths:` glob `{glob}` has a literal prefix that matches nothing in the tree",
                    )
                )


def check_c5(tree: Tree, docs: dict[str, Doc], out: list[Finding]) -> None:
    for path in sorted(p for p in tree.files if tree.surf.is_skill(p)):
        doc = docs[path]
        data, err = parse_frontmatter(tree.read(path))
        if err is not None or data is None:
            out.append(Finding(FAIL, "C5", path, 1, f"SKILL.md frontmatter: {err}"))
            continue
        for key in ("name", "description"):
            if not str(data.get(key) or "").strip():
                out.append(Finding(FAIL, "C5", path, 1, f"SKILL.md frontmatter is missing `{key}`"))
        for key in sorted(set(data) - SKILL_KEYS):
            out.append(
                Finding(
                    WARN,
                    "C5",
                    path,
                    frontmatter_line(doc, key),
                    f"unknown SKILL.md frontmatter key `{key}:` — the harness ignores what it does not know",
                )
            )


# ── failure modes (C16) ───────────────────────────────────────────────────────────────────────────

ENTRY_RE = re.compile(r"^## ([A-Z]{2,3}-\d+) — ")
FIELD_RE = re.compile(r"^\*\*([A-Za-z ]+):\*\*")
OCCURRENCES_STATE_RE = re.compile(
    r"^\*\*Occurrences:\*\* (\d+) · \*\*State:\*\* "
    r"(advisory|advisory \(no gate: [^)]+\)|deterministic \(`([A-Za-z0-9_.]+)`\))\s*$"
)
# Three digits or more: a bare `#64` inside prose would silently raise the cap, while every PR or
# issue worth citing as provenance is >= #100 by the time a repo has a failure-mode ledger.
CHANGE_REF_RE = re.compile(r"#\d{3,}")
MANDATORY_FIELDS = ("Mined from", "Occurrences", "State", "Trigger")
STATE_GRAMMAR = '"**Occurrences:** N · **State:** advisory | advisory (no gate: …) | deterministic (`Gate`)"'


def failure_mode_entries(doc: Doc) -> list[tuple[str, int, list[tuple[int, str]]]]:
    """One `(id, heading line, body)` per `## XX-N — …` entry. Fenced lines and other H2s are dropped —
    the `## Review exclusions` block is an H2 with a fenced payload and must not read as an entry."""
    entries: list[tuple[str, int, list[tuple[int, str]]]] = []
    current: tuple[str, int, list[tuple[int, str]]] | None = None
    for index, line in enumerate(doc.lines):
        if doc.fenced[index]:
            continue
        if line.startswith("## "):
            match = ENTRY_RE.match(line)
            current = (match.group(1), index + 1, []) if match else None
            if current is not None:
                entries.append(current)
            continue
        if current is not None:
            current[2].append((index + 1, line))
    return entries


def entry_fields(body: list[tuple[int, str]]) -> dict[str, tuple[int, str]]:
    """`**Field:**` -> (first line, text). A field runs until the next `**`-line, a heading or a blank line."""
    fields: dict[str, tuple[int, str]] = {}
    name: str | None = None
    for line_no, text in body:
        if text.startswith("**") or text.startswith("#") or text.strip() == "":
            match = FIELD_RE.match(text)
            name = match.group(1) if match else None
            if name is not None and name not in fields:
                fields[name] = (line_no, text)
            continue
        if name is None:
            continue
        first, joined = fields[name]
        fields[name] = (first, f"{joined} {text}")
    return fields


def check_c16(head: Tree, docs: dict[str, Doc], out: list[Finding]) -> None:
    doc = docs.get(head.config.failure_modes)
    if doc is None:
        return
    seen: set[str] = set()
    for entry_id, heading, body in failure_mode_entries(doc):
        if entry_id in seen:
            out.append(Finding(FAIL, "C16", doc.path, heading, f"duplicate entry id {entry_id}"))
            continue
        seen.add(entry_id)
        fields = entry_fields(body)
        state_line = next((n for n, text in body if "**State:**" in text), None)
        for name in MANDATORY_FIELDS:
            if (state_line is not None) if name == "State" else (name in fields):
                continue
            out.append(Finding(FAIL, "C16", doc.path, heading, f"entry {entry_id} is missing `**{name}:**`"))
        if "Occurrences" not in fields:
            continue
        line, text = fields["Occurrences"]
        match = OCCURRENCES_STATE_RE.match(text)
        if match is None:
            out.append(
                Finding(
                    FAIL,
                    "C16",
                    doc.path,
                    line,
                    f"entry {entry_id}: `**Occurrences:**`/`**State:**` line is malformed (expected {STATE_GRAMMAR})",
                )
            )
            continue
        occurrences = int(match.group(1))
        changes = len(set(CHANGE_REF_RE.findall(fields.get("Mined from", (heading, ""))[1])))
        if occurrences < 1 or occurrences > changes:
            out.append(
                Finding(
                    FAIL,
                    "C16",
                    doc.path,
                    line,
                    f"entry {entry_id} claims {occurrences} occurrence(s) but Mined from cites {changes} change(s)",
                )
            )
        gate = match.group(3)
        if gate and base_identifier(gate) not in head.identifiers():
            out.append(
                Finding(FAIL, "C16", doc.path, line, f"entry {entry_id} names gate `{gate}`, which does not exist")
            )


# ── reference checks: delta engine ────────────────────────────────────────────────────────────────

NEW, REGRESSION, LEGACY = "new", "regression", "legacy"


@dataclass
class Ref:
    key: str
    path: str
    line: int
    display: str


class RefCheck:
    """A reference check: collect strings per doc, resolve them against a tree, classify by delta."""

    code = "C0"
    new_level = FAIL

    def enabled(self, config: skills_config.Config) -> bool:
        return True

    def collect(self, doc: Doc, tree: Tree) -> list[Ref]:
        raise NotImplementedError

    def prepare(self, refs: list[Ref], tree: Tree) -> None:
        """Hook for a check that resolves faster in one batch than one subprocess per reference."""

    def resolve(self, ref: Ref, tree: Tree) -> bool:
        raise NotImplementedError

    def level_for_new(self, ref: Ref, tree: Tree) -> str:
        return self.new_level

    def message(self, ref: Ref, kind: str) -> str:
        return f"`{ref.display}` does not resolve ({kind})"


def run_ref_check(
    check: RefCheck,
    head: Tree,
    head_docs: dict[str, Doc],
    base: Tree | None,
    base_docs: dict[str, Doc],
    out: list[Finding],
) -> None:
    if not check.enabled(head.config):
        return
    head_refs: list[Ref] = []
    for path in head.surfaces:
        head_refs.extend(check.collect(head_docs[path], head))
    check.prepare(head_refs, head)
    broken = [ref for ref in head_refs if not check.resolve(ref, head)]
    if not broken:
        return

    if base is None:
        for ref in broken:
            out.append(Finding(WARN, check.code, ref.path, ref.line, check.message(ref, "no base ref")))
        return

    base_list: list[Ref] = []
    for path in base.surfaces:
        base_list.extend(check.collect(base_docs[path], base))
    check.prepare(base_list, base)
    base_refs: dict[str, bool] = {}
    for ref in base_list:
        resolved = check.resolve(ref, base)
        base_refs[ref.key] = base_refs.get(ref.key, True) and resolved

    for ref in broken:
        if ref.key not in base_refs:
            out.append(Finding(check.level_for_new(ref, head), check.code, ref.path, ref.line, check.message(ref, NEW)))
        elif base_refs[ref.key]:
            out.append(Finding(FAIL, check.code, ref.path, ref.line, check.message(ref, REGRESSION)))
        else:
            out.append(Finding(WARN, check.code, ref.path, ref.line, check.message(ref, LEGACY)))


# ── C6: line-number anchors ───────────────────────────────────────────────────────────────────────

ANCHOR_RE = re.compile(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,5}:[0-9]+")
# `docs/x.md:12` is an anchor; `http://host:8080` and `V001__init.sql:` are not — the extension has to
# look like a file's, and the character after the number must not continue a version or a port.
ANCHOR_SKIP = ("http", "https")


class AnchorCheck(RefCheck):
    code = "C6"

    def collect(self, doc: Doc, tree: Tree) -> list[Ref]:
        refs = []
        for match in ANCHOR_RE.finditer(doc.raw_text):
            text = match.group(0)
            if text.split(".", 1)[0] in ANCHOR_SKIP:
                continue
            extension = text.rsplit(":", 1)[0].rsplit(".", 1)[-1].lower()
            if extension not in KNOWN_EXTENSIONS:
                continue
            line = doc.raw_text.count("\n", 0, match.start()) + 1
            refs.append(Ref(key=text, path=doc.path, line=line, display=text))
        return refs

    def resolve(self, ref: Ref, tree: Tree) -> bool:
        return False  # a line-number anchor is a violation by construction; only the delta grades it

    def message(self, ref: Ref, kind: str) -> str:
        return f"line-number anchor `{ref.display}` — anchor by symbol, line numbers rot silently ({kind})"


# ── C7: relative markdown links ───────────────────────────────────────────────────────────────────

LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+?)(#[^)\s]*)?\)")
PLACEHOLDERS = ("{", "*", "…", "...", "NNN", "MMM", "YYYY")


class LinkCheck(RefCheck):
    code = "C7"

    def collect(self, doc: Doc, tree: Tree) -> list[Ref]:
        refs = []
        for match in LINK_RE.finditer(doc.clean_text):
            target = match.group(2)
            if "://" in target or target.startswith(("#", "/", "mailto:")):
                continue
            if any(token in target for token in PLACEHOLDERS):
                continue
            line = doc.clean_text.count("\n", 0, match.start()) + 1
            refs.append(Ref(key=target, path=doc.path, line=line, display=target))
        return refs

    def resolve(self, ref: Ref, tree: Tree) -> bool:
        target = ref.display
        base_dir = os.path.dirname(ref.path)
        for candidate in (os.path.normpath(os.path.join(base_dir, target)), os.path.normpath(target)):
            if tree.exists(candidate.replace(os.sep, "/")):
                return True
        return False

    def message(self, ref: Ref, kind: str) -> str:
        return f"relative link `{ref.display}` points at nothing in the tree ({kind})"


# ── C8: backticked paths ──────────────────────────────────────────────────────────────────────────

PATH_SPAN_RE = re.compile(r"^[A-Za-z0-9_.@{}*-]+(?:/[A-Za-z0-9_.@{}*…-]+)+/?$")
COORDINATE_RE = re.compile(r"^[\w.-]+/[\w.-]+@")
BRANCH_PREFIXES = {"origin", "upstream", "feat", "fix", "chore", "ci", "refactor", "test", "release", "hotfix"}
# `owner/action` coordinates read like paths but name a marketplace action; a coordinate carrying
# `@vN` is already dropped by COORDINATE_RE.
ACTION_OWNERS = ("actions/", "github/", "docker/", "aws-actions/", "anthropics/")
KNOWN_EXTENSIONS = {
    "md",
    "txt",
    "csv",
    "json",
    "yml",
    "yaml",
    "toml",
    "xml",
    "html",
    "css",
    "sh",
    "bash",
    "py",
    "js",
    "mjs",
    "cjs",
    "ts",
    "tsx",
    "jsx",
    "kt",
    "kts",
    "java",
    "cs",
    "go",
    "rs",
    "rb",
    "php",
    "swift",
    "sql",
    "gradle",
    "properties",
    "cfg",
    "ini",
    "lock",
}


def path_extension(ref: str) -> str | None:
    tail = ref.rstrip("/").rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[-1].lower() if "." in tail else None


class BacktickPathCheck(RefCheck):
    code = "C8"

    def collect(self, doc: Doc, tree: Tree) -> list[Ref]:
        refs = []
        for span in doc.spans:
            ref = span.text.strip()
            if not PATH_SPAN_RE.match(ref):
                continue
            if any(token in ref for token in ("{", "*", "…", "...", "NNN", "@")):
                continue
            if COORDINATE_RE.match(ref) or ref.split("/", 1)[0] in BRANCH_PREFIXES:
                continue
            if ref.startswith(ACTION_OWNERS):
                continue
            extension = path_extension(ref)
            if extension not in KNOWN_EXTENSIONS and ref.split("/", 1)[0] not in tree.top_level:
                continue
            refs.append(Ref(key=ref, path=doc.path, line=span.line, display=ref))
        return refs

    def prepare(self, refs: list[Ref], tree: Tree) -> None:
        # Keep the trailing slash: `git check-ignore` only matches a `dir/` pattern against a path
        # that is spelled as a directory.
        tree.load_ignored([ref.display for ref in refs])

    def resolve(self, ref: Ref, tree: Tree) -> bool:
        target = ref.display
        base_dir = os.path.dirname(ref.path)
        directory = target.endswith("/")
        clean = target.rstrip("/")
        # A gitignored path is a runtime location (a ledger dir, build output): it resolves by design
        # and is absent from every tree by design.
        if tree.is_ignored(target):
            return True
        candidates = [clean, os.path.normpath(os.path.join(base_dir, clean)).replace(os.sep, "/")]
        candidates += [f"{root.rstrip('/')}/{clean}" for root in tree.config.path_roots]
        for candidate in candidates:
            if tree.exists(candidate, directory=directory):
                return True
        suffix = "/" + clean
        matches = [p for p in tree.files if p.endswith(suffix)]
        return len(matches) == 1 and not directory

    def level_for_new(self, ref: Ref, tree: Tree) -> str:
        return FAIL if path_extension(ref.display) in KNOWN_EXTENSIONS else WARN

    def message(self, ref: Ref, kind: str) -> str:
        return f"backticked path `{ref.display}` does not exist ({kind})"


# ── C9: migration versions (off until `Migration dirs` is configured) ─────────────────────────────


class MigrationCheck(RefCheck):
    code = "C9"

    def enabled(self, config: skills_config.Config) -> bool:
        return bool(config.migration_dirs)

    def collect(self, doc: Doc, tree: Tree) -> list[Ref]:
        refs = []
        for match in re.finditer(tree.config.migration_pattern, doc.raw_text):
            line = doc.raw_text.count("\n", 0, match.start()) + 1
            refs.append(Ref(key=match.group(0), path=doc.path, line=line, display=match.group(0)))
        return refs

    def resolve(self, ref: Ref, tree: Tree) -> bool:
        if "__" in ref.display:
            return ref.display in tree.migrations
        prefix = ref.display + "__"
        return any(name.startswith(prefix) for name in tree.migrations)

    def message(self, ref: Ref, kind: str) -> str:
        return f"migration `{ref.display}` has no file under the configured migration dirs ({kind})"


# ── C10: source symbols in backticks (off until `Source globs` is configured) ─────────────────────

SYMBOL_SPAN_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?:[A-Z][a-z0-9]+)+(?:[.#$][^`]*)?$")
LOUD_SUFFIXES = ("Test", "Tests", "Spec", "DTO", "RequestBody", "Calculator", "Allocator", "Fixtures")
# Agent tool names: PascalCase, never source symbols, and a rule or skill that drives an agent has to
# spell them exactly. This list grows only when a NEW TOOL NAME shows up in a skill — anything else
# that "does not resolve" is a doc bug, not an allowlist candidate.
AGENT_TOOLS = frozenset(
    {
        "AskUserQuestion",
        "StructuredOutput",
        "ExitPlanMode",
        "EnterPlanMode",
        "TodoWrite",
        "WebFetch",
        "WebSearch",
        "SendMessage",
        "TaskOutput",
        "TaskStop",
        "NotebookEdit",
        "ToolSearch",
        "SlashCommand",
    }
)


def base_identifier(ref: str) -> str:
    return re.split(r"[.#$]", ref, maxsplit=1)[0]


class SymbolCheck(RefCheck):
    code = "C10"

    def enabled(self, config: skills_config.Config) -> bool:
        return bool(config.source_globs)

    def collect(self, doc: Doc, tree: Tree) -> list[Ref]:
        refs = []
        for span in doc.spans:
            ref = span.text.strip()
            if not SYMBOL_SPAN_RE.match(ref) or span.before in ("{", "}"):
                continue
            if ref in AGENT_TOOLS:
                continue
            refs.append(Ref(key=base_identifier(ref), path=doc.path, line=span.line, display=ref))
        return refs

    def resolve(self, ref: Ref, tree: Tree) -> bool:
        return ref.key in tree.identifiers()

    def level_for_new(self, ref: Ref, tree: Tree) -> str:
        return FAIL if ref.key.endswith(LOUD_SUFFIXES) else WARN

    def message(self, ref: Ref, kind: str) -> str:
        return f"symbol `{ref.key}` is not defined anywhere in the configured sources ({kind})"


# ── premises (C11–C14, C17) ───────────────────────────────────────────────────────────────────────

FIELDS = ("**Why:**", "**Breaks:**", "**Tests:**")
ID_FIELD = "**Id:**"
ALL_FIELDS = FIELDS + ("**Depends on:**", ID_FIELD)
ID_RE = re.compile(r"^\*\*Id:\*\* (p-[0-9a-f]{8})\s*$")


@dataclass
class Premise:
    path: str
    domain: str
    title: str
    line: int
    body: list[str]
    body_start: int
    id: str | None = None
    id_raw: str | None = None  # the value when it is there but malformed — C17 says so instead of "missing"
    id_line: int = 0

    @property
    def text(self) -> str:
        return "\n".join(self.body)

    @property
    def id_in_place(self) -> bool:
        """The `**Id:**` line is the first non-empty line of the body — where `premise.py` writes it."""
        for line in self.body:
            if line.strip() == "":
                continue
            return line.startswith(ID_FIELD)
        return False

    def has_field(self, field_name: str) -> bool:
        return any(line.startswith(field_name) for line in self.body)

    def measured_text(self) -> str:
        """The body with the `**Id/Why/Breaks/Tests/Depends on:**` blocks and trailing blanks removed —
        the RATIONALE alone. Every premise carries the same field skeleton, so two short ones read as
        80 % alike on the skeleton before a word of their content is compared."""
        kept: list[str] = []
        in_field = False
        for line in self.body:
            if line.startswith(ALL_FIELDS):
                in_field = True
                continue
            if in_field:
                if line.strip() == "":
                    in_field = False
                continue
            kept.append(line)
        while kept and kept[-1].strip() == "":
            kept.pop()
        return "\n".join(kept)

    def measured(self) -> tuple[int, int]:
        """Body lines/bytes with the `**Why/Breaks/Tests/Depends on:**` blocks and trailing blanks removed."""
        text = self.measured_text()
        return len(text.split("\n")) if text else 0, len(text.encode("utf-8"))

    def tests_value(self) -> tuple[str, int] | None:
        for index, line in enumerate(self.body):
            if not line.startswith("**Tests:**"):
                continue
            parts = [line[len("**Tests:**") :]]
            for follow in self.body[index + 1 :]:
                if follow.strip() == "" or follow.startswith("**") or follow.startswith("#"):
                    break
                parts.append(follow)
            return re.sub(r"\s+", " ", " ".join(parts)).strip(), self.body_start + index
        return None


def parse_premise_id(body: list[str], body_start: int) -> tuple[str | None, str | None, int]:
    """`(id, malformed value, 1-based line)` of the `**Id:**` field — `(None, None, 0)` when it is absent."""
    for offset, line in enumerate(body):
        if not line.startswith(ID_FIELD):
            continue
        match = ID_RE.match(line.rstrip())
        value = match.group(1) if match else None
        return value, None if match else line[len(ID_FIELD) :].strip(), body_start + offset
    return None, None, 0


def collect_premises(docs: dict[str, Doc], surfaces: Surfaces) -> list[Premise]:
    premises: list[Premise] = []
    for path in sorted(docs):
        if not surfaces.is_premises(path):
            continue
        doc = docs[path]
        domain = os.path.dirname(path)
        heads = [
            index
            for index, line in enumerate(doc.lines)
            if not doc.fenced[index] and (line.startswith("## ") or (line.startswith("# ") and index > 0))
        ]
        for position, index in enumerate(heads):
            line = doc.lines[index]
            if not line.startswith("## "):
                continue
            title = line[3:].strip()
            if title.startswith("["):
                continue
            end = heads[position + 1] if position + 1 < len(heads) else len(doc.lines)
            body = doc.lines[index + 1 : end]
            premise_id, id_raw, id_line = parse_premise_id(body, index + 2)
            premises.append(
                Premise(
                    path=path,
                    domain=domain,
                    title=title,
                    line=index + 1,
                    body=body,
                    body_start=index + 2,
                    id=premise_id,
                    id_raw=id_raw,
                    id_line=id_line,
                )
            )
    return premises


def match_premises(head: list[Premise], base: list[Premise]) -> dict[int, Premise | None]:
    """Map each head premise to its base counterpart: same title in the domain, else a rename (ratio >= 0.8)."""
    by_domain_title: dict[tuple[str, str], Premise] = {}
    for premise in base:
        by_domain_title.setdefault((premise.domain, premise.title), premise)
    head_titles = {(p.domain, p.title) for p in head}
    orphans: dict[str, list[Premise]] = {}
    for premise in base:
        if (premise.domain, premise.title) not in head_titles:
            orphans.setdefault(premise.domain, []).append(premise)

    mapping: dict[int, Premise | None] = {}
    for premise in head:
        found = by_domain_title.get((premise.domain, premise.title))
        if found is None:
            for candidate in orphans.get(premise.domain, []):
                if difflib.SequenceMatcher(None, premise.text, candidate.text).ratio() >= 0.8:
                    found = candidate
                    break
        mapping[id(premise)] = found
    return mapping


TEST_TOKEN_RE = re.compile(r"(?<![*@{\w])([A-Z][A-Za-z0-9]*(?:Test|Tests|Spec))\b")
NO_TEST_HINTS = ("none yet", "n/a", "see ", "todo")


def check_premises(
    head: Tree,
    head_docs: dict[str, Doc],
    base: Tree | None,
    base_docs: dict[str, Doc],
    out: list[Finding],
) -> None:
    head_premises = collect_premises(head_docs, head.surf)
    base_premises = collect_premises(base_docs, base.surf) if base is not None else []
    mapping = match_premises(head_premises, base_premises)

    # C13 needs the same new/regression/legacy grading as the reference checks.
    base_tokens: dict[str, bool] = {}
    if base is not None:
        for premise in base_premises:
            value = premise.tests_value()
            if value is None:
                continue
            for token in TEST_TOKEN_RE.findall(value[0]):
                resolved = token in base.identifiers()
                base_tokens[token] = base_tokens.get(token, True) and resolved

    for premise in head_premises:
        counterpart = mapping[id(premise)] if base is not None else None
        is_new = base is not None and counterpart is None
        check_c11(premise, is_new, out)
        check_c12(premise, counterpart, is_new, base is None, head.config, out)
        check_c13(premise, is_new, base, base_tokens, head, out)
    check_c14(head_docs, head_premises, head.surf, out)
    check_c17(head_premises, head.config, out)


def check_c11(premise: Premise, is_new: bool, out: list[Finding]) -> None:
    if not is_new:
        return
    missing = [f for f in (ID_FIELD,) + FIELDS if not premise.has_field(f)]
    if missing:
        out.append(
            Finding(
                FAIL,
                "C11",
                premise.path,
                premise.line,
                f'new premise "{premise.title}" is missing {", ".join(missing)}',
            )
        )


def check_c12(
    premise: Premise,
    counterpart: Premise | None,
    is_new: bool,
    no_base: bool,
    config: skills_config.Config,
    out: list[Finding],
) -> None:
    lines, size = premise.measured()
    max_lines = config.ceilings["premise_lines"]
    max_bytes = config.ceilings["premise_bytes"]
    warn_lines = config.ceilings["premise_warn_lines"]
    if is_new:
        if lines > max_lines or size > max_bytes:
            out.append(
                Finding(
                    FAIL,
                    "C12",
                    premise.path,
                    premise.line,
                    f'new premise "{premise.title}" body is {lines} lines / {size} B '
                    f"> {max_lines} lines / {max_bytes} B (fields excluded)",
                )
            )
        elif lines > warn_lines:
            out.append(
                Finding(
                    WARN,
                    "C12",
                    premise.path,
                    premise.line,
                    f'new premise "{premise.title}" body is {lines} lines > {warn_lines} (fields excluded)',
                )
            )
        return
    if no_base or counterpart is None:
        return
    if lines <= max_lines and size <= max_bytes:
        return
    was_lines, was_size = counterpart.measured()
    if lines > was_lines or size > was_size:
        out.append(
            Finding(
                WARN,
                "C12",
                premise.path,
                premise.line,
                f'premise "{premise.title}" is over the ceiling and grew: '
                f"{was_lines} -> {lines} lines / {was_size} -> {size} B",
            )
        )


def check_c13(
    premise: Premise,
    is_new: bool,
    base: Tree | None,
    base_tokens: dict[str, bool],
    head: Tree,
    out: list[Finding],
) -> None:
    value = premise.tests_value()
    if value is None:
        return
    text, line = value
    tokens = TEST_TOKEN_RE.findall(text)
    if not tokens:
        lowered = text.lower()
        if not any(hint in lowered for hint in NO_TEST_HINTS):
            out.append(
                Finding(WARN, "C13", premise.path, line, f'`**Tests:**` of "{premise.title}" names no test class')
            )
        elif is_new and "none yet" in lowered:
            out.append(Finding(WARN, "C13", premise.path, line, f'new premise "{premise.title}" ships with `none yet`'))
        return
    for token in tokens:
        if token in head.identifiers():
            continue
        if base is None:
            kind, level = "no base ref", WARN
        elif token not in base_tokens:
            kind, level = NEW, FAIL
        elif base_tokens[token]:
            kind, level = REGRESSION, FAIL
        else:
            kind, level = LEGACY, WARN
        out.append(
            Finding(level, "C13", premise.path, line, f"`**Tests:**` names `{token}`, which no longer exists ({kind})")
        )
    check_c13_members(premise, text, line, head, out)


MEMBER_SPAN_RE = re.compile(r"`([A-Z][A-Za-z0-9]*(?:Test|Tests|Spec))[.#$]([^`]+)`")


def member_of(rest: str) -> str:
    """First segment after the `.`/`#`/`$` — a nested class, a method, or a backticked test name."""
    rest = re.sub(r"\s+", " ", rest).strip()
    if rest[:1] in ('"', "'"):
        quote, end = rest[0], rest.find(rest[0], 1)
        return rest[1:end] if end > 0 else rest[1:]
    return re.split(r"[.#$]", rest, maxsplit=1)[0].strip().strip("\"'")


def check_c13_members(premise: Premise, text: str, line: int, head: Tree, out: list[Finding]) -> None:
    for match in MEMBER_SPAN_RE.finditer(text):
        klass, member = match.group(1), member_of(match.group(2))
        if len(member) < 3 or any(token in member for token in ("{", "*", "…")):
            continue
        files = [p for p in head.files if os.path.basename(p).split(".", 1)[0] == klass]
        if len(files) != 1:
            continue
        if member not in head.read(files[0]):
            out.append(
                Finding(
                    WARN,
                    "C13",
                    premise.path,
                    line,
                    f"`**Tests:**` names `{klass}.{member}` but `{member}` is absent from {files[0]}",
                )
            )


WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")


def normalise_title(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lstrip("#").strip()


def check_c14(docs: dict[str, Doc], premises: list[Premise], surfaces: Surfaces, out: list[Finding]) -> None:
    by_domain: dict[str, set[str]] = {}
    everywhere: set[str] = set()
    for premise in premises:
        title = normalise_title(premise.title)
        by_domain.setdefault(premise.domain, set()).add(title)
        everywhere.add(title)
        if premise.id:  # a link may address a premise by its stable id — it survives a retitle
            everywhere.add(premise.id)
    for path, doc in sorted(docs.items()):
        if not surfaces.is_premises(path):
            continue
        domain = os.path.dirname(path)
        for match in WIKI_RE.finditer(doc.raw_text):
            title = normalise_title(match.group(1))
            if title in by_domain.get(domain, set()) or title in everywhere:
                continue
            line = doc.raw_text.count("\n", 0, match.start()) + 1
            out.append(Finding(WARN, "C14", path, line, f"`[[{title}]]` matches no premise title or id"))


# ── C17: premise ids ──────────────────────────────────────────────────────────────────────────────


def mint_hint(config: skills_config.Config) -> str:
    return f"mint one with `{config.fetch_command('mint')}`"


def check_c17(premises: list[Premise], config: skills_config.Config, out: list[Finding]) -> None:
    """Every premise carries an `**Id:**`, well-formed and unique across the premises tree.

    The id is the handle the fetch command resolves to exactly one section — a duplicate makes the
    reader ambiguous, a missing one makes the premise unreachable by id.
    """
    hint = mint_hint(config)
    by_id: dict[str, list[Premise]] = {}
    for premise in premises:
        if premise.id_raw is not None:
            out.append(
                Finding(
                    FAIL,
                    "C17",
                    premise.path,
                    premise.id_line,
                    f'premise "{premise.title}" has a malformed `**Id:** {premise.id_raw}` '
                    "— expected `p-` plus 8 lowercase hex digits",
                )
            )
            continue
        if premise.id is None:
            out.append(
                Finding(
                    FAIL, "C17", premise.path, premise.line, f'premise "{premise.title}" has no `**Id:**` — {hint}'
                )
            )
            continue
        if not premise.id_in_place:
            out.append(
                Finding(
                    WARN,
                    "C17",
                    premise.path,
                    premise.id_line,
                    f'`**Id:**` of "{premise.title}" is out of position — it belongs right below the H2',
                )
            )
        by_id.setdefault(premise.id, []).append(premise)
    for premise_id, holders in sorted(by_id.items()):
        if len(holders) == 1:
            continue
        for premise in holders:
            others = ", ".join(f'"{p.title}" ({p.path}:{p.line})' for p in holders if p is not premise)
            out.append(
                Finding(
                    FAIL,
                    "C17",
                    premise.path,
                    premise.id_line,
                    f"premise id `{premise_id}` is not unique — also held by {others}; {hint}",
                )
            )


# ── premises index (C15) ──────────────────────────────────────────────────────────────────────────

ESSENCE_ABBREVIATIONS = frozenset(
    {"e.g", "i.e", "vs", "ex", "etc", "cf", "no", "art", "p", "min", "max", "approx", "resp"}
)
ESSENCE_SKIP_PREFIXES = ("#", ">", "|", "<!--")
LIST_MARKER_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")


def index_header(config: skills_config.Config) -> str:
    return (
        f"Generated by `{config.index_write}` from the H2 titles of the premises files in this folder; "
        "never edit by hand (context-lint C15 fails on drift)."
    )


def index_recipe(config: skills_config.Config) -> str:
    return (
        f"To read one premise: `{config.premise_fetch}` (add `--deps` for its dependencies). Never read the "
        "whole file — the `Read` tool truncates at 2.000 lines and a large domain passes that."
    )


def essence_blocks(body: list[str]) -> list[list[str]]:
    """Paragraph blocks of a premise body — blank-line separated, fenced code dropped (parse_doc's fence rule)."""
    blocks: list[list[str]] = []
    current: list[str] = []
    marker: str | None = None
    for line in body:
        match = FENCE_RE.match(line)
        if marker is None and match:
            marker = match.group(1)
            if current:
                blocks.append(current)
                current = []
            continue
        if marker is not None:
            if match and match.group(1)[0] == marker[0] and len(match.group(1)) >= len(marker):
                marker = None
            continue
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def field_text(body: list[str], field_name: str) -> str | None:
    for index, line in enumerate(body):
        if not line.startswith(field_name):
            continue
        parts = [line[len(field_name) :]]
        for follow in body[index + 1 :]:
            if follow.strip() == "" or follow.startswith("**") or follow.startswith("#"):
                break
            parts.append(follow)
        return " ".join(parts)
    return None


def first_text(premise: Premise) -> str | None:
    """First paragraph before the field lines; a field-first body falls back to its `**Why:**`.

    The `**Id:**` block is skipped, not treated as a field-first body: it is the first block of EVERY
    premise, so reading it as one would send every essence to its `**Why:**`.
    """
    for block in essence_blocks(premise.body):
        lines = [line for line in block if not line.lstrip().startswith(ID_FIELD)]
        if not lines:
            continue
        first = lines[0].lstrip()
        if first.startswith(ALL_FIELDS):
            return field_text(premise.body, "**Why:**")
        if first.startswith(ESSENCE_SKIP_PREFIXES):
            continue
        if LIST_MARKER_RE.match(first):
            return LIST_MARKER_RE.sub("", first)
        return " ".join(line.strip() for line in lines)
    return None


def first_sentence(text: str) -> str:
    """Cut at `.`/`!`/`?` outside code spans and parentheses, followed by a capital, digit or markup opener."""
    in_code = False
    depth = 0
    for index, char in enumerate(text):
        if char == "`":
            in_code = not in_code
            continue
        if in_code:
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char in ".!?" and depth == 0:
            end = index + 1
            while end < len(text) and text[end] in ')"*':
                end += 1
            if end == len(text):
                return text
            if text[end] != " ":
                continue
            after = end
            while after < len(text) and text[after] == " ":
                after += 1
            following = text[after] if after < len(text) else ""
            if not (following.isupper() or following.isdigit() or following in "`*([\""):
                continue
            word = re.search(r"([A-Za-zÀ-ÿ.]+)$", text[:index])
            if word and word.group(1).lower() in ESSENCE_ABBREVIATIONS:
                continue
            return text[:end]
    return text


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    text = text[: cut if cut > 0 else limit]
    if text.count("`") % 2:
        text = text[: text.rfind("`")]
    if text.count("**") % 2:
        text = text[: text.rfind("**")]
    return text.rstrip(" ,;:—-(") + " …"


def essence(premise: Premise) -> str:
    text = first_text(premise)
    if text is None:
        return ""
    return truncate(first_sentence(re.sub(r"\s+", " ", text).strip()), INDEX_ESSENCE_CHARS)


def render_index(domain: str, premises: list[Premise], config: skills_config.Config) -> str:
    main = os.path.basename(config.premises).replace("{domain}", "").replace("{module}", "")
    files = sorted({p.path for p in premises}, key=lambda p: (os.path.basename(p) != main, p))
    lines = [
        f"# {os.path.basename(domain)} — premises index",
        "",
        index_header(config),
        "",
        index_recipe(config),
        "",
    ]
    for path in files:
        name = os.path.basename(path)
        lines += [f"## [{name}]({name})", ""]
        for premise in premises:
            if premise.path != path:
                continue
            handle = f" · `{premise.id}`" if premise.id else ""
            line = essence(premise)
            lines.append(f"- **{premise.title}** — {line}{handle}" if line else f"- **{premise.title}**{handle}")
        lines.append("")
    return "\n".join(lines)


def render_indices(docs: dict[str, Doc], surfaces: Surfaces) -> dict[str, str]:
    """Expected content of every index, keyed by path — one per domain folder holding at least one premise."""
    by_domain: dict[str, list[Premise]] = {}
    for premise in collect_premises(docs, surfaces):
        by_domain.setdefault(premise.domain, []).append(premise)
    basename = surfaces.config.premises_index_basename
    return {f"{d}/{basename}".lstrip("/"): render_index(d, ps, surfaces.config) for d, ps in sorted(by_domain.items())}


def write_indices(repo: str, config: skills_config.Config | None = None) -> list[str]:
    """Write every index whose content differs; returns the paths written (empty = up to date)."""
    config = config or skills_config.load(repo)
    surfaces = Surfaces(config)
    tree = Tree(repo, None, surfaces)
    written: list[str] = []
    for path, content in render_indices(parse_docs(tree), surfaces).items():
        if tree.exists(path) and tree.read(path) == content:
            continue
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        written.append(path)
    return written


def check_c15(tree: Tree, docs: dict[str, Doc], out: list[Finding]) -> None:
    hint = f"run `{tree.config.index_write}`"
    expected = render_indices(docs, tree.surf)
    for path, content in expected.items():
        if not tree.exists(path):
            out.append(Finding(FAIL, "C15", path, 1, f"premises index is missing — {hint}"))
            continue
        actual = tree.read(path)
        if actual == content:
            continue
        pairs = zip(actual.split("\n"), content.split("\n"))
        line = next(
            (i + 1 for i, (a, b) in enumerate(pairs) if a != b),
            min(actual.count("\n"), content.count("\n")) + 1,
        )
        message = f"premises index is out of date (first difference at line {line}) — {hint}"
        out.append(Finding(FAIL, "C15", path, line, message))
    for path in sorted(tree.files):
        if tree.surf.is_index(path) and path not in expected:
            orphan = "orphan premises index — its folder has no premises file; delete it"
            out.append(Finding(FAIL, "C15", path, 1, orphan))


# ── near-duplicates: the merge queue (report, never a gate) ───────────────────────────────────────

DUPLICATE_THRESHOLD = 0.8
# Two premises can only be 80 % similar as sequences if they share most of their vocabulary, so the
# cheap set comparison throws out ~all of the n² pairs before the expensive one runs. 0.4 is half the
# body threshold: generous on purpose, since a prefilter that drops a real pair is a silent miss.
DUPLICATE_PREFILTER = 0.4
WORD_RE = re.compile(r"\w+")


def word_set(text: str) -> frozenset:
    return frozenset(WORD_RE.findall(text.lower()))


def jaccard(left: frozenset, right: frozenset) -> float:
    if not left or not right:
        return 0.0
    shared = len(left & right)
    return shared / (len(left) + len(right) - shared)


def premise_ref(item: Premise) -> dict:
    return {"path": item.path, "title": item.title, "id": item.id}


def near_duplicates(repo_dir: str, threshold: float = DUPLICATE_THRESHOLD,
                    config: skills_config.Config | None = None) -> dict:
    """Pairs of premises whose BODIES are near-identical — candidates to merge into one.

    The comparator is the `difflib.SequenceMatcher(...).ratio()` C11/C12 already use to recognise a
    renamed premise, so "near-duplicate" means one single thing in this toolkit. What it compares is
    `measured_text()`, not the raw body: C11/C12 match a premise against its OWN former self, where the
    shared field skeleton is harmless, while ACROSS premises that skeleton alone puts two unrelated
    short ones over 0.8. This is a REPORT: it names pairs for a human to judge, it never fails a build.
    """
    repo = os.path.abspath(repo_dir)
    config = config or skills_config.load(repo)
    surfaces = Surfaces(config)
    premises = collect_premises(parse_docs(Tree(repo, None, surfaces)), surfaces)
    texts = [item.measured_text() for item in premises]
    words = [word_set(text) for text in texts]
    matcher = difflib.SequenceMatcher(None, autojunk=False)

    pairs: list[dict] = []
    for left in range(len(premises)):
        matcher.set_seq2(texts[left])
        for right in range(left + 1, len(premises)):
            if jaccard(words[left], words[right]) < DUPLICATE_PREFILTER:
                continue
            matcher.set_seq1(texts[right])
            # Both are cheap UPPER BOUNDS of ratio(): under the threshold they cannot reach it.
            if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                continue
            ratio = matcher.ratio()
            if ratio >= threshold:
                pairs.append({"ratio": round(ratio, 4), "a": premise_ref(premises[left]),
                              "b": premise_ref(premises[right])})
    pairs.sort(key=lambda item: (-item["ratio"], item["a"]["path"], item["a"]["title"]))
    return {"threshold": threshold, "prefilter": DUPLICATE_PREFILTER, "premises": len(premises), "pairs": pairs}


def render_near_duplicates(report: dict) -> str:
    lines = [
        "# Near-duplicate premises",
        "",
        "%d premise(s), threshold %.2f (Jaccard prefilter %.2f) — %d pair(s)."
        % (report["premises"], report["threshold"], report["prefilter"], len(report["pairs"])),
        "",
    ]
    if not report["pairs"]:
        return "\n".join(lines + ["No pair over the threshold.", ""])
    lines += ["| ratio | a | b |", "|---:|---|---|"]
    for pair in report["pairs"]:
        lines.append(
            "| %.2f | `%s` › %s (`%s`) | `%s` › %s (`%s`) |"
            % (pair["ratio"], pair["a"]["path"], pair["a"]["title"], pair["a"]["id"] or "—",
               pair["b"]["path"], pair["b"]["title"], pair["b"]["id"] or "—")
        )
    return "\n".join(lines) + "\n"


# ── driver ────────────────────────────────────────────────────────────────────────────────────────

REF_CHECKS = (AnchorCheck(), LinkCheck(), BacktickPathCheck(), MigrationCheck(), SymbolCheck())


def resolve_base(repo: str, base_ref: str | None) -> str | None:
    if not base_ref:
        return None
    merge_base = git(repo, "merge-base", base_ref, "HEAD").strip() or base_ref
    sha = git(repo, "rev-parse", "--verify", "--quiet", f"{merge_base}^{{commit}}").strip()
    if not sha:
        raise SystemExit(f"context-lint: cannot resolve base ref '{base_ref}'")
    return sha


def collect_changed_paths(repo: str, base_sha: str | None) -> set[str] | None:
    """Files this branch touched: `git diff base..worktree` plus everything untracked. None = no base."""
    if base_sha is None:
        return None
    changed = {p for p in git(repo, "diff", "--name-only", "-z", base_sha, "--").split("\0") if p}
    changed |= {p for p in git(repo, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p}
    return changed


def run(repo_dir: str, base_ref: str | None = None, config: skills_config.Config | None = None) -> Result:
    repo = os.path.abspath(repo_dir)
    config = config or skills_config.load(repo)
    surfaces = Surfaces(config)
    base_sha = resolve_base(repo, base_ref)
    head = Tree(repo, None, surfaces)
    head_docs = parse_docs(head)
    base = Tree(repo, base_sha, surfaces) if base_sha else None
    base_docs = parse_docs(base) if base else {}

    result = Result(base=base_sha, changed_paths=collect_changed_paths(repo, base_sha), config=config)
    findings = result.findings
    check_c1(head, head_docs, findings)
    result.always_on_bytes = check_c2(head, head_docs, findings)
    check_c3(head, base, findings)
    check_c4(head, head_docs, findings)
    check_c5(head, head_docs, findings)
    check_c16(head, head_docs, findings)
    for check in REF_CHECKS:
        run_ref_check(check, head, head_docs, base, base_docs, findings)
    check_premises(head, head_docs, base, base_docs, findings)
    check_c15(head, head_docs, findings)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint the context surfaces (instructions file, rules, skills, docs).")
    parser.add_argument("--base", default=os.environ.get("CONTEXT_LINT_BASE_REF") or None)
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument(
        "--config", default=None, help=f"path to the config (default: <repo>/{skills_config.CONFIG_PATH})"
    )
    parser.add_argument(
        "--write-indices",
        action="store_true",
        help="regenerate every premises index and exit — the formatter to C15's format check",
    )
    parser.add_argument(
        "--near-duplicates",
        action="store_true",
        help="report premise pairs whose bodies are near-identical and exit 0 (never a gate)",
    )
    parser.add_argument("--threshold", type=float, default=DUPLICATE_THRESHOLD,
                        help="similarity floor of --near-duplicates (default: %.1f)" % DUPLICATE_THRESHOLD)
    parser.add_argument("--format", choices=("md", "json"), default="md",
                        help="output format of --near-duplicates (default: md)")
    args = parser.parse_args(argv)
    config = skills_config.load(args.repo, args.config)

    if args.near_duplicates:
        report = near_duplicates(args.repo, args.threshold, config)
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(render_near_duplicates(report), end="")
        return 0

    if args.write_indices:
        written = write_indices(args.repo, config)
        for path in written:
            print(path)
        if not written:
            print("context-lint: premises indices up to date")
        return 0

    result = run(args.repo, args.base, config)
    annotated = {id(f) for f in result.annotations} if os.environ.get("GITHUB_ACTIONS") else set()
    for finding in sorted(result.findings, key=lambda f: (f.code, f.path, f.line, f.message)):
        print(f"{finding.level} {finding.path}:{finding.line}: [{finding.code}] {finding.message}")
        if id(finding) in annotated:
            kind = "error" if finding.level == FAIL else "warning"
            print(f"::{kind} file={finding.path},line={finding.line}::[{finding.code}] {finding.message}")

    base = result.base[:12] if result.base else "none — delta checks degraded to warnings"
    for note in config.notes:
        print(f"context-lint: {note}")
    print(
        f"context-lint: {len(result.failures)} failure(s), {len(result.warnings)} warning(s) "
        f"(always-on {result.always_on_bytes} B, base {base})"
    )
    return 1 if result.failures else 0


if __name__ == "__main__":
    sys.exit(main())
