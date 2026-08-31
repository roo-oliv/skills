#!/usr/bin/env python3
"""Read `docs/agents/skills-config.md` — the one file the whole toolkit adapts to.

Every path, glob, ceiling and domain the context scripts use comes from here, with a declared
default when the section is absent. Nothing about a stack is hardcoded: the same
`context_lint.py` runs on a Kotlin/Gradle backend, a TS/Deno app and a C# engine.

    from skills_config import load
    config = load(repo)              # repo root; missing config -> every default
    config.premises_index_for("docs/accounts/premises.md")

The format is the one `skills/setup/skills-config.template.md` writes: `## Section` headings,
`- **Key:** value` bullets (a `<placeholder>`, `none` or an empty value reads as unset) and
markdown tables. Unknown sections and prose are ignored, so a hand-edited config keeps working.

Python 3 stdlib only, no third-party YAML — this file is vendored into `.github/scripts/` (and
beside the hooks) of the consuming repo.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

CONFIG_PATH = "docs/agents/skills-config.md"

# Defaults. Each one is quoted verbatim in docs/context-toolkit.md — change both together.
DEFAULTS = {
    "agent_instructions": "CLAUDE.md",
    "extra_root_surfaces": ["README.md"],
    "rules_dir": ".claude/rules",
    "skills_dir": ".claude/skills",
    "docs_root": "docs",
    "docs_index": "docs/index.md",
    "ephemeral_globs": ["docs/work/**"],
    "excluded_globs": [".claude/deep-plan/**", "**/eval/**"],
    "premises": "docs/{domain}/premises.md",
    "premises_index": "docs/{domain}/premises-index.md",
    "premise_fetch": "python3 .github/scripts/premise.py <id>",
    "index_write": "python3 .github/scripts/context_lint.py --write-indices",
    "failure_modes": "docs/planning/recurring-failure-modes.md",
    "intent_dir": "intent",
    "flows_dir": "docs/flows",
    "path_roots": [],
    "source_globs": [],
    "migration_dirs": [],
    "migration_pattern": r"\bV[0-9]{2,3}(?:__[a-z0-9_]+\.[a-z]+)?\b",
    # Telemetry. Lane A (git-only) is the default and needs no vendor: the trailers the commit gate
    # writes are mined out of the branch log. Lane B (otlp) is the cost axis, and only then do the
    # endpoint/key values below mean anything — the endpoint default is the OTLP one (a collector or
    # vendor agent on the machine); the header default is one measured example, not a requirement.
    "telemetry_lanes": "git-only",
    "default_branch": "main",
    "otel_endpoint": "http://localhost:4318",
    "otel_protocol": "http/protobuf",
    "otel_key_variable": "CLAUDE_CODE_OTEL_API_KEY",
    "otel_key_header": "dd-api-key",
    # Lint ratchet. Both gates are OFF until the repo names its production globs.
    "baseline_entry_pattern": "<ID>",
    "cpd_report": "build/reports/cpd/cpd.xml",
}

CEILINGS = {
    "agent_instructions_lines": 200,
    "always_on_bytes": 32768,
    "rule_lines": 150,
    "premise_lines": 40,
    "premise_bytes": 4096,
    "premise_warn_lines": 25,
}

CEILING_ALIASES = {
    "agent instructions lines": "agent_instructions_lines",
    "claude md lines": "agent_instructions_lines",
    "always on bytes": "always_on_bytes",
    "rule lines": "rule_lines",
    "premise lines": "premise_lines",
    "premise bytes": "premise_bytes",
    "premise warn lines": "premise_warn_lines",
}

PLACEHOLDER_RE = re.compile(r"^<.*>$")
UNSET = ("", "none", "n/a", "-", "—", "tbd")
BULLET_RE = re.compile(r"^\s*[-*]\s*\*\*(?P<label>[^*]+?)\*\*(?P<rest>.*)$")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _clean(value: str) -> str:
    value = COMMENT_RE.sub("", value)
    return value.replace("`", "").strip().strip(";,").strip()


def _is_set(value: str) -> bool:
    lowered = value.strip().lower()
    return bool(value.strip()) and lowered not in UNSET and not PLACEHOLDER_RE.match(value.strip())


def _split_list(value: str) -> list[str]:
    return [part for part in (p.strip() for p in re.split(r"[,;]", value)) if part]


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)
    bullets: dict[str, str] = field(default_factory=dict)
    tables: list[list[list[str]]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def strip_comments(text: str) -> str:
    """Blank out `<!-- … -->` while keeping the line structure — a comment may span several lines, and a
    per-line strip would leave its tail looking like a continuation of the bullet above it."""
    return COMMENT_RE.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def parse_sections(text: str) -> dict[str, Section]:
    """`## Heading` -> Section, with the bullets and tables of that heading already extracted."""
    text = strip_comments(text)
    sections: dict[str, Section] = {}
    current = Section("")
    fenced = False
    last_label: str | None = None
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            current = Section(line[3:].strip().lower())
            sections[current.title] = current
            last_label = None
            continue
        current.lines.append(line)
        if fenced:
            continue
        # An indented continuation line belongs to the bullet above it (the template wraps long values).
        if last_label and re.match(r"^\s+\S", line) and not line.lstrip().startswith(("-", "*", "|", "#")):
            tail = _clean(line)
            if tail:
                current.bullets[last_label] = f"{current.bullets[last_label]} {tail}".strip()
            continue
        if not line.strip() or line.lstrip().startswith(("|", "#")):
            last_label = None
        # A comment carries a `default: …` colon of its own; strip_comments already removed it above.
        bullet = BULLET_RE.match(line)
        if bullet:
            label = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", bullet.group("label").lower())).strip()
            # Three spellings reach here: `**Label:** value` (colon inside the bold),
            # `**Label**: value`, and `**Label** (optional): value`. Only a LEADING annotation and a
            # LEADING colon are separators — splitting on any colon would eat the scheme of a URL
            # (`https://…`) or half of a `key=value:pair`.
            rest = bullet.group("rest").lstrip()
            if rest.startswith("(") and ")" in rest:
                rest = rest[rest.index(")") + 1 :].lstrip()
            value = rest[1:] if rest.startswith(":") else rest
            if label and label not in current.bullets:
                current.bullets[label] = _clean(value)
            last_label = label if label in current.bullets else None
        elif line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            if not current.tables or current.tables[-1] is None:
                current.tables.append([])
            current.tables[-1].append([_clean(c) for c in cells])
        elif not line.strip() and current.tables:
            current.tables.append(None)  # type: ignore[arg-type]
    for section in sections.values():
        section.tables = [t for t in section.tables if t]
    return sections


@dataclass
class Domain:
    name: str
    globs: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)

    def matches(self, path: str) -> bool:
        path = path.replace(os.sep, "/")
        if any(glob_match(path, glob) for glob in self.globs):
            return True
        return not self.globs and f"/{self.name}/" in f"/{path}"


def glob_match(path: str, glob: str) -> bool:
    """`**` crosses directories, `*` does not, `{a,b}` alternates — the `paths:` frontmatter dialect."""
    return _glob_regex(glob).match(path) is not None


_GLOB_CACHE: dict[str, re.Pattern[str]] = {}


def _glob_regex(glob: str) -> re.Pattern[str]:
    cached = _GLOB_CACHE.get(glob)
    if cached is not None:
        return cached
    out: list[str] = []
    index = 0
    while index < len(glob):
        if glob.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif glob.startswith("**", index):
            out.append(".*")
            index += 2
        elif glob[index] == "*":
            out.append("[^/]*")
            index += 1
        elif glob[index] == "?":
            out.append("[^/]")
            index += 1
        elif glob[index] == "{" and "}" in glob[index:]:
            end = glob.index("}", index)
            out.append("(?:" + "|".join(re.escape(o.strip()) for o in glob[index + 1 : end].split(",")) + ")")
            index = end + 1
        else:
            out.append(re.escape(glob[index]))
            index += 1
    compiled = re.compile("^" + "".join(out) + "$")
    _GLOB_CACHE[glob] = compiled
    return compiled


def _pattern_regex(pattern: str, wildcard_stem: bool = False) -> re.Pattern[str]:
    """`docs/{domain}/premises.md` -> a regex capturing the domain; `wildcard_stem` also accepts
    `premises-cancellation.md` (a domain past the split threshold partitions its file).

    The placeholder spans one or more path segments, so a repo that writes `docs/{domain}/premises.md`
    in its config still matches a `docs/domain/accounts/premises.md` tree.
    """
    parts = re.split(r"(\{[a-z_]+\})", pattern)
    out = []
    for part in parts:
        if re.fullmatch(r"\{[a-z_]+\}", part):
            out.append("(?P<domain>[^/]+(?:/[^/]+)*)")
        else:
            out.append(re.escape(part))
    regex = "".join(out)
    if wildcard_stem:
        regex = re.sub(r"([A-Za-z0-9_\\-]+)(\\\.[A-Za-z0-9]+)$", r"\1[^/]*\2", regex)
    return re.compile("^" + regex + "$")


@dataclass
class Config:
    repo: str = "."
    present: bool = False
    agent_instructions: str = DEFAULTS["agent_instructions"]
    rules_dir: str = DEFAULTS["rules_dir"]
    skills_dir: str = DEFAULTS["skills_dir"]
    docs_root: str = DEFAULTS["docs_root"]
    docs_index: str = DEFAULTS["docs_index"]
    ephemeral_globs: list[str] = field(default_factory=lambda: list(DEFAULTS["ephemeral_globs"]))
    excluded_globs: list[str] = field(default_factory=lambda: list(DEFAULTS["excluded_globs"]))
    premises: str = DEFAULTS["premises"]
    premises_index: str = DEFAULTS["premises_index"]
    premise_fetch: str = DEFAULTS["premise_fetch"]
    index_write: str = DEFAULTS["index_write"]
    schema: str | None = None
    failure_modes: str = DEFAULTS["failure_modes"]
    intent_dir: str = DEFAULTS["intent_dir"]
    flows_dir: str = DEFAULTS["flows_dir"]
    path_roots: list[str] = field(default_factory=list)
    source_globs: list[str] = field(default_factory=list)
    migration_dirs: list[str] = field(default_factory=list)
    migration_pattern: str = DEFAULTS["migration_pattern"]
    telemetry_lanes: str = DEFAULTS["telemetry_lanes"]
    default_branch: str = DEFAULTS["default_branch"]
    otel_endpoint: str = DEFAULTS["otel_endpoint"]
    otel_protocol: str = DEFAULTS["otel_protocol"]
    otel_resource_attributes: str | None = None
    otel_key_variable: str = DEFAULTS["otel_key_variable"]
    otel_key_header: str = DEFAULTS["otel_key_header"]
    otel_key_repo: str | None = None
    lint_config_files: list[str] = field(default_factory=list)
    baseline_globs: list[str] = field(default_factory=list)
    baseline_entry_pattern: str = DEFAULTS["baseline_entry_pattern"]
    production_globs: list[str] = field(default_factory=list)
    cpd_command: str | None = None
    cpd_report: str = DEFAULTS["cpd_report"]
    domains: list[Domain] = field(default_factory=list)
    sensitive: list[str] = field(default_factory=list)
    ceilings: dict[str, int] = field(default_factory=lambda: dict(CEILINGS))
    notes: list[str] = field(default_factory=list)

    # ── derived ───────────────────────────────────────────────────────────────────────────────

    @property
    def otlp_enabled(self) -> bool:
        """Is lane B on? The OTLP env block and the header helper are installed only when it is."""
        return self.telemetry_lanes in ("otlp", "both")

    @property
    def root_surfaces(self) -> tuple[str, ...]:
        return (self.agent_instructions, *DEFAULTS["extra_root_surfaces"])

    @property
    def premises_index_basename(self) -> str:
        return os.path.basename(self.premises_index)

    def is_premises(self, path: str) -> bool:
        return (
            _pattern_regex(self.premises, wildcard_stem=True).match(path) is not None
            and os.path.basename(path) != self.premises_index_basename
        )

    def is_premises_index(self, path: str) -> bool:
        return _pattern_regex(self.premises_index).match(path) is not None

    def domain_of_premises(self, path: str) -> str | None:
        match = _pattern_regex(self.premises, wildcard_stem=True).match(path)
        return match.group("domain") if match and "domain" in (match.groupdict() or {}) else None

    def premises_index_for(self, premises_path: str) -> str:
        """The index that covers a premises file — beside it, whatever the pattern's shape."""
        return f"{os.path.dirname(premises_path)}/{self.premises_index_basename}".lstrip("/")

    def premises_index_of_domain(self, domain: str) -> str:
        return self.premises_index.replace("{domain}", domain).replace("{module}", domain)

    def premises_of_domain(self, domain: str) -> str:
        return self.premises.replace("{domain}", domain).replace("{module}", domain)

    def schema_of_domain(self, domain: str) -> str | None:
        if not self.schema:
            return None
        return self.schema.replace("{domain}", domain).replace("{module}", domain)

    def fetch_command(self, premise_id: str) -> str:
        return self.premise_fetch.replace("<id>", premise_id)

    def domain_names(self) -> list[str]:
        return [d.name for d in self.domains]

    def domain_of_path(self, path: str) -> str | None:
        for domain in self.domains:
            if domain.matches(path):
                return domain.name
        return None

    def is_ephemeral(self, path: str) -> bool:
        return any(glob_match(path, glob) for glob in self.ephemeral_globs)

    def is_excluded(self, path: str) -> bool:
        return any(glob_match(path, glob) for glob in self.excluded_globs)


