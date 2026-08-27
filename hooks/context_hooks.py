#!/usr/bin/env python3
"""Context hooks — put a domain's premises index on the READING path.

`paths:` rules only fire when a file is read or edited. A prompt that names a domain, a query that
names one of its tables, or the first edit in an area nobody consulted carry no context at all.
These hooks close that gap: when the text names a domain, they inject that domain's premises index
as additional context, once per domain per session, and never block. Everything is fail-open: any
bad payload, missing file or exception exits 0 with no stdout.

Nothing here is stack-specific. Which domains exist, how a path maps to one, which extra words name
one in prose, and which domains are sensitive all come from `docs/agents/skills-config.md`
(`skills_config.py`, vendored beside this file). With no config the hooks stay silent.

Wired in `.claude/settings.json` on UserPromptSubmit, PreToolUse (Write|mcp__.*) and PostToolUse
(Read|Edit|Write|Bash) — see `settings/hooks.json`. Two argv services back the PR gate:

    context_hooks.py gate-input        # stdin: the PreToolUse payload -> "<gh pr subcommand>\\n<ttl>"
    context_hooks.py sensitive-domains # stdin: changed paths -> the sensitive domains they touch
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import time
from fnmatch import fnmatch

# The config reader is vendored beside this hook by install.sh; in the skills repo itself it lives in
# ci/. Any failure here degrades to "no config" — the hooks then stay silent instead of guessing.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (_HERE, os.path.join(_HERE, "..", "ci"), os.path.join(_HERE, "..", "..", ".github", "scripts")):
    if os.path.exists(os.path.join(_candidate, "skills_config.py")):
        sys.path.insert(0, os.path.abspath(_candidate))
        break
try:
    import skills_config
except Exception:  # pragma: no cover - fail-open import guard
    skills_config = None

ENGAGED_TTL_MIN = 240
SESSION_MAX_AGE_H = 24
INDEX_MAX_BYTES = 8192
MAX_DOMAINS_PER_EVENT = 3
STATE_DIR = os.path.join(".claude", ".context-hooks")

ENV_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Wrappers that keep the wrapped command in argv. Unwrapping them is not cosmetic: an argv the gate
# does not recognise means NO gate runs, which the old substring matcher did not risk.
ARGV_WRAPPERS = ("nohup", "sudo", "command", "time", "env", "xargs", "timeout", "stdbuf", "setsid")
DURATION_RE = re.compile(r"^[0-9]+(\.[0-9]+)?[smhd]?$")
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
SEGMENT_RE = re.compile(r"\n|;|&&|\|\||\||\$\(|\(|\)")

# Every injected message is one line; `{d}` is the domain, `{tokens}` what the text actually named.
NO_INDEX = "no premises index yet — read {premises}"
PROMPT_LEAD = "context-hooks: the prompt names {d} ({tokens}); "
PROMPT_HEAD = PROMPT_LEAD + (
    "the premises index {index} follows — open {premises} by H2 title when a premise is relevant."
)
PROMPT_MISS = PROMPT_LEAD + NO_INDEX + " (H2 = premise titles) before reasoning about this domain."
QUERY_LEAD = "context-hooks: the query names {tokens} → domain {d}.{schema} "
QUERY_HEAD = QUERY_LEAD + "Premises index follows."
QUERY_MISS = QUERY_LEAD + "No premises index yet — read {premises}."
QUERY_SCHEMA = " Exact names and types: {schema} — check them before writing the query."
NUDGE = "context-hooks: first edit in {d} this session without consulting its premises — read {premises}{schema}."
CHARTER_MSG = "context-hooks: new context surface {path} — charter follows ({charter})."


def read_payload():
    if sys.stdin.isatty():
        return {}
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def disabled(name):
    parts = [p.strip() for p in os.environ.get("CONTEXT_HOOKS_DISABLE", "").split(",")]
    return "all" in parts or name in parts


def project_root(payload):
    return os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()


def load_config(root):
    """The repo's config, or None — every caller treats None as 'stay silent'."""
    if skills_config is None:
        return None
    try:
        return skills_config.load(root)
    except Exception:
        return None


