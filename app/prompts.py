"""Prompt marketplace. Each prompt is built for one pattern."""

PROMPTS = (
    {
        "slug": "documentation",
        "title": "Documentation",
        "blurb": "Write the documentation a team follows for one pattern.",
        "produces": "Intent, when to use it, the parts, and the consequences.",
        "needs_code": False,
        "task": (
            "Write the documentation for the {name} pattern.\n"
            "Include the intent, when to use it, when not to use it, the parts and how they cooperate, "
            "and the consequences you accept.\n"
            "Stay with this pattern. Do not rename it or swap in a neighboring pattern."
        ),
    },
    {
        "slug": "examples",
        "title": "Examples",
        "blurb": "Produce worked examples that show the pattern in use.",
        "produces": "One small example and one example at system scale.",
        "needs_code": False,
        "task": (
            "Give worked examples of the {name} pattern.\n"
            "Include one small example and one example at system scale.\n"
            "For each example, name the parts, what triggers the pattern, and the outcome.\n"
            "Stay with this pattern."
        ),
    },
    {
        "slug": "risks",
        "title": "Risks",
        "blurb": "Name the risks of using the pattern and the guard that belongs with each one.",
        "produces": "Failures, why this pattern creates them, and the guard to keep.",
        "needs_code": False,
        "task": (
            "List the risks of using the {name} pattern.\n"
            "For each risk, give the failure you would see, why this pattern creates it, "
            "and the guard that belongs with the pattern.\n"
            "Use the design concerns from the library when they apply."
        ),
    },
    {
        "slug": "diagrams",
        "title": "Diagrams",
        "blurb": "Ask for a flowchart that shows this pattern and one failure path.",
        "produces": "A Mermaid flowchart with the pattern's parts, the happy path, and one failure.",
        "needs_code": False,
        "task": (
            "Draw a flowchart for the {name} pattern.\n"
            "Use a Mermaid flowchart TD.\n"
            "Label each part with its role in this pattern.\n"
            "Show the happy path and one failure path.\n"
            "Do not invent a second pattern to make the picture work."
        ),
    },
    {
        "slug": "code",
        "title": "Tackle your code",
        "blurb": "Apply this pattern to the code you paste, and only where the pattern belongs.",
        "produces": "Revised code, a note for each change, or a reason the pattern does not fit.",
        "needs_code": True,
        "task": (
            "Apply the {name} pattern to the code below, the way this pattern is defined.\n"
            "Change only the code this pattern is responsible for.\n"
            "Keep behavior the pattern does not replace.\n"
            "If this pattern does not fit the code, say so and name the constraint it would break.\n"
            "Show the revised code and a short note for each change."
        ),
    },
)


def list_prompts() -> list[dict]:
    return [dict(item) for item in PROMPTS]


def prompt_by_slug(slug: str) -> dict | None:
    for item in PROMPTS:
        if item["slug"] == slug:
            return dict(item)
    return None


def compose_prompt(spec: dict, pattern, brief: str, focus: str, code: str) -> str:
    lines = [
        f"Pattern: {pattern.name}",
        f"Category: {pattern.category}",
    ]
    if pattern.subcategory:
        lines.append(f"Group: {pattern.subcategory}")
    if pattern.repository_path:
        lines.append(f"Library source: {pattern.repository_path}")
    lines.extend(["", "Library constraints:", brief or "Use the pattern as named.", ""])
    lines.append(f"Focus: {focus or 'The system you are designing.'}")
    lines.extend(["", "Task:", spec["task"].format(name=pattern.name)])
    if spec["needs_code"]:
        lines.extend(["", "Code:", "```", code.strip(), "```"])
    return "\n".join(lines)
