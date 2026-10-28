"""Map a problem case onto a standard A3 problem-solving sheet."""

import re
from pathlib import PurePosixPath

from markupsafe import Markup, escape

from app.markdown import _inline, render_case_markdown

A3_BOXES = (
    ("background", "1. Background", "Why this problem is worth solving."),
    ("current", "2. Current Condition", "What happens with the approach in place today."),
    ("goal", "3. Goal", "The target condition the design must meet."),
    ("analysis", "4. Root-Cause Analysis", "Why the simple approach fails."),
    ("countermeasures", "5. Countermeasures", "The patterns and architecture that address those causes."),
    ("plan", "6. Implementation Plan", "How the countermeasures are carried out."),
    ("followup", "7. Follow-up", "What to check after the change, including remaining failures."),
)


def case_title(body: str) -> str:
    for line in (body or "").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def build_a3(body: str, repository_path: str, pattern_urls: dict[str, str]) -> list[dict]:
    linked = rewrite_case_links(body or "", repository_path, pattern_urls)
    grouped: dict[str, list[tuple[str, str]]] = {key: [] for key, _title, _purpose in A3_BOXES}
    for heading, section in _sections(linked):
        kind = _box_for(heading)
        if kind == "gap":
            prose, table = _split_table(section)
            if prose:
                grouped["current"].append((heading, prose))
            if table:
                grouped["analysis"].append((heading, table))
            elif not prose:
                grouped["current"].append((heading, section))
            continue
        grouped[kind].append((heading, section))
    boxes = []
    for key, title, purpose in A3_BOXES:
        parts = grouped[key]
        html = Markup("")
        if parts:
            rendered = []
            for heading, section in parts:
                rendered.append(f"<h3>{_inline(str(escape(heading)))}</h3>")
                rendered.append(str(render_case_markdown(section)))
            html = Markup("\n".join(rendered))
        boxes.append({"key": key, "title": title, "purpose": purpose, "html": html})
    return boxes


def linked_pattern_paths(body: str, repository_path: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for base in _link_bases(repository_path):
        for _label, url in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body or ""):
            path = resolve_case_path(base, url)
            if path and path not in seen:
                seen.add(path)
                found.append(path)
    return found


def _link_bases(repository_path: str) -> list[str]:
    bases = [repository_path]
    parent = PurePosixPath(repository_path).parent
    if parent.name in {"concerns", "exercises"}:
        bases.append(str(parent.parent / PurePosixPath(repository_path).name))
    return bases


def resolve_case_path(repository_path: str, url: str) -> str:
    target = url.split("#", 1)[0].strip()
    if not target.endswith(".md") or "://" in target:
        return ""
    combined = PurePosixPath(repository_path).parent / target
    parts: list[str] = []
    for part in combined.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in {"", "."}:
            parts.append(part)
    return "/".join(parts)


def _sections(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []
    for line in body.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if heading:
                sections.append((heading, "\n".join(buffer).strip()))
            heading = line[3:].strip()
            buffer = []
            continue
        if heading:
            buffer.append(line)
    if heading:
        sections.append((heading, "\n".join(buffer).strip()))
    return sections


def _box_for(heading: str) -> str:
    label = heading.lower()
    if "business problem" in label or label == "background":
        return "background"
    if any(word in label for word in ("hard requirement", "goal", "target condition")):
        return "goal"
    if any(word in label for word in ("why one pattern", "not enough", "root cause", "root-cause", "cause analysis")):
        return "gap"
    if any(word in label for word in ("failure", "follow-up", "follow up", "effect confirmation")):
        return "followup"
    if any(word in label for word in ("pattern mix", "patterns used", "countermeasure")):
        return "countermeasures"
    if any(
        word in label
        for word in ("flow", "sketch", "implementation", "strategy", "concurrency", "study path", "typescript")
    ):
        return "plan"
    if any(word in label for word in ("architecture", "platform", "subsystem", "deep dive", "overview", "engine")):
        return "countermeasures"
    return "analysis"


def _split_table(section: str) -> tuple[str, str]:
    lines = section.splitlines()
    table_at = next((index for index, line in enumerate(lines) if line.strip().startswith("|")), None)
    if table_at is None:
        return section.strip(), ""
    prose = "\n".join(line for line in lines if not line.strip().startswith("|")).strip()
    table = "\n".join(line for line in lines if line.strip().startswith("|")).strip()
    return prose, table


def rewrite_case_links(body: str, repository_path: str, pattern_urls: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        for base in _link_bases(repository_path):
            target = pattern_urls.get(resolve_case_path(base, url))
            if target:
                return f"[{label}]({target})"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace, body)