class Session(object):
    """Sentinel files under .claude/.context-hooks/{session_id}/ — mtime is the clock."""

    def __init__(self, root, session_id):
        self.root = root
        self.dir = os.path.join(root, STATE_DIR, re.sub(r"[^A-Za-z0-9_-]", "_", session_id))

    def _path(self, kind, domain=None):
        return os.path.join(self.dir, kind if domain is None else "{}-{}".format(kind, domain))

    def exists(self, kind, domain=None):
        return os.path.exists(self._path(kind, domain))

    def fresh(self, kind, domain, ttl_min):
        try:
            return (time.time() - os.path.getmtime(self._path(kind, domain))) < ttl_min * 60
        except OSError:
            return False

    def touch(self, kind, domain=None):
        os.makedirs(self.dir, exist_ok=True)
        path = self._path(kind, domain)
        open(path, "a").close()
        now = time.time()
        os.utime(path, (now, now))
        os.utime(self.dir, (now, now))
        self.prune()

    def prune(self):
        base = os.path.join(self.root, STATE_DIR)
        cutoff = time.time() - SESSION_MAX_AGE_H * 3600
        try:
            names = os.listdir(base)
        except OSError:
            return
        for name in names:
            sibling = os.path.join(base, name)
            if sibling == self.dir or not os.path.isdir(sibling):
                continue
            try:
                stale = os.path.getmtime(sibling) < cutoff
            except OSError:
                continue
            if stale:
                shutil.rmtree(sibling, ignore_errors=True)


# ── domain detection ──────────────────────────────────────────────────────────────────────────────


def word_re(term):
    return re.compile(r"(?<![a-z0-9_])" + re.escape(term.lower()) + r"(?![a-z0-9_])")


def domains_in_text(text, config, bare_words):
    """[(domain, tokens)] ordered by mention count.

    A domain's configured `Prompt terms` always count — that is where a repo puts the table names and
    the words its people actually use. The BARE domain name only counts on the prompt path, and only
    for a sensitive domain: elsewhere it is prose, not a reference.
    """
    lowered = (text or "").lower()
    counts = {}
    hits = {}

    def add(domain, token, times):
        if times <= 0:
            return
        counts[domain] = counts.get(domain, 0) + times
        hits.setdefault(domain, []).append(token)

    for domain in config.domains:
        for term in domain.terms:
            if not bare_words and term == domain.name:
                continue  # a bare domain word is prose, not a query reference
            add(domain.name, term, len(word_re(term).findall(lowered)))
        if bare_words and domain.name in config.sensitive:
            add(domain.name, domain.name, len(word_re(domain.name).findall(lowered)))
    ordered = sorted(counts, key=lambda d: (-counts[d], d))
    return [(d, sorted(set(hits[d]))) for d in ordered[:MAX_DOMAINS_PER_EVENT]]


def relative(path, root):
    path = (path or "").replace(os.sep, "/")
    root = root.replace(os.sep, "/").rstrip("/")
    if path.startswith(root + "/"):
        path = path[len(root) + 1 :]
    return path[2:] if path.startswith("./") else path


def domain_of_path(path, root, config):
    rel = relative(path, root)
    docs_root = config.docs_root.rstrip("/") + "/"
    if not rel or rel.startswith(docs_root):
        return None
    return config.domain_of_path(rel)


def premises_path_re(config):
    """A regex over the configured premises pattern that captures the domain from a path or command."""
    parts = re.split(r"(\{[a-z_]+\})", config.premises)
    out = []
    for part in parts:
        out.append(r"([A-Za-z0-9_-]+)" if re.fullmatch(r"\{[a-z_]+\}", part) else re.escape(part))
    regex = "".join(out)
    return re.compile(re.sub(r"([A-Za-z0-9_\\-]+)(\\\.[A-Za-z0-9]+)$", r"\1[^/\\s]*\2", regex))


def existing(root, path):
    return bool(path) and os.path.exists(os.path.join(root, path))


# ── the hooks ─────────────────────────────────────────────────────────────────────────────────────


