"""The cleaning contract, in one place.

Every dataset builder and the server import from here so training targets and
runtime prompts cannot drift apart.
"""

SYSTEM_PROMPT = (
    "You clean raw speech-to-text transcripts into written text. "
    "Remove fillers and false starts, keep only the value the speaker landed on "
    "after a self-correction, restore punctuation and capitalisation, and write "
    "numbers, money, times and dates in written form. "
    "Keep the speaker's own words, verbs and point of view. Do not paraphrase, "
    "summarise, shorten, or rewrite first person into commands: if they said "
    "'I need to come back', write 'I need to come back'. "
    "In bullets style, or whenever the speaker asks for a list, split the "
    "content into a markdown list with one item per point, keeping every item "
    "and the wording the speaker used. "
    "Never invent facts, email addresses, phone numbers or recipients that were "
    "not spoken. Output only the cleaned text."
)

STYLES = ("chat", "email", "code", "bullets")


def build_user_content(text: str, style: str = "chat") -> str:
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}, expected one of {STYLES}")
    return f"[style: {style}]\n{text}"


def build_messages(text: str, style: str = "chat", cleaned: str | None = None) -> list[dict]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_content(text, style)},
    ]
    if cleaned is not None:
        messages.append({"role": "assistant", "content": cleaned})
    return messages
