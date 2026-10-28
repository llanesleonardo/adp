import re

from markupsafe import Markup, escape


def _inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)

    def link(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        if url.startswith("http://") or url.startswith("https://"):
            return f'<a href="{url}" rel="noopener noreferrer">{label}</a>'
        if url.startswith("/patterns/") and ":" not in url:
            return f'<a href="{url}">{label}</a>'
        return label

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)


def render_markdown(text: str | None) -> Markup:
    if not text:
        return Markup("")
    html: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html.append("</ul>")
            in_list = False

    for raw_line in str(text).replace("\r\n", "\n").split("\n"):
        line = str(escape(raw_line))
        if line.startswith("### "):
            close_list()
            html.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            close_list()
            html.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            close_list()
            html.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{_inline(line[2:])}</li>")
        elif line.strip() == "":
            close_list()
        else:
            close_list()
            html.append(f"<p>{_inline(line)}</p>")
    close_list()
    return Markup("\n".join(html))


def render_case_markdown(text: str | None) -> Markup:
    """Render the tables, lists, and sketches used in a problem case."""
    if not text:
        return Markup("")
    lines = str(text).replace("\r\n", "\n").split("\n")
    html: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            language = line[3:].strip() or "code"
            index += 1
            block: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            caption = "Diagram" if language == "mermaid" else language
            html.append(f'<p class="meta">{escape(caption)}</p><pre><code>{escape(chr(10).join(block))}</code></pre>')
            continue
        if _table_starts(lines, index):
            table, index = _render_table(lines, index)
            html.append(table)
            continue
        if line.startswith("### "):
            html.append(f"<h3>{_inline(str(escape(line[4:])))}</h3>")
            index += 1
            continue
        if line.startswith("## "):
            html.append(f"<h2>{_inline(str(escape(line[3:])))}</h2>")
            index += 1
            continue
        if line.startswith("# "):
            html.append(f"<h1>{_inline(str(escape(line[2:])))}</h1>")
            index += 1
            continue
        if line.startswith("> "):
            html.append(f"<blockquote><p>{_inline(str(escape(line[2:])))}</p></blockquote>")
            index += 1
            continue
        if re.match(r"^\d+\. ", line):
            items: list[str] = []
            while index < len(lines) and re.match(r"^\d+\. ", lines[index]):
                item = re.sub(r"^\d+\. ", "", lines[index])
                items.append(f"<li>{_inline(str(escape(item)))}</li>")
                index += 1
            html.append("<ol>" + "".join(items) + "</ol>")
            continue
        if line.startswith("- ") or line.startswith("* "):
            items = []
            while index < len(lines) and (lines[index].startswith("- ") or lines[index].startswith("* ")):
                items.append(f"<li>{_inline(str(escape(lines[index][2:])))}</li>")
                index += 1
            html.append("<ul>" + "".join(items) + "</ul>")
            continue
        if line.strip() == "":
            index += 1
            continue
        paragraph: list[str] = []
        while index < len(lines) and lines[index].strip() and not _block_boundary(lines, index):
            paragraph.append(str(escape(lines[index])))
            index += 1
        html.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return Markup("\n".join(html))


def _block_boundary(lines: list[str], index: int) -> bool:
    line = lines[index]
    return (
        line.startswith("```")
        or line.startswith("#")
        or line.startswith("- ")
        or line.startswith("* ")
        or bool(re.match(r"^\d+\. ", line))
        or _table_starts(lines, index)
    )


def _table_starts(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines) or not lines[index].strip().startswith("|"):
        return False
    return bool(re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1]))


def _render_table(lines: list[str], index: int) -> tuple[str, int]:
    raw: list[str] = []
    while index < len(lines) and lines[index].strip().startswith("|"):
        raw.append(lines[index])
        index += 1
    rows = [_split_row(row) for row in raw]
    header = rows[0]
    body = [row for row in rows[2:] if any(cell.strip() for cell in row)]
    head = "".join(f"<th>{_inline(str(escape(cell)))}</th>" for cell in header)
    rendered = [f"<thead><tr>{head}</tr></thead>", "<tbody>"]
    for row in body:
        cells = row + [""] * (len(header) - len(row))
        rendered.append("<tr>" + "".join(f"<td>{_inline(str(escape(cell)))}</td>" for cell in cells[: len(header)]) + "</tr>")
    rendered.append("</tbody>")
    return '<div class="table-wrap"><table>' + "".join(rendered) + "</table></div>", index


def _split_row(row: str) -> list[str]:
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    return cells