def default_repo(start: str | None = None) -> str:
    """The repo root: walk up from `start` (default cwd) to the first directory holding `.git`."""
    current = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(start or os.getcwd())
        current = parent


def load(repo: str = ".", path: str | None = None) -> Config:
    """Read the config of `repo`. A missing file is not an error — every default applies."""
    config = Config(repo=os.path.abspath(repo))
    full = path if path else os.path.join(config.repo, CONFIG_PATH)
    try:
        with open(full, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        config.notes.append(f"no {CONFIG_PATH} — every default applies")
        return config
    config.present = True
    sections = parse_sections(text)
    _read_docs_layout(config, sections.get("docs layout"))
    _read_toolkit(config, sections.get("context toolkit"))
    _read_intent(config, sections.get("intent"))
    _read_telemetry(config, sections.get("telemetry"))
    _read_lint_ratchet(config, sections.get("lint ratchet"))
    _read_flows(config, sections.get("flows"))
    _read_domains(config, sections.get("domains"))
    _read_sensitive(config, sections.get("sensitive domains"))
    return config


def _bullet(section: Section | None, *labels: str) -> str | None:
    if section is None:
        return None
    for label in labels:
        value = section.bullets.get(label)
        if value is not None and _is_set(value):
            return value
    return None


def _read_docs_layout(config: Config, section: Section | None) -> None:
    for attr, labels in (
        ("premises", ("premises",)),
        ("premises_index", ("premises index",)),
        ("premise_fetch", ("premise fetch command",)),
        ("index_write", ("premises index write command",)),
        ("schema", ("schema",)),
        ("rules_dir", ("rules dir",)),
    ):
        value = _bullet(section, *labels)
        if value:
            setattr(config, attr, value.rstrip("/") if attr == "rules_dir" else value)
    planning = _bullet(section, "planning")
    if planning:
        found = re.findall(r"[A-Za-z0-9_./{}-]+\.md", planning)
        for candidate in found:
            if "failure" in candidate:
                config.failure_modes = candidate


def _read_toolkit(config: Config, section: Section | None) -> None:
    if section is None:
        return
    for attr, labels in (
        ("agent_instructions", ("agent instructions file", "agent instructions")),
        ("skills_dir", ("skills dir",)),
        ("docs_root", ("docs root",)),
        ("docs_index", ("docs index",)),
        ("migration_pattern", ("migration version pattern",)),
    ):
        value = _bullet(section, *labels)
        if value:
            setattr(config, attr, value.rstrip("/") if attr.endswith("_dir") or attr == "docs_root" else value)
    for attr, labels in (
        ("ephemeral_globs", ("ephemeral docs",)),
        ("excluded_globs", ("never a surface", "excluded")),
        ("path_roots", ("path resolution roots",)),
        ("source_globs", ("source globs",)),
        ("migration_dirs", ("migration dirs",)),
    ):
        value = _bullet(section, *labels)
        if value:
            setattr(config, attr, _split_list(value))
    for table in section.tables:
        header = " ".join(table[0]).lower()
        if "ceiling" not in header:
            continue
        for row in table[1:]:
            if len(row) < 2:
                continue
            key = CEILING_ALIASES.get(re.sub(r"[^a-z0-9]+", " ", row[0].lower()).strip())
            if key and row[1].strip().isdigit():
                config.ceilings[key] = int(row[1].strip())


def _read_intent(config: Config, section: Section | None) -> None:
    value = _bullet(section, "intent dir")
    if value:
        config.intent_dir = value.rstrip("/")


TELEMETRY_LANES = ("git-only", "otlp", "both")
# The bullets that only mean something on lane B — they are what an older config used to declare it.
OTLP_LABELS = ("endpoint", "protocol", "resource attributes", "key variable", "key header", "key repo")


def _read_telemetry(config: Config, section: Section | None) -> None:
    """`## Telemetry` — which lanes are on, the branch the trailer miner reads, and the OTLP destination."""
    if section is None:
        return
    lanes = _bullet(section, "lanes")
    if lanes:
        if lanes.strip().lower() in TELEMETRY_LANES:
            config.telemetry_lanes = lanes.strip().lower()
        else:
            config.notes.append(
                f"Telemetry › Lanes {lanes!r} is not one of {TELEMETRY_LANES} — using {config.telemetry_lanes}"
            )
    elif any(_bullet(section, label) for label in OTLP_LABELS):
        # No `Lanes` line, but the section names an OTLP destination: that repo configured lane B
        # before the field existed, and reading it as `git-only` would silently switch its export off.
        config.telemetry_lanes = "both"
    for attr, labels in (
        ("default_branch", ("default branch",)),
        ("otel_endpoint", ("endpoint",)),
        ("otel_protocol", ("protocol",)),
        ("otel_resource_attributes", ("resource attributes",)),
        ("otel_key_variable", ("key variable",)),
        ("otel_key_header", ("key header",)),
        ("otel_key_repo", ("key repo",)),
    ):
        value = _bullet(section, *labels)
        if value:
            setattr(config, attr, value.rstrip("/") if attr == "otel_endpoint" else value)


def _read_lint_ratchet(config: Config, section: Section | None) -> None:
    """`## Lint ratchet` — which files are the ratchet, which files are production, how to read a baseline."""
    if section is None:
        return
    for attr, labels in (
        ("lint_config_files", ("lint config files", "lint config")),
        ("baseline_globs", ("baseline files", "baselines")),
        ("production_globs", ("production globs", "production")),
    ):
        value = _bullet(section, *labels)
        if value:
            setattr(config, attr, _split_list(value))
    for attr, labels in (
        ("baseline_entry_pattern", ("baseline entry pattern",)),
        ("cpd_command", ("cpd command",)),
        ("cpd_report", ("cpd report",)),
    ):
        value = _bullet(section, *labels)
        if value:
            setattr(config, attr, value)


def _read_flows(config: Config, section: Section | None) -> None:
    value = _bullet(section, "flows dir")
    if value:
        config.flows_dir = value.rstrip("/")


def _read_domains(config: Config, section: Section | None) -> None:
    if section is None:
        return
    for table in section.tables:
        for row in table:
            if len(row) < 2 or not row[0]:
                continue
            name = row[0].strip().lower()
            if name in ("domain", "name") or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
                continue
            globs = [g for g in _split_list(row[1]) if _is_set(g)]
            terms = [t.lower() for t in _split_list(row[2])] if len(row) > 2 and _is_set(row[2]) else []
            config.domains.append(Domain(name=name, globs=globs, terms=terms))


def _read_sensitive(config: Config, section: Section | None) -> None:
    if section is None:
        return
    for line in section.lines:
        text = _clean(line)
        if not text or text.startswith((">", "|", "#", "-", "*")):
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*(\s*,\s*[a-z0-9][a-z0-9_-]*)*", text):
            continue
        names = [n for n in _split_list(text) if n not in ("none", "empty")]
        if names:
            config.sensitive = names
        return