def index_block(root, config, domain, header, fallback):
    path = os.path.join(root, config.premises_index_of_domain(domain))
    try:
        if os.path.getsize(path) <= INDEX_MAX_BYTES:
            with open(path, encoding="utf-8") as handle:
                return header + "\n\n" + handle.read().strip(), True
    except OSError:
        pass
    return fallback, False


def emit(event, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def remind(session, root, config, text, bare_words, event, templates):
    head, miss = templates
    blocks = []
    for domain, tokens in domains_in_text(text, config, bare_words):
        if session.exists("reminded", domain) or session.fresh("engaged", domain, ENGAGED_TTL_MIN):
            continue
        schema = config.schema_of_domain(domain)
        fields = {
            "d": domain,
            "tokens": ", ".join(tokens),
            "premises": config.premises_of_domain(domain),
            "index": config.premises_index_of_domain(domain),
            "schema": QUERY_SCHEMA.format(schema=schema) if existing(root, schema) else "",
        }
        block, injected = index_block(root, config, domain, head.format(**fields), miss.format(**fields))
        blocks.append(block)
        session.touch("reminded", domain)
        if injected:
            session.touch("engaged", domain)
    if blocks:
        emit(event, "\n\n".join(blocks))


def nudge(session, root, config, path):
    domain = domain_of_path(path, root, config)
    if domain is None or session.exists("nudged", domain) or session.fresh("engaged", domain, ENGAGED_TTL_MIN):
        return
    index = config.premises_index_of_domain(domain)
    if existing(root, index):
        premises = "{} (titles; open {} by title when relevant)".format(index, config.premises_of_domain(domain))
    else:
        premises = "{} (H2 = premise titles)".format(config.premises_of_domain(domain))
    schema_path = config.schema_of_domain(domain)
    schema = " and {} for the exact names".format(schema_path) if existing(root, schema_path) else ""
    session.touch("nudged", domain)
    emit("PostToolUse", NUDGE.format(d=domain, premises=premises, schema=schema))


def charter(session, root, config, tool_input):
    if session.exists("charter-shown"):
        return
    rel = relative(tool_input.get("file_path") or "", root)
    if not rel or os.path.exists(os.path.join(root, rel)):
        return
    charter_path = config.rules_dir.rstrip("/") + "/context.md"
    try:
        with open(os.path.join(root, charter_path), encoding="utf-8") as handle:
            raw = handle.read()
    except OSError:
        return
    parts = raw.split("---")
    globs = []
    for line in parts[1].splitlines() if len(parts) > 2 else []:
        if line.startswith("paths:"):
            try:
                globs = json.loads(line.split(":", 1)[1].strip())
            except ValueError:
                globs = []
    body = "---".join(parts[2:]).strip()
    if not body or not any(glob_match(rel, glob) for glob in globs):
        return
    session.touch("charter-shown")
    emit("PreToolUse", CHARTER_MSG.format(path=rel, charter=charter_path) + "\n\n" + body)


def glob_match(rel, pattern):
    # fnmatch's `**` needs at least one segment, so `docs/**/*.md` must also be tried without it.
    return fnmatch(rel, pattern) or ("**/" in pattern and fnmatch(rel, pattern.replace("**/", "", 1)))


def mark(session, config, text):
    if disabled("mark"):
        return
    for match in premises_path_re(config).finditer((text or "").replace(os.sep, "/")):
        session.touch("engaged", match.group(1))


def on_prompt(payload, root, config, session):
    prompt = payload.get("prompt") or ""
    if disabled("remind") or prompt.lstrip().startswith("/"):
        return
    remind(session, root, config, prompt, True, "UserPromptSubmit", (PROMPT_HEAD, PROMPT_MISS))


def on_pre_tool(payload, root, config, session):
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if tool == "Write" and not disabled("charter"):
        charter(session, root, config, tool_input)
    elif tool.startswith("mcp__") and not disabled("query"):
        remind(session, root, config, json.dumps(tool_input), False, "PreToolUse", (QUERY_HEAD, QUERY_MISS))


def on_post_tool(payload, root, config, session):
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if tool == "Bash":
        command = tool_input.get("command") or ""
        mark(session, config, command)
        if not disabled("query"):
            remind(session, root, config, command, False, "PostToolUse", (QUERY_HEAD, QUERY_MISS))
        return
    if tool not in ("Read", "Edit", "Write"):
        return
    path = tool_input.get("file_path") or ""
    if premises_path_re(config).search(path.replace(os.sep, "/")):
        mark(session, config, path)
        return
    if tool in ("Edit", "Write") and not disabled("nudge"):
        nudge(session, root, config, path)


# ── argv services for the PR gate ─────────────────────────────────────────────────────────────────


def gh_pr_subcommand(command):
    """`create`/`ready` only when argv says so — `echo "gh pr create"` and heredoc bodies must not match."""
    lines = []
    terminator = None
    for line in (command or "").split("\n"):
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        lines.append(line)
        opener = HEREDOC_RE.search(line)
        if opener:
            terminator = opener.group(2)
    for segment in SEGMENT_RE.split("\n".join(lines)):
        tokens = argv_of(segment)
        if tokens[:1] in (["bash"], ["sh"], ["zsh"]) and "-c" in tokens:
            tokens = argv_of(tokens[-1])
        if tokens[:2] == ["gh", "pr"] and tokens[2:3] and tokens[2] in ("create", "ready"):
            return tokens[2]
    return ""


def argv_of(segment):
    try:
        tokens = shlex.split(segment)
    except ValueError:
        # Unbalanced quote: the segment was cut mid-string (`--body "$(...`). Words are enough here.
        tokens = segment.replace('"', " ").replace("'", " ").split()
    peeled = True
    while tokens and peeled:
        peeled = False
        while tokens and ENV_PREFIX_RE.match(tokens[0]):
            tokens.pop(0)
            peeled = True
        if tokens and tokens[0] in ARGV_WRAPPERS:
            wrapper = tokens.pop(0)
            peeled = True
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            if wrapper == "timeout" and tokens and DURATION_RE.match(tokens[0]):
                tokens.pop(0)
    return tokens


def gate_input(payload):
    print(gh_pr_subcommand((payload.get("tool_input") or {}).get("command") or ""))
    print(ENGAGED_TTL_MIN)


def sensitive_domains(raw, root):
    """stdin: one changed path per line -> the sensitive domains those paths touch, one per line."""
    config = load_config(root)
    if config is None or not config.sensitive:
        return
    paths = [line.strip() for line in (raw or "").split("\n") if line.strip()]
    if not paths:
        return
    by_name = {d.name: d for d in config.domains}
    found = []
    for name in config.sensitive:
        domain = by_name.get(name) or skills_config.Domain(name=name)
        if any(domain.matches(path) for path in paths):
            found.append(name)
    for name in sorted(set(found)):
        print(name)


def premises_paths(raw, root):
    """stdin: one domain name per line -> `<domain>\\t<index>\\t<premises>`, for the gate's message."""
    config = load_config(root)
    if config is None:
        return
    for name in [line.strip() for line in (raw or "").split("\n") if line.strip()]:
        print("%s\t%s\t%s" % (name, config.premises_index_of_domain(name), config.premises_of_domain(name)))


def main():
    mode = sys.argv[1:2]
    if mode in (["sensitive-domains"], ["premises-paths"]):
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        (sensitive_domains if mode == ["sensitive-domains"] else premises_paths)(raw, root)
        return
    payload = read_payload()
    if mode == ["gate-input"]:
        gate_input(payload)
        return
    session_id = payload.get("session_id") or ""
    event = payload.get("hook_event_name") or ""
    if not session_id or not event:
        return
    root = project_root(payload)
    config = load_config(root)
    if config is None:
        return
    session = Session(root, session_id)
    if event == "UserPromptSubmit":
        on_prompt(payload, root, config, session)
    elif event == "PreToolUse":
        on_pre_tool(payload, root, config, session)
    elif event == "PostToolUse":
        on_post_tool(payload, root, config, session)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
