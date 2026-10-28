"""Turn an already written Mermaid flowchart into a diagram-building exercise."""

import re

_FENCE = re.compile(r"```mermaid\s*(.*?)```", re.S)
_NODE = re.compile(r"^([A-Za-z_][\w-]*)\s*[\[\(\{]+(.*)$")
_ID = re.compile(r"^[A-Za-z_][\w-]*")


def diagram_spec(markdown: str) -> dict | None:
    """Return the first complete flowchart, or nothing when the diagram is unfinished."""
    match = _FENCE.search(markdown or "")
    if match is None:
        return None
    spec = _flowchart(match.group(1))
    if spec is None:
        return None
    explanations = _explanations(markdown, spec["nodes"])
    for node in spec["nodes"]:
        node["explain"] = explanations.get(node["id"]) or _fallback_explain(node["label"])
    return spec


def public_diagram_spec(spec: dict) -> dict:
    return {
        "kind": "diagram",
        "prompt": spec.get("prompt") or "Connect the parts to build the diagram.",
        "nodes": [
            {
                "id": node["id"],
                "label": node["label"],
                "x": node["x"],
                "y": node["y"],
                "explain": node.get("explain") or _fallback_explain(node["label"]),
            }
            for node in spec.get("nodes") or []
        ],
    }


def grade_diagram(spec: dict, links: list) -> dict:
    expected = {(edge["source"], edge["target"]) for edge in spec.get("edges") or []}
    given: set[tuple[str, str]] = set()
    for link in links or []:
        if not isinstance(link, dict):
            continue
        source = str(link.get("source") or "")
        target = str(link.get("target") or "")
        if source and target:
            given.add((source, target))
    correct = len(expected & given)
    wrong = sorted({source for source, _target in given - expected})
    return {
        "match": {"correct": correct, "total": len(expected), "wrong": wrong},
        "questions": {},
        "questionsCorrect": 0,
        "questionsTotal": 0,
        "passed": given == expected and bool(expected),
    }


def _flowchart(body: str) -> dict | None:
    labels: dict[str, str] = {}
    edges: list[tuple[str, str]] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("%%") or line.startswith("flowchart") or line.startswith("graph"):
            continue
        if line in {"subgraph", "end"} or line.startswith("subgraph ") or line.startswith("classDef") or line.startswith("style "):
            continue
        if "-->" in line or "---" in line:
            edges.extend(_edge_pairs(line))
            continue
        node = _NODE.match(line)
        if node is None:
            continue
        label = _clean_label(node.group(2))
        if label:
            labels[node.group(1)] = label
    if any(label.strip("?") == "" for label in labels.values()):
        return None
    for source, target in edges:
        labels.setdefault(source, source)
        labels.setdefault(target, target)
    unique_edges = list(dict.fromkeys(edges))
    if len(labels) < 2 or len(unique_edges) < 1:
        return None
    nodes = [{"id": node_id, "label": label} for node_id, label in labels.items()]
    edge_rows = [{"source": source, "target": target} for source, target in unique_edges]
    positions = _positions(nodes, edge_rows)
    for node in nodes:
        node.update(positions[node["id"]])
    return {
        "kind": "diagram",
        "prompt": "Connect the parts to build the diagram.",
        "nodes": nodes,
        "edges": edge_rows,
    }


def _edge_pairs(line: str) -> list[tuple[str, str]]:
    cleaned = re.sub(r"\|[^|]*\|", " ", line)
    pieces = re.split(r"\s+-->\s+|\s+---\s+", cleaned)
    ids = []
    for piece in pieces:
        piece = re.sub(r"^\|[^|]*\|\s*", "", piece).strip()
        found = _ID.match(piece)
        if found:
            ids.append(found.group(0))
    return [(ids[index], ids[index + 1]) for index in range(len(ids) - 1)]


def _clean_label(raw: str) -> str:
    text = raw.strip().strip("\"'")
    text = re.sub(r"[\]\)\}]+$", "", text).strip().strip("\"'")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180]


def _positions(nodes: list[dict], edges: list[dict]) -> dict[str, dict]:
    ids = [node["id"] for node in nodes]
    outgoing = {node_id: [] for node_id in ids}
    layer = {node_id: 0 for node_id in ids}
    for edge in edges:
        if edge["source"] in outgoing and edge["target"] in layer:
            outgoing[edge["source"]].append(edge["target"])
    for _step in ids:
        for source, targets in outgoing.items():
            for target in targets:
                layer[target] = max(layer[target], layer[source] + 1)
    buckets: dict[int, list[str]] = {}
    for node_id, depth in layer.items():
        buckets.setdefault(depth, []).append(node_id)
    positions = {}
    for depth, group in buckets.items():
        for index, node_id in enumerate(group):
            positions[node_id] = {"x": depth * 230, "y": index * 116}
    return positions


_STOP = {
    "the", "and", "for", "with", "from", "this", "that", "into", "over", "your",
    "each", "when", "then", "than", "per", "via", "not", "are", "one",
}


def _explanations(markdown: str, nodes: list[dict]) -> dict[str, str]:
    snippets = _snippets(markdown)
    found = {}
    for node in nodes:
        text = _best_snippet(node["label"], snippets)
        if text:
            found[node["id"]] = text
    return found


def _snippets(markdown: str) -> list[str]:
    text = re.sub(r"```.*?```", "\n", markdown or "", flags=re.S)
    snippets = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or set(line) <= {"-", "|", ":", " "}:
            continue
        if line.startswith("|"):
            cells = [_plain(cell) for cell in line.strip().strip("|").split("|")]
            cells = [cell for cell in cells if cell and not set(cell) <= {"-", ":", " "}]
            if len(cells) >= 2:
                snippets.append(". ".join(cells) + ".")
            continue
        line = re.sub(r"^[-*\d.]+\s*", "", line)
        plain = _plain(line)
        if len(plain) > 24:
            snippets.append(plain)
    return snippets


def _plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")
    text = text.replace("`", "").replace("**", "")
    text = re.sub(r"(?<!\*)\*(?!\*)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _label_tokens(label: str) -> list[str]:
    without_notes = re.sub(r"\([^)]*\)", " ", label or "")
    words = re.findall(r"[A-Za-z][A-Za-z0-9+]{2,}", without_notes)
    tokens = []
    for word in words:
        lowered = word.lower()
        if lowered not in _STOP and lowered not in tokens:
            tokens.append(lowered)
    return tokens


def _best_snippet(label: str, snippets: list[str]) -> str:
    tokens = _label_tokens(label)
    if not tokens:
        return ""
    scored = []
    for snippet in snippets:
        lowered = snippet.lower()
        hits = sum(1 for token in tokens if token in lowered)
        if hits:
            scored.append((hits, -len(snippet), snippet))
    if not scored:
        return ""
    scored.sort(reverse=True)
    text = scored[0][2]
    if len(text) > 520:
        text = text[:517].rsplit(" ", 1)[0] + "."
    return text


def _fallback_explain(label: str) -> str:
    detail = ""
    paren = re.search(r"\(([^)]+)\)", label or "")
    if paren:
        detail = f" It includes {paren.group(1).strip()}."
    name = (label or "This block").strip()
    return f"{name} is one part of this diagram.{detail} Use the written case to decide which lines should meet it."
