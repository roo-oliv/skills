#!/usr/bin/env python3
"""Read ONE premise by its stable id — never the whole premises file.

    python3 .github/scripts/premise.py p-1a2b3c4d           # the section: the H2 line to the next H2 (or EOF)
    python3 .github/scripts/premise.py p-1a2b3c4d --deps    # plus the premises it points at (one level)
    python3 .github/scripts/premise.py mint                 # a fresh id no premise in the tree holds
    python3 .github/scripts/premise.py assign-missing       # write `**Id:**` under every H2 that has none

Why an id instead of a title: the `Read` tool caps at 2.000 lines, so "read the domain's premises.md"
silently truncates a large domain; and a title gets retitled while the invariant does not. The id is
minted once, sits right below the H2, and survives a retitle or a file split. `context_lint.py` C17
keeps it present and unique; the generated premises index is where a reader picks it up.

This script carries its own scanner — os.walk over the premises tree, no git, no full-tree parse — so
a fetch stays well under 100 ms. The premise shape it recognises is the one
`context_lint.collect_premises` defines: an H2 outside fenced blocks whose title does not start with
`[`, running to the next heading or EOF. Where premises live comes from `docs/agents/skills-config.md`
(`Docs layout › Premises`). Python 3 stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skills_config  # noqa: E402

ID_FIELD = "**Id:**"
DEPENDS_FIELD = "**Depends on:**"
ID_RE = re.compile(r"^\*\*Id:\*\* (p-[0-9a-f]{8})\s*$")
ID_TOKEN_RE = re.compile(r"\bp-[0-9a-f]{8}\b")
WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
MIN_TITLE_MATCH = 12  # a `**Depends on:**` in prose only resolves a title long enough to be unambiguous


@dataclass
class Section:
    path: str
    title: str
    id: str | None
    start: int  # 1-based line of the H2
    lines: list[str]  # the H2 line included, trailing blank lines dropped

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lstrip("#").strip()


def fence_map(lines: list[str]) -> list[bool]:
    """Per line: is it inside a fenced code block? (context_lint.parse_doc's rule, kept identical.)"""
    fenced: list[bool] = []
    marker: str | None = None
    for line in lines:
        match = FENCE_RE.match(line)
        if marker is None:
            fenced.append(bool(match))
            if match:
                marker = match.group(1)
        else:
            fenced.append(True)
            if match and match.group(1)[0] == marker[0] and len(match.group(1)) >= len(marker):
                marker = None
    return fenced


def premises_root(config: skills_config.Config) -> str:
    """The deepest fixed prefix of the premises pattern — where the walk starts."""
    prefix = re.split(r"\{[a-z_]+\}", config.premises)[0]
    return prefix.rstrip("/") or "."


def premises_files(repo: str, config: skills_config.Config) -> list[str]:
    """Every premises file, repo-relative and sorted — the generated index excluded."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(repo, premises_root(config))):
        dirnames.sort()
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), repo).replace(os.sep, "/")
            if config.is_premises(rel):
                found.append(rel)
    return sorted(found)


def section_id(lines: list[str]) -> str | None:
    for line in lines[1:]:
        if line.startswith(ID_FIELD):
            match = ID_RE.match(line.rstrip())
            return match.group(1) if match else None
    return None


def has_id_line(lines: list[str]) -> bool:
    return any(line.startswith(ID_FIELD) for line in lines[1:])


def parse_sections(path: str, content: str) -> list[Section]:
    lines = content.split("\n")
    fenced = fence_map(lines)
    heads = [
        index
        for index, line in enumerate(lines)
        if not fenced[index] and (line.startswith("## ") or (line.startswith("# ") and index > 0))
    ]
    sections: list[Section] = []
    for position, index in enumerate(heads):
        line = lines[index]
        if not line.startswith("## "):
            continue
        title = line[3:].strip()
        if title.startswith("["):  # a link heading is a part map, not a premise
            continue
        end = heads[position + 1] if position + 1 < len(heads) else len(lines)
        body = lines[index:end]
        while body and body[-1].strip() == "":
            body.pop()
        sections.append(Section(path=path, title=title, id=section_id(body), start=index + 1, lines=body))
    return sections


def load(repo: str, config: skills_config.Config | None = None) -> list[Section]:
    config = config or skills_config.load(repo)
    sections: list[Section] = []
    for path in premises_files(repo, config):
        with open(os.path.join(repo, path), encoding="utf-8") as handle:
            sections += parse_sections(path, handle.read())
    return sections


def field_value(section: Section, field_name: str) -> str | None:
    """The text of a field line plus its continuation lines — `None` when the field is absent."""
    for index, line in enumerate(section.lines):
        if not line.startswith(field_name):
            continue
        parts = [line[len(field_name) :]]
        for follow in section.lines[index + 1 :]:
            if follow.strip() == "" or follow.startswith("**") or follow.startswith("#"):
                break
            parts.append(follow)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()
    return None


def deps(sections: list[Section], section: Section) -> list[Section]:
    """The premises this one points at — `[[title]]`, `[[p-id]]` and `**Depends on:**` — one level, no repeats."""
    by_id = {s.id: s for s in sections if s.id}
    by_title: dict[str, Section] = {}
    for candidate in sections:
        by_title.setdefault(normalise(candidate.title), candidate)

    found: list[Section] = []
    seen = {id(section)}

    def add(target: Section | None) -> None:
        if target is not None and id(target) not in seen:
            seen.add(id(target))
            found.append(target)

    for match in WIKI_RE.finditer(section.text):
        ref = normalise(match.group(1))
        add(by_id.get(ref) or by_title.get(ref))
    depends = field_value(section, DEPENDS_FIELD)
    if depends:
        for token in ID_TOKEN_RE.findall(depends):
            add(by_id.get(token))
        lowered = depends.lower()
        for title, target in by_title.items():
            if len(title) >= MIN_TITLE_MATCH and title.lower() in lowered:
                add(target)
    return found


def mint(sections: list[Section]) -> str:
    taken = {s.id for s in sections if s.id}
    while True:
        candidate = "p-" + uuid.uuid4().hex[:8]
        if candidate not in taken:
            return candidate


def assign_missing(repo: str, config: skills_config.Config | None = None) -> int:
    """Insert `**Id:**` right below every H2 that has none — one added line per premise, nothing else."""
    config = config or skills_config.load(repo)
    taken = {s.id for s in load(repo, config) if s.id}
    assigned = 0
    for path in premises_files(repo, config):
        full = os.path.join(repo, path)
        with open(full, encoding="utf-8") as handle:
            content = handle.read()
        missing = [s for s in parse_sections(path, content) if not has_id_line(s.lines)]
        if not missing:
            continue
        lines = content.split("\n")
        for section in sorted(missing, key=lambda s: s.start, reverse=True):
            while True:
                new_id = "p-" + uuid.uuid4().hex[:8]
                if new_id not in taken:
                    break
            taken.add(new_id)
            lines.insert(section.start, f"{ID_FIELD} {new_id}")
        with open(full, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))
        assigned += len(missing)
    return assigned


def render(section: Section) -> str:
    return f"# {section.path} › {section.title}\n{section.text}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read one premise by its `**Id:**`.")
    parser.add_argument("target", help="a premise id (p-xxxxxxxx), or the command `mint` / `assign-missing`")
    parser.add_argument("--deps", action="store_true", help="also print the premises this one points at")
    parser.add_argument("--repo", default=skills_config.default_repo())
    parser.add_argument(
        "--config", default=None, help=f"path to the config (default: <repo>/{skills_config.CONFIG_PATH})"
    )
    args = parser.parse_args(argv)
    config = skills_config.load(args.repo, args.config)

    if args.target == "mint":
        print(mint(load(args.repo, config)))
        return 0
    if args.target == "assign-missing":
        assigned = assign_missing(args.repo, config)
        print(f"premise: {assigned} id(s) assigned" if assigned else "premise: every premise already has an id")
        return 0
    if not ID_TOKEN_RE.fullmatch(args.target):
        print(f"premise: '{args.target}' is not a premise id (p- plus 8 hex digits)", file=sys.stderr)
        return 1

    sections = load(args.repo, config)
    matches = [s for s in sections if s.id == args.target]
    if not matches:
        print(f"premise: no premise holds the id {args.target}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        where = ", ".join(f"{s.path}:{s.start}" for s in matches)
        print(f"premise: the id {args.target} is ambiguous — held by {where}", file=sys.stderr)
        return 1

    blocks = [render(matches[0])]
    if args.deps:
        blocks += [render(section) for section in deps(sections, matches[0])]
    print("\n\n".join(blocks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
