"""Turn an exercise note into a matching board and a questionnaire."""

import hashlib
import json
import re


def practice_spec(markdown: str) -> dict | None:
    pairs = _matching_pairs(markdown)
    if len(pairs) < 2:
        return None
    questions = []
    rights = [pair["right"] for pair in pairs]
    for index, pair in enumerate(pairs):
        choices = _choices(pair["right"], rights)
        questions.append(
            {
                "id": f"q-{index}",
                "prompt": pair["question"],
                "choices": choices,
                "answer": choices.index(pair["right"]),
            }
        )
    return {
        "matchPrompt": pairs[0]["prompt"],
        "pairs": [
            {
                "leftId": _token("s", index, pair["left"]),
                "rightId": _token("t", index, pair["right"]),
                "left": pair["left"],
                "right": pair["right"],
            }
            for index, pair in enumerate(pairs)
        ],
        "questions": questions,
    }


def public_practice_spec(spec: dict) -> dict:
    left = [{"id": pair["leftId"], "label": pair["left"]} for pair in spec.get("pairs") or []]
    right = [{"id": pair["rightId"], "label": pair["right"]} for pair in spec.get("pairs") or []]
    right.sort(key=lambda item: item["id"])
    return {
        "matchPrompt": spec.get("matchPrompt") or "Connect each item on the left to the match on the right.",
        "left": left,
        "right": right,
        "questions": [
            {"id": question["id"], "prompt": question["prompt"], "choices": question["choices"]}
            for question in spec.get("questions") or []
        ],
    }


def grade_practice(spec: dict, links: list, answers: dict) -> dict:
    expected = {pair["leftId"]: pair["rightId"] for pair in spec.get("pairs") or []}
    correct = 0
    wrong: list[str] = []
    seen: set[str] = set()
    for link in links or []:
        source = str(link.get("source") or "")
        target = str(link.get("target") or "")
        if not source or source in seen or source not in expected:
            continue
        seen.add(source)
        if expected[source] == target:
            correct += 1
        else:
            wrong.append(source)
    question_results = {}
    for question in spec.get("questions") or []:
        given = answers.get(question["id"]) if isinstance(answers, dict) else None
        try:
            given = int(given)
        except (TypeError, ValueError):
            given = None
        question_results[question["id"]] = given == question["answer"]
    total_questions = len(question_results)
    questions_correct = sum(1 for ok in question_results.values() if ok)
    return {
        "match": {"correct": correct, "total": len(expected), "wrong": wrong},
        "questions": question_results,
        "questionsCorrect": questions_correct,
        "questionsTotal": total_questions,
        "passed": correct == len(expected) and questions_correct == total_questions and len(expected) > 0,
    }


def _matching_pairs(markdown: str) -> list[dict]:
    for table in _tables(markdown):
        header = [_clean(cell).lower() for cell in table[0]]
        left_at = _find(header, ("naive", "symptom", "approach", "exercise"))
        right_at = _find(header, ("fix", "pattern that", "hypothesis", "focus", "job"))
        if left_at is None or right_at is None or left_at == right_at:
            continue
        prompt = "Connect each item to the matching response."
        if "naive" in header[left_at]:
            prompt = "Connect each naive approach to the pattern that fixes it."
            question = "Which fix matches this approach?"
        elif "symptom" in header[left_at]:
            prompt = "Connect each symptom to the cause."
            question = "Which cause matches this symptom?"
        elif "exercise" in header[left_at] or "week" in " ".join(header):
            prompt = "Connect each exercise group to its focus."
            question = "Which focus matches this exercise group?"
        else:
            question = "Which item matches?"
        pairs = []
        for row in table[1:]:
            if left_at >= len(row) or right_at >= len(row):
                continue
            left = _clean(row[left_at])
            right = _clean(row[right_at])
            if not left or not right or left.isdigit():
                continue
            pairs.append(
                {
                    "left": left[:280],
                    "right": right[:280],
                    "prompt": prompt,
                    "question": f"{question} {left[:180]}",
                }
            )
        if len(pairs) >= 2:
            return pairs[:8]
    return []


def _tables(markdown: str) -> list[list[list[str]]]:
    found = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip().startswith("|") and index + 1 < len(lines) and "---" in lines[index + 1]:
            rows = [_cells(lines[index])]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_cells(lines[index]))
                index += 1
            found.append(rows)
        else:
            index += 1
    return found


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _clean(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")
    text = text.replace("`", "").replace("**", "")
    text = re.sub(r"(?<!\*)\*(?!\*)", "", text)
    return " ".join(text.split())


def _find(header: list[str], needles: tuple[str, ...]) -> int | None:
    for index, cell in enumerate(header):
        if any(needle in cell for needle in needles):
            return index
    return None


def _choices(correct: str, rights: list[str]) -> list[str]:
    choices = [correct]
    for option in rights:
        if option != correct and option not in choices:
            choices.append(option)
        if len(choices) == 4:
            break
    return choices


def load_note_specs(raw: str) -> list[tuple[str, str, dict]]:
    notes = json.loads(raw)
    built = []
    for path, markdown in notes.items():
        spec = practice_spec(markdown)
        if spec is None:
            continue
        title = _title(markdown) or path.rsplit("/", 1)[-1]
        built.append((path, title[:300], spec))
    return built


def _token(kind: str, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{kind}:{index}:{text}".encode()).hexdigest()[:10]
    return f"{kind}{digest}"


def _title(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return _clean(line[2:])
    return ""
