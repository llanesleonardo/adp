"""A small text PDF for one person's exercise result."""


def render_result_pdf(report: dict) -> bytes:
    lines = [report["title"], "", "Result", report["summary"], "", "Notes"]
    lines.extend(_wrap(report["note"]) or ["No notes yet."])
    if report["lines"]:
        lines.extend(["", "Lines you drew"])
        lines.extend(report["lines"])
    if report["questions"]:
        lines.extend(["", "Questions"])
        for question in report["questions"]:
            mark = "Correct" if question["ok"] else "Try again"
            lines.extend(_wrap(f"{question['prompt']} — {question['choice']} ({mark})"))
    if report["kind"] == "prompt" and report["prompt"]:
        lines.extend(["", "Exercise", *(_wrap(report["prompt"]) or [])])
    if report["blocks"]:
        lines.extend(["", "Blocks"])
        for block in report["blocks"]:
            lines.extend(_wrap(block["label"]))
            lines.extend(_wrap(block["explain"]) or [""])
            lines.append("")
    pages = [lines[index : index + 46] for index in range(0, max(len(lines), 1), 46)]
    return _document(pages)


def _wrap(text: str, width: int = 92) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    rows: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) > width:
            rows.append(current)
            current = word
        else:
            current = f"{current} {word}"
    rows.append(current)
    return rows


def _escape(text: str) -> str:
    cleaned = (
        text.replace("\u2192", "->")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    raw = cleaned.encode("latin-1", "replace").decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _document(pages: list[list[str]]) -> bytes:
    streams = []
    for lines in pages:
        commands = ["BT", "/F1 11 Tf", "50 780 Td", "15 TL"]
        for index, line in enumerate(lines):
            shown = f"({_escape(line)}) Tj"
            commands.append(shown if index == 0 else f"T* {shown}")
        commands.append("ET")
        streams.append("\n".join(commands).encode("latin-1", "replace"))

    objects: list[bytes] = [b""]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kid_numbers = [4 + index * 2 for index in range(len(streams))]
    kids = " ".join(f"{number} 0 R" for number in kid_numbers)
    objects.append(f"<< /Type /Pages /Count {len(streams)} /Kids [{kids}] >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, stream in enumerate(streams):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + index * 2} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects[1:], start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return bytes(output)
