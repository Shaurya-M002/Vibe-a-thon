"""Generate the synthetic half of the training data.

Public disfluency corpora are American telephone speech. They contain no rupees,
no Indian names, no code identifiers and nothing that punishes a model for
inventing an email address. Those are exactly the cases we demo, so we write
them ourselves.

Output: data/synthetic/synthetic.jsonl (committed).
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "synthetic" / "synthetic.jsonl"

NAMES = [
    "Rohan", "Priya", "Shaurya", "Ananya", "Vikram", "Meera", "Arjun", "Divya",
    "Karthik", "Sneha", "Rahul", "Aditi", "Nikhil", "Pooja", "Siddharth",
    "Lakshmi", "Harsha", "Ishaan", "Kavya", "Manish", "Tanvi", "Varun",
]

# "i mean" is deliberately absent: it also appears in CORRECTION_CUES, and a
# token that is sometimes deleted and sometimes signals a replacement teaches
# the model to ignore corrections.
FILLERS = ["um", "uh", "like", "you know", "so", "basically", "right"]

CORRECTION_CUES = [
    "no wait", "sorry", "no sorry", "actually", "no actually", "scratch that",
    "i mean", "no make that",
]

UNITS = ["", "thousand", "lakh", "crore"]

ONES = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
    13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
    17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
    30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy",
    80: "eighty", 90: "ninety",
}

MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
]

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def spell_number(n: int) -> str:
    """Spell 1..999 the way a person dictates it."""
    if n in ONES:
        return ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return f"{ONES[tens * 10]} {ONES[ones]}"
    hundreds, rest = divmod(n, 100)
    out = f"{ONES[hundreds]} hundred"
    if rest:
        out += f" and {spell_number(rest)}"
    return out


def indian_group(n: int) -> str:
    """1234567 -> 12,34,567 (Indian digit grouping)."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def money_pair(rng: random.Random) -> tuple[str, str]:
    unit = rng.choice(UNITS)
    if unit == "":
        n = rng.choice([50, 75, 120, 250, 400, 600, 850, 999])
        return f"{spell_number(n)} rupees", f"₹{n}"
    if unit == "thousand":
        n = rng.choice([2, 5, 8, 12, 20, 35, 48, 75])
        return f"{spell_number(n)} thousand rupees", f"₹{indian_group(n * 1000)}"
    if unit == "lakh":
        n = rng.choice([1, 2, 3, 5, 8, 12, 25])
        return f"{spell_number(n)} lakh rupees", f"₹{indian_group(n * 100000)}"
    n = rng.choice([1, 2, 4, 7])
    return f"{spell_number(n)} crore rupees", f"₹{indian_group(n * 10000000)}"


def time_pair(rng: random.Random) -> tuple[str, str]:
    hour = rng.randint(1, 12)
    minute = rng.choice([0, 15, 30, 45, 10, 20, 40])
    meridiem = rng.choice(["a m", "p m"])
    written_meridiem = "am" if meridiem == "a m" else "pm"
    if minute == 0:
        spoken = f"{ONES[hour]} {meridiem}"
        written = f"{hour}:00{written_meridiem}"
    elif minute == 30:
        spoken = rng.choice([f"half past {ONES[hour]}", f"{ONES[hour]} thirty {meridiem}"])
        written = f"{hour}:30{written_meridiem}" if meridiem in spoken else f"{hour}:30"
    else:
        spoken = f"{ONES[hour]} {spell_number(minute)} {meridiem}"
        written = f"{hour}:{minute:02d}{written_meridiem}"
    return spoken, written


def asrify(text: str, rng: random.Random) -> str:
    """Make a clean sentence look like raw ASR output."""
    out = text.lower()
    for ch in ".,!?;:":
        out = out.replace(ch, "")
    return " ".join(out.split())


def sprinkle_fillers(text: str, rng: random.Random, n: int = 1) -> str:
    words = text.split()
    for _ in range(n):
        pos = rng.randint(0, len(words))
        words.insert(pos, rng.choice(FILLERS))
    return " ".join(words)


def gen(rng: random.Random) -> list[dict]:
    rows: list[dict] = []

    def add(raw: str, clean: str, style: str = "chat", tag: str = "") -> None:
        rows.append({"raw": raw, "clean": clean, "style": style, "tag": tag})

    # 1. Self-correction on a name. The demo case.
    verbs = ["send it to", "assign this to", "forward the deck to", "loop in",
             "give the laptop to", "hand it over to", "ping"]
    for _ in range(180):
        wrong, right = rng.sample(NAMES, 2)
        verb = rng.choice(verbs)
        cue = rng.choice(CORRECTION_CUES)
        raw = f"{verb} {wrong.lower()} {cue} {right.lower()}"
        raw = sprinkle_fillers(raw, rng, n=rng.randint(0, 2))
        add(raw, f"{verb[0].upper()}{verb[1:]} {right}.", tag="self_correction_name")

    # 2. Self-correction on a number.
    for _ in range(120):
        a, b = rng.sample([12, 15, 18, 20, 24, 30, 36, 42, 43, 50, 60, 75], 2)
        cue = rng.choice(CORRECTION_CUES)
        noun = rng.choice(["units", "seats", "boxes", "licences", "tickets", "servers"])
        raw = sprinkle_fillers(
            f"we need {spell_number(a)} {noun} {cue} {spell_number(b)}", rng, rng.randint(0, 2)
        )
        add(raw, f"We need {b} {noun}.", tag="self_correction_number")

    # 3. Rupee amounts.
    for _ in range(160):
        spoken, written = money_pair(rng)
        template = rng.choice([
            ("the invoice came to {spoken}", "The invoice came to {written}."),
            ("it costs about {spoken}", "It costs about {written}."),
            ("transfer {spoken} today", "Transfer {written} today."),
            ("the budget is {spoken}", "The budget is {written}."),
            ("they quoted {spoken} for the whole thing", "They quoted {written} for the whole thing."),
        ])
        raw = sprinkle_fillers(template[0].format(spoken=spoken), rng, rng.randint(0, 2))
        add(raw, template[1].format(written=written), tag="money")

    # 4. Money with a self-correction.
    for _ in range(80):
        (spoken_a, _), (spoken_b, written_b) = money_pair(rng), money_pair(rng)
        cue = rng.choice(CORRECTION_CUES)
        raw = sprinkle_fillers(f"it was {spoken_a} {cue} {spoken_b}", rng, rng.randint(0, 1))
        add(raw, f"It was {written_b}.", tag="money_correction")

    # 5. Times.
    for _ in range(140):
        spoken, written = time_pair(rng)
        template = rng.choice([
            ("let's meet at {spoken}", "Let's meet at {written}."),
            ("the call is at {spoken}", "The call is at {written}."),
            ("standup moved to {spoken}", "Standup moved to {written}."),
            ("i'll be there by {spoken}", "I'll be there by {written}."),
        ])
        raw = sprinkle_fillers(template[0].format(spoken=spoken), rng, rng.randint(0, 2))
        add(raw, template[1].format(written=written), tag="time")

    # 6. Time with a self-correction.
    for _ in range(90):
        (spoken_a, _), (spoken_b, written_b) = time_pair(rng), time_pair(rng)
        cue = rng.choice(CORRECTION_CUES)
        raw = sprinkle_fillers(f"the demo is at {spoken_a} {cue} {spoken_b}", rng, rng.randint(0, 1))
        add(raw, f"The demo is at {written_b}.", tag="time_correction")

    # 7. Dates.
    for _ in range(90):
        month = rng.choice(MONTHS)
        day = rng.randint(1, 28)
        year = rng.choice([2025, 2026])
        spoken_year = "twenty twenty five" if year == 2025 else "twenty twenty six"
        raw = sprinkle_fillers(
            f"it's due on {month.lower()} {spell_number(day)} {spoken_year}", rng, rng.randint(0, 1)
        )
        add(raw, f"It's due on {month} {day}, {year}.", tag="date")

    # 8. PRESERVE: a recipient is named but no address is spoken.
    contact_verbs = ["mail", "email", "message", "call", "text"]
    for name in NAMES:
        for verb in contact_verbs:
            raw = sprinkle_fillers(
                f"{verb} {name.lower()} about the invoice", rng, rng.randint(0, 2)
            )
            add(raw, f"{verb.capitalize()} {name} about the invoice.", tag="preserve_contact")

    # 9. PRESERVE: an address IS spoken, so it must be written out. Enumerated
    # rather than sampled — this is the mirror of the "must not invent" case and
    # it needs enough weight to survive the rest of the mix.
    domains = ["skan.ai", "gmail.com", "example.com", "company.co.in",
               "outlook.com", "acme.in", "iisc.ac.in", "zoho.com"]
    mail_templates = [
        ("send it to {h} at {d}", "Send it to {e}."),
        ("send the deck to {h} at {d} before the call", "Send the deck to {e} before the call."),
        ("mail {h} at {d} about the invoice", "Mail {e} about the invoice."),
        ("cc {h} at {d}", "CC {e}."),
        ("forward it to {h} at {d} today", "Forward it to {e} today."),
        ("her email is {h} at {d}", "Her email is {e}."),
        ("his email is {h} at {d}", "His email is {e}."),
    ]
    for name in NAMES:
        for domain in domains:
            handle = name.lower()
            spoken_domain = domain.replace(".", " dot ")
            spoken_raw, clean_tpl = rng.choice(mail_templates)
            raw = spoken_raw.format(h=handle, d=spoken_domain)
            raw = sprinkle_fillers(raw, rng, rng.randint(0, 2))
            add(raw, clean_tpl.format(e=f"{handle}@{domain}"), tag="preserve_spoken_email")

    # Dotted and underscored local parts, spoken out.
    for _ in range(120):
        first, last = rng.sample(NAMES, 2)
        sep_spoken, sep = rng.choice([("dot", "."), ("underscore", "_")])
        domain = rng.choice(domains)
        handle = f"{first.lower()}{sep}{last.lower()}"
        raw = (f"send it to {first.lower()} {sep_spoken} {last.lower()} at "
               f"{domain.replace('.', ' dot ')}")
        add(sprinkle_fillers(raw, rng, rng.randint(0, 2)),
            f"Send it to {handle}@{domain}.", tag="preserve_spoken_email")

    # 10. Code style: identifiers and commands survive.
    code_cases = [
        ("git commit dash a m", "git commit -am", "code"),
        ("git checkout dash b feature slash login", "git checkout -b feature/login", "code"),
        ("npm run dev dash dash port three thousand", "npm run dev --port 3000", "code"),
        ("pip install dash r requirements dot txt", "pip install -r requirements.txt", "code"),
        ("cd vibes slash slm", "cd vibes/slm", "code"),
        ("docker compose up dash d", "docker compose up -d", "code"),
        ("call get underscore user underscore by underscore id", "call get_user_by_id", "code"),
        ("rename it to parse underscore transcript", "rename it to parse_transcript", "code"),
        ("the function is clean underscore text", "the function is clean_text", "code"),
        ("post slash v one slash clean", "POST /v1/clean", "code"),
        ("set temperature to zero", "set temperature to 0", "code"),
        ("import json as j s o n", "import json", "code"),
        ("run pytest dash v", "run pytest -v", "code"),
        ("export path equals slash usr slash local slash bin", "export PATH=/usr/local/bin", "code"),
        ("kill dash nine the process", "kill -9 the process", "code"),
    ]
    for raw, clean, style in code_cases:
        for _ in range(8):
            noisy = sprinkle_fillers(raw, rng, rng.randint(0, 2))
            add(noisy, clean, style=style, tag="code")

    # 11. Email style: greeting, body, sign-off.
    for _ in range(70):
        to = rng.choice(NAMES)
        sender = rng.choice(NAMES)
        body = rng.choice([
            "just wanted to follow up on the proposal can you send the numbers by end of week",
            "the invoice is attached please confirm once the payment goes out",
            "we're moving the review to next tuesday let me know if that breaks anything",
            "thanks for the quick turnaround the deck looks good to me",
        ])
        clean_body = {
            "just wanted to follow up on the proposal can you send the numbers by end of week":
                "Just wanted to follow up on the proposal. Can you send the numbers by end of week?",
            "the invoice is attached please confirm once the payment goes out":
                "The invoice is attached. Please confirm once the payment goes out.",
            "we're moving the review to next tuesday let me know if that breaks anything":
                "We're moving the review to next Tuesday. Let me know if that breaks anything.",
            "thanks for the quick turnaround the deck looks good to me":
                "Thanks for the quick turnaround. The deck looks good to me.",
        }[body]
        raw = sprinkle_fillers(f"hey {to.lower()} {body} thanks {sender.lower()}", rng, rng.randint(0, 2))
        clean = f"Hi {to},\n\n{clean_body}\n\nThanks,\n{sender}"
        add(raw, clean, style="email", tag="email")

    # 12. Filler-only input returns nothing.
    for _ in range(60):
        raw = " ".join(rng.choice(FILLERS) for _ in range(rng.randint(1, 4)))
        add(raw, "", tag="empty")

    # 13. Plain fillers and punctuation restoration, meeting-room flavour.
    plain = [
        ("the build is green we can ship after lunch",
         "The build is green. We can ship after lunch."),
        ("i pushed the branch can you review it today",
         "I pushed the branch. Can you review it today?"),
        ("the model runs on my mac and on windows too",
         "The model runs on my Mac and on Windows too."),
        ("we walked the building and the barometer picked up three floors",
         "We walked the building and the barometer picked up three floors."),
        ("the classifier is overfitting on one animal",
         "The classifier is overfitting on one animal."),
        ("the demo is not a pitch something has to run on a screen",
         "The demo is not a pitch. Something has to run on a screen."),
        ("can you check whether the token transferred on devnet",
         "Can you check whether the token transferred on devnet?"),
        ("the spend cap refused the payment which is what we wanted",
         "The spend cap refused the payment, which is what we wanted."),
        ("laptops close at eight then we make gin",
         "Laptops close at 8 then we make gin."),
        ("i think the latency is under a second on cpu",
         "I think the latency is under a second on CPU."),
        ("we need one more person on the navigation track",
         "We need one more person on the navigation track."),
        ("the wearable has to stay under fifteen hundred rupees",
         "The wearable has to stay under ₹1,500."),
    ]
    for raw, clean in plain:
        for _ in range(10):
            add(sprinkle_fillers(raw, rng, rng.randint(1, 3)), clean, tag="plain")

    # 14. Two entities in one sentence. Without these the model formats the
    # first number and quietly eats the second.
    for _ in range(200):
        spoken_money, written_money = money_pair(rng)
        month, day = rng.choice(MONTHS), rng.randint(1, 28)
        year = rng.choice([2025, 2026])
        spoken_year = "twenty twenty five" if year == 2025 else "twenty twenty six"
        raw = (f"the invoice came to {spoken_money} and it's due on "
               f"{month.lower()} {spell_number(day)} {spoken_year}")
        clean = f"The invoice came to {written_money} and it's due on {month} {day}, {year}."
        add(sprinkle_fillers(raw, rng, rng.randint(0, 2)), clean, tag="money_plus_date")

    for _ in range(140):
        spoken_money, written_money = money_pair(rng)
        spoken_time, written_time = time_pair(rng)
        raw = f"transfer {spoken_money} before {spoken_time}"
        add(sprinkle_fillers(raw, rng, rng.randint(0, 2)),
            f"Transfer {written_money} before {written_time}.", tag="money_plus_time")

    for _ in range(140):
        name = rng.choice(NAMES)
        spoken_time, written_time = time_pair(rng)
        raw = f"tell {name.lower()} the demo is at {spoken_time}"
        add(sprinkle_fillers(raw, rng, rng.randint(0, 2)),
            f"Tell {name} the demo is at {written_time}.", tag="name_plus_time")

    # 15. A correction where the replacement carries no am/pm of its own.
    for _ in range(110):
        hour_a = rng.randint(1, 12)
        meridiem = rng.choice(["a m", "p m"])
        hour_b = rng.randint(1, 12)
        minute_b = rng.choice([15, 30, 45, 20])
        cue = rng.choice(CORRECTION_CUES)
        raw = (f"let's meet at {ONES[hour_a]} {meridiem} {cue} "
               f"{ONES[hour_b]} {spell_number(minute_b)}")
        add(sprinkle_fillers(raw, rng, rng.randint(0, 1)),
            f"Let's meet at {hour_b}:{minute_b:02d}.", tag="time_correction_bare")

    # 16. Identifiers inside a sentence, not standing alone.
    lead_ins = ["the endpoint is", "hit", "the route is", "we expose", "call"]
    route_cases = [
        ("post slash v one slash clean", "POST /v1/clean"),
        ("get slash healthz", "GET /healthz"),
        ("post slash v one slash chat slash completions", "POST /v1/chat/completions"),
        ("get slash api slash status", "GET /api/status"),
    ]
    # Code style never touches casing and never adds a full stop: you are
    # dictating into an editor, not writing prose.
    for lead in lead_ins:
        for spoken, written in route_cases:
            for _ in range(6):
                raw = sprinkle_fillers(f"{lead} {spoken}", rng, rng.randint(0, 2))
                add(raw, f"{lead} {written}", style="code", tag="code_in_sentence")

    cmd_leads = ["you have to run", "just run", "then run", "first run"]
    cmd_cases = [
        ("git checkout dash b vibe slash slm", "git checkout -b vibe/slm"),
        ("npm install dash dash save dev", "npm install --save-dev"),
        ("python dash m uvicorn server dot app colon app", "python -m uvicorn server.app:app"),
        ("pytest dash v dash dash tb short", "pytest -v --tb short"),
    ]
    for lead in cmd_leads:
        for spoken, written in cmd_cases:
            for _ in range(5):
                raw = sprinkle_fillers(f"{lead} {spoken}", rng, rng.randint(0, 2))
                add(raw, f"{lead} {written}", style="code", tag="code_in_sentence")

    # 16b. One spoken "underscore" is one "_". Ordinary words that follow an
    # identifier stay ordinary words; without these the model joins the rest of
    # the sentence into the identifier.
    ident_cases = [
        ("clean underscore transcript", "clean_transcript"),
        ("parse underscore fixtures", "parse_fixtures"),
        ("build underscore dataset", "build_dataset"),
        ("apply underscore guard", "apply_guard"),
        ("find underscore violations", "find_violations"),
    ]
    tails = [
        ("with temperature zero", "with temperature 0"),
        ("on the raw text", "on the raw text"),
        ("before we paste", "before we paste"),
        ("with style set to chat", "with style set to chat"),
        ("and check the output", "and check the output"),
    ]
    for spoken_ident, written_ident in ident_cases:
        for spoken_tail, written_tail in tails:
            for _ in range(4):
                raw = sprinkle_fillers(
                    f"call {spoken_ident} {spoken_tail}", rng, rng.randint(0, 2)
                )
                add(raw, f"call {written_ident} {written_tail}", style="code",
                    tag="code_identifier_tail")

    # 16c. A correction is not the end of the sentence.
    tails_chat = [
        ("she has context", "She has context."),
        ("he has the keys", "He has the keys."),
        ("they already started", "They already started."),
        ("it's on their board", "It's on their board."),
        ("she's closer to it", "She's closer to it."),
    ]
    for _ in range(200):
        wrong, right = rng.sample(NAMES, 2)
        verb = rng.choice(verbs)
        cue = rng.choice(CORRECTION_CUES)
        spoken_tail, written_tail = rng.choice(tails_chat)
        raw = f"{verb} {wrong.lower()} {cue} {right.lower()} {spoken_tail}"
        add(sprinkle_fillers(raw, rng, rng.randint(0, 2)),
            f"{verb[0].upper()}{verb[1:]} {right}. {written_tail}",
            tag="self_correction_with_tail")

    # 17. Comma-joined clauses. The model otherwise runs them together.
    for _ in range(120):
        subject = rng.choice(["the spend cap", "the guard", "the classifier", "the server"])
        verb = rng.choice(["refused it", "blocked it", "caught it", "rejected it"])
        tail = rng.choice([
            "which is exactly what we wanted",
            "which is the whole point",
            "which is what we demo",
        ])
        add(sprinkle_fillers(f"{subject} {verb} {tail}", rng, rng.randint(0, 2)),
            f"{subject[0].upper()}{subject[1:]} {verb}, {tail}.", tag="comma_clause")

    for _ in range(120):
        a, b, c = rng.choice([
            ("laptops close at eight", "we make gin", "dinner"),
            ("we freeze scope at nine", "we demo", "beer"),
            ("lunch is at twelve thirty", "we pick tracks", "we build"),
        ])
        raw = f"{a} then {b} then {c}"
        clean_a = a.replace("eight", "8").replace("nine", "9").replace("twelve thirty", "12:30")
        add(sprinkle_fillers(raw, rng, rng.randint(0, 2)),
            f"{clean_a[0].upper()}{clean_a[1:]}, then {b}, then {c}.", tag="comma_clause")

    # "and" joining two clauses is one sentence, not two. The list work teaches
    # the model to split on every connective, and it starts splitting prose too.
    for _ in range(220):
        name = rng.choice(NAMES)
        lead = rng.choice(["call", "ping", "message", "text", "email"])
        hour, minute = rng.randint(1, 12), rng.choice([0, 15, 30, 45])
        meridiem = rng.choice(["a m", "p m"])
        spoken_time = f"{ONES[hour]}{'' if minute == 0 else ' ' + spell_number(minute)} {meridiem}"
        written_time = f"{hour}:{minute:02d}{meridiem.replace(' ', '')}"
        thing = rng.choice(["the demo", "the standup", "the review", "the handover"])
        tail = rng.choice([
            f"tell him {thing} is at {{t}}",
            f"let him know {thing} moved to {{t}}",
            f"remind her {thing} starts at {{t}}",
        ])
        raw = f"{lead} {name.lower()} and {tail.format(t=spoken_time)}"
        clean = (f"{lead[0].upper()}{lead[1:]} {name} and "
                 f"{tail.format(t=written_time)}.")
        add(sprinkle_fillers(raw, rng, rng.randint(0, 2)), clean, tag="name_plus_time")

    # 17b. Enumerated dictation. The public list data is American groceries; this
    # is what our people actually dictate, and it is the case that sent us here:
    # Hex hands back one paragraph when the speaker clearly listed three things.
    CITIES = ["Hyderabad", "Bhubaneswar", "Delhi", "Bengaluru", "Chennai", "Pune",
              "Mumbai", "Kolkata", "Kochi", "Jaipur", "Indore", "Guwahati"]
    TASKS = [
        ("book the flights", "Book the flights"),
        ("pay the vendor", "Pay the vendor"),
        ("call the landlord", "Call the landlord"),
        ("send the invoice", "Send the invoice"),
        ("finish the deck", "Finish the deck"),
        ("review the pull request", "Review the pull request"),
        ("update the roadmap", "Update the roadmap"),
        ("order the sensors", "Order the sensors"),
        ("charge the laptops", "Charge the laptops"),
        ("confirm the venue", "Confirm the venue"),
    ]
    ORDINALS = [
        ["first", "second", "third", "fourth"],
        ["one", "two", "three", "four"],
        ["number one", "number two", "number three", "number four"],
        ["firstly", "secondly", "thirdly", "lastly"],
    ]
    LIST_LEADS = [
        ("so there are {n} points", "there are {n} points"),
        ("okay so {n} things", "{n} things"),
        ("the plan is {n} steps", "{n} steps"),
        ("action items are", "action items"),
        ("todo for today", "todo"),
    ]

    def enumerate_raw(items: list[str], rng: random.Random) -> str:
        ordinals = rng.choice(ORDINALS)
        parts = []
        for i, item in enumerate(items):
            joiner = ""
            if i == len(items) - 1 and rng.random() < 0.6:
                joiner = "and "
            parts.append(f"{joiner}{ordinals[i]} {item}")
        lead = rng.choice(LIST_LEADS)[0].format(n=spell_number(len(items)))
        return f"{lead} {' '.join(parts)}"

    # Travel, the user's own example. People slip in and out of first person
    # mid-list ("first go to Hyderabad ... and third I need to go to Delhi"), so
    # the obligation is decided per item and whatever they said is kept.
    for _ in range(280):
        n = rng.randint(2, 4)
        cities = rng.sample(CITIES, n)
        items, clean_items = [], []
        for city in cities:
            spoken_obl, written_obl = rng.choice(
                [("", ""), ("", ""), ("i need to ", "I need to "),
                 ("i have to ", "I have to "), ("we need to ", "We need to ")]
            )
            items.append(f"{spoken_obl}go to {city.lower()}")
            clean_items.append(f"{written_obl}go to {city}" if written_obl
                               else f"Go to {city}")
        raw = sprinkle_fillers(enumerate_raw(items, rng), rng, rng.randint(1, 3))
        clean = "\n".join(f"- {i}" for i in clean_items)
        add(raw, clean, style="bullets", tag="enumerated_list")

    # 17b-i. When the speaker NAMES the list ("three things in my plan"), the
    # name is content and becomes a bold title. "there are three points" names
    # nothing, so it stays scaffolding and is dropped.
    TITLED_LEADS = [
        ("okay so there are {n} things in my plan", "Plan"),
        ("so my plan is {n} steps", "Plan"),
        ("my travel plan has {n} stops", "Travel Plan"),
        ("here's my shopping list", "Shopping List"),
        ("the grocery list for this week", "Grocery List"),
        ("action items for today", "Action Items"),
        ("action items from the standup", "Action Items"),
        ("the agenda is", "Agenda"),
        ("agenda for the review", "Agenda"),
        ("my packing list", "Packing List"),
        ("todo list for today", "To-do List"),
        ("so the checklist is", "Checklist"),
        ("sprint goals are", "Sprint Goals"),
        ("my reading list", "Reading List"),
    ]

    def titled(items_clean: list[str], title: str) -> str:
        return f"**{title}**\n" + "\n".join(f"- {i}" for i in items_clean)

    for _ in range(320):
        spoken_lead, title = rng.choice(TITLED_LEADS)
        n = rng.randint(2, 4)
        if "plan" in title.lower() or "packing" in title.lower():
            cities = rng.sample(CITIES, n)
            items_raw = [f"go to {c.lower()}" for c in cities]
            items_clean = [f"Go to {c}" for c in cities]
        else:
            chosen = rng.sample(TASKS, n)
            items_raw = [c[0] for c in chosen]
            items_clean = [c[1] for c in chosen]
        ordinals = rng.choice(ORDINALS)
        parts = [f"{ordinals[i]} {item}" for i, item in enumerate(items_raw)]
        raw = f"{spoken_lead.format(n=spell_number(n))} {' '.join(parts)}"
        raw = sprinkle_fillers(raw, rng, rng.randint(1, 3))
        add(raw, titled(items_clean, title), style="bullets", tag="titled_list")

    # A journey dictated as a chain, which is how people actually say it.
    #
    # The bullet keeps the speaker's own verb and nothing else. "and then from
    # there I need to" is scaffolding: the order of the list already says
    # "then", so repeating it on every line just reads like a machine filled in
    # a template. Which is exactly what happened the first time we wrote this.
    LEGS = ["go to", "head to", "fly to", "drive to", "stop at", "spend a day in"]
    RETURNS = ["come back to", "fly back to", "head back to"]
    OBLIGATION = ["i need to ", "i have to ", "i want to ", "we need to ", ""]
    # The connector is the speaker's, so it stays. Only the leading "and" goes,
    # and the comma is punctuation we are allowed to restore.
    CONNECTORS = [("and then from there", "then from there, "),
                  ("and then", "then "),
                  ("then", "then "),
                  ("after that", "after that, "),
                  ("and from there", "from there, ")]

    def sentence_case(text: str) -> str:
        return text[:1].upper() + text[1:] if text else text

    def as_written(text: str) -> str:
        """The speaker's clause as a bullet: capitalise the start and the word I."""
        return sentence_case(re.sub(r"\bi\b", "I", text))

    for _ in range(260):
        spoken_lead, title = rng.choice(
            [t for t in TITLED_LEADS if "Plan" in t[1]] + [(None, None)] * 2
        )
        n = rng.randint(3, 4)
        cities = rng.sample(CITIES, n)
        ordinal = rng.choice(["first ", "firstly ", ""])
        obligation = rng.choice(OBLIGATION)

        verb = rng.choice(LEGS)
        chain = [f"{ordinal}{obligation}{verb} {cities[0].lower()}"]
        items_clean = [as_written(f"{obligation}{verb} {cities[0]}")]

        for i, city in enumerate(cities[1:]):
            last = i == len(cities) - 2
            verb = rng.choice(RETURNS if last and rng.random() < 0.6 else LEGS)
            spoken_conn, written_conn = rng.choice(CONNECTORS)
            chain.append(f"{spoken_conn} {obligation}{verb} {city.lower()}")
            items_clean.append(as_written(f"{written_conn}{obligation}{verb} {city}"))

        body = " ".join(chain)
        if spoken_lead:
            raw = f"{spoken_lead.format(n=spell_number(n))} {body}"
            clean = titled(items_clean, title)
        else:
            raw = f"so there are {spell_number(n)} things {body}"
            clean = "\n".join(f"- {i}" for i in items_clean)
        add(sprinkle_fillers(raw, rng, rng.randint(1, 3)), clean,
            style="bullets", tag="titled_list")

    # 17b-ii. Tone. Cleaning removes disfluency; it does not promote the speaker
    # into a business memo. First person stays first person, "gonna" stays
    # "gonna", a casual opener stays casual. Without these the model learns that
    # "cleaner" means "more formal and shorter", which is not what we asked for.
    #
    # Stance words are content, not fillers: "yeah", "nah", "honestly" tell you
    # how the speaker feels and they stay. "um", "so", "like" are in FILLERS and
    # go, here as everywhere else — a word cannot be a filler in one example and
    # content in another without teaching the model to guess.
    # "yeah so" is the collision that matters: a stance word immediately followed
    # by a filler. Without it spelled out, the model keeps the wrong one.
    STANCE = [("yeah", "Yeah, "), ("yeah so", "Yeah, "), ("so yeah", "Yeah, "),
              ("nah", "Nah, "), ("nah so", "Nah, "),
              ("honestly", "Honestly, "), ("so honestly", "Honestly, "),
              ("okay cool", "Okay cool, "), ("okay so", "Okay, "),
              ("", ""), ("", "")]
    CASUAL = [
        ("i'm gonna push the branch tonight and see if it breaks",
         "I'm gonna push the branch tonight and see if it breaks."),
        ("i think we should just ship it and fix it monday",
         "I think we should just ship it and fix it Monday."),
        ("it's kind of a mess but it works", "It's kind of a mess but it works."),
        ("i really don't wanna redo the whole thing",
         "I really don't wanna redo the whole thing."),
        ("we gotta get the sensors before friday",
         "We gotta get the sensors before Friday."),
        ("i was thinking maybe we do the wearable first",
         "I was thinking maybe we do the wearable first."),
        ("that's not gonna work on windows", "That's not gonna work on Windows."),
        ("i'll take the navigation one", "I'll take the navigation one."),
        ("i guess we could try the smaller model instead",
         "I guess we could try the smaller model instead."),
        ("i need to head out by eight but i'll be back",
         "I need to head out by 8 but I'll be back."),
        ("we're not gonna finish the tokenisation bit today",
         "We're not gonna finish the tokenisation bit today."),
        ("this thing's way slower than i expected",
         "This thing's way slower than I expected."),
        ("i dunno if the mic is even picking me up",
         "I dunno if the mic is even picking me up."),
        ("let's just keep it simple for now",
         "Let's just keep it simple for now."),
    ]
    def join_stance(stance: str, clause: str) -> str:
        if not stance:
            return clause
        # The clause is no longer the first word, so it loses its capital —
        # unless it is "I", which never does.
        if re.match(r"I(?:'|\b)", clause.split()[0]):
            return stance + clause
        return stance + clause[0].lower() + clause[1:]

    for spoken_stance, written_stance in STANCE:
        for spoken_clause, written_clause in CASUAL:
            for _ in range(3):
                raw = f"{spoken_stance} {spoken_clause}".strip()
                add(sprinkle_fillers(raw, rng, rng.randint(0, 2)),
                    join_stance(written_stance, written_clause), tag="tone")

    # The same voice, split into bullets. Splitting is not licence to rewrite.
    for _ in range(200):
        n = rng.randint(2, 3)
        chosen = rng.sample(TASKS, n)
        obligation = rng.choice(["i need to ", "i have to ", "i gotta ", "we need to "])
        ordinals = rng.choice(ORDINALS)
        parts = [f"{ordinals[i]} {obligation}{c[0]}" for i, c in enumerate(chosen)]
        raw = f"so there are {spell_number(n)} things {' '.join(parts)}"
        clean = "\n".join(f"- {as_written(obligation + c[0])}" for c in chosen)
        add(sprinkle_fillers(raw, rng, rng.randint(1, 3)), clean,
            style="bullets", tag="tone")

    # A statement before the list is content and becomes its own bullet; the
    # meta lead-in ("there are three points") is scaffolding and is dropped.
    PREAMBLES = [
        ("i'm just testing this", "I'm just testing this"),
        ("this is for next week", "This is for next week"),
        ("quick note before i forget", "Quick note before I forget"),
        ("the trip is confirmed", "The trip is confirmed"),
        ("we're behind on this", "We're behind on this"),
    ]
    for _ in range(220):
        n = rng.randint(2, 4)
        cities = rng.sample(CITIES, n)
        spoken_pre, written_pre = rng.choice(PREAMBLES)
        raw = f"{spoken_pre} {enumerate_raw([f'go to {c.lower()}' for c in cities], rng)}"
        raw = sprinkle_fillers(raw, rng, rng.randint(1, 3))
        clean = f"- {written_pre}\n" + "\n".join(f"- Go to {c}" for c in cities)
        add(raw, clean, style="bullets", tag="enumerated_list")

    # Generic task lists.
    for _ in range(260):
        n = rng.randint(2, 4)
        chosen = rng.sample(TASKS, n)
        raw = enumerate_raw([c[0] for c in chosen], rng)
        raw = sprinkle_fillers(raw, rng, rng.randint(1, 3))
        clean = "\n".join(f"- {c[1]}" for c in chosen)
        add(raw, clean, style="bullets", tag="enumerated_list")

    # Lists carrying money and times, so formatting survives restructuring.
    for _ in range(160):
        name = rng.choice(NAMES)
        spoken_money, written_money = money_pair(rng)
        spoken_time, written_time = time_pair(rng)
        city = rng.choice(CITIES)
        raw = (f"three things first pay {name.lower()} {spoken_money} "
               f"second the call is at {spoken_time} and third book the ticket to {city.lower()}")
        raw = sprinkle_fillers(raw, rng, rng.randint(1, 3))
        clean = (f"- Pay {name} {written_money}\n"
                 f"- The call is at {written_time}\n"
                 f"- Book the ticket to {city}")
        add(raw, clean, style="bullets", tag="enumerated_list")

    # A list dictated without ordinals, just "and then" / "also". The opening
    # "we need to" belongs to the speaker and stays on the first bullet.
    JOINERS = [("and then", "Then "), ("also", "Also "), ("then", "Then "),
               ("after that", "After that, "), ("plus", "Plus ")]
    for _ in range(200):
        n = rng.randint(2, 4)
        chosen = rng.sample(TASKS, n)
        spoken_obl, written_obl = rng.choice(
            [("we need to ", "We need to "), ("i need to ", "I need to "), ("", "")]
        )
        spoken_parts = [f"{spoken_obl}{chosen[0][0]}"]
        clean_items = [
            f"{written_obl}{chosen[0][0]}" if written_obl else chosen[0][1]
        ]
        for task in chosen[1:]:
            spoken_join, written_join = rng.choice(JOINERS)
            spoken_parts.append(f"{spoken_join} {task[0]}")
            clean_items.append(f"{written_join}{task[0]}")
        raw = sprinkle_fillers(" ".join(spoken_parts), rng, rng.randint(1, 3))
        clean = "\n".join(f"- {i}" for i in clean_items)
        add(raw, clean, style="bullets", tag="enumerated_list")

    # 17c. The speaker asks for a list out loud, in chat style. The request
    # itself must not end up in the output.
    ASK = ["make this bullet points", "put this in bullet points", "as a list",
           "make it a list", "bullet points please", "give me this as bullets"]
    for _ in range(200):
        n = rng.randint(2, 4)
        chosen = rng.sample(TASKS, n)
        body = enumerate_raw([c[0] for c in chosen], rng)
        ask = rng.choice(ASK)
        raw = sprinkle_fillers(f"{ask} {body}" if rng.random() < 0.5 else f"{body} {ask}",
                               rng, rng.randint(0, 2))
        clean = "\n".join(f"- {c[1]}" for c in chosen)
        add(raw, clean, style="chat", tag="asked_for_list")

    # 17d. bullets style on something that is not a list stays one bullet, and
    # chat style on an enumerated sentence stays prose. Without both the model
    # decides for itself when to make a list.
    for raw_text, clean_text in plain[:8]:
        for _ in range(6):
            noisy = sprinkle_fillers(raw_text, rng, rng.randint(1, 2))
            add(noisy, f"- {clean_text}", style="bullets", tag="bullets_single")

    for _ in range(140):
        n = rng.randint(2, 3)
        chosen = rng.sample(TASKS, n)
        raw = sprinkle_fillers(enumerate_raw([c[0] for c in chosen], rng), rng, rng.randint(1, 2))
        ordinal_words = ["First", "Second", "Third", "Fourth"]
        clean = " ".join(
            f"{ordinal_words[i]}, {c[1][0].lower()}{c[1][1:]}." for i, c in enumerate(chosen)
        )
        add(raw, clean, style="chat", tag="enumerated_stays_prose")

    # 18. Stutters and word repetition.
    for _ in range(90):
        base, clean = rng.choice(plain)
        words = base.split()
        idx = rng.randrange(len(words))
        words.insert(idx, words[idx])
        add(sprinkle_fillers(" ".join(words), rng, rng.randint(0, 2)), clean, tag="repetition")

    # 19. Shopping and grocery lists, in the speaker's words.
    #
    # These replace the public grocery corpus, which writes them as a catalogue
    # — `- Basmati rice — 10 lb bag (Daawat if available)`. That notation is
    # where the model learned to compress, and compressing is how it started
    # dropping whole items off the end of a long list.
    GROCERIES = [
        ("two litres of milk", "preferably the full cream one", "2 litres of milk"),
        ("some brown bread", "not the white one", "some brown bread"),
        ("a dozen eggs", "if they have the farm ones", "a dozen eggs"),
        ("half a kilo of tomatoes", "", "half a kilo of tomatoes"),
        ("a packet of coffee", "the dark roast if they have it", "a packet of coffee"),
        ("some bananas", "not too ripe", "some bananas"),
        ("a bottle of olive oil", "", "a bottle of olive oil"),
        ("two kilos of onions", "", "2 kilos of onions"),
        ("a block of paneer", "the fresh one", "a block of paneer"),
        ("some curd", "the small tub", "some curd"),
        ("five kilos of atta", "", "5 kilos of atta"),
        ("green chillies", "just a handful", "green chillies"),
        ("a bar of dark chocolate", "", "a bar of dark chocolate"),
        ("some ginger and garlic", "", "some ginger and garlic"),
        ("a packet of biscuits", "the ones the kids like", "a packet of biscuits"),
        ("three lemons", "", "3 lemons"),
    ]
    SHOP_LEADS = [
        ("my shopping list is", "Shopping List"),
        ("the grocery list for this week is", "Grocery List"),
        ("so the shopping list is", "Shopping List"),
        ("my grocery list is", "Grocery List"),
    ]
    SHOP_JOINS = ["and then ", "and also ", "then ", "also ", "and ", ""]

    for _ in range(420):
        lead, title = rng.choice(SHOP_LEADS + [(None, None)] * 2)
        n = rng.randint(3, 6)
        chosen = rng.sample(GROCERIES, n)
        opener = rng.choice(["i need ", "i want ", "we need ", "get ", "pick up ", ""])

        spoken_parts, items = [], []
        for i, (spoken, note, written) in enumerate(chosen):
            join = "" if i == 0 else rng.choice(SHOP_JOINS)
            head = opener if i == 0 else ""
            spoken_parts.append(f"{join}{head}{spoken}{' ' + note if note else ''}")
            # A bullet does not start with "and"; the list itself is the "and".
            body = f"{join.removeprefix('and ')}{head}{written}"
            items.append(as_written(f"{body}, {note}" if note else body))

        raw = " ".join(p for p in spoken_parts if p)
        if lead:
            raw = f"{lead} {raw}"
            clean = titled(items, title)
        else:
            raw = f"{rng.choice(['so', 'okay so', 'right'])} {raw}"
            clean = "\n".join(f"- {i}" for i in items)
        add(sprinkle_fillers(raw, rng, rng.randint(1, 3)), clean,
            style="bullets", tag="shopping_list")

    # 20. Emoji, but only when the speaker asks for one out loud.
    #
    # The instruction is scaffolding and disappears, exactly like "make this
    # bullet points". The emoji itself is the only thing it leaves behind.
    # Each glyph is a rare multi-byte token, so it needs far more repetition than
    # a word does. At ~24 examples each the model answered a request for 🚀 with
    # 🥑 — it had learned "an emoji goes here" without learning which one. A
    # short vocabulary trained hard beats a long one trained thinly.
    EMOJI = [
        (["fire", "a fire"], "🔥"),
        (["smiley", "a smiley", "smiling", "a smiley face"], "😊"),
        (["rocket", "a rocket"], "🚀"),
        (["thumbs up", "a thumbs up"], "👍"),
        (["party", "a party popper", "celebration"], "🎉"),
        (["heart", "a heart"], "❤️"),
        (["clapping", "a clap", "clap"], "👏"),
        (["star", "a star"], "⭐"),
        (["tick", "a tick", "check mark"], "✅"),
        (["thinking", "a thinking face"], "🤔"),
        (["laughing", "a laughing face"], "😂"),
        (["muscle", "a flex"], "💪"),
    ]
    ASK_END = ["add {n} emoji", "add {n} emoji at the end", "put {n} emoji at the end",
               "and add {n} emoji", "with {n} emoji at the end", "add {n} emoji to it",
               "stick {n} emoji at the end", "end it with {n} emoji"]
    ASK_EACH = ["add {n} emoji to each point", "put {n} emoji on every bullet",
                "add {n} emoji to every point", "{n} emoji on each line",
                "add {n} emoji after each one"]

    emoji_chat = [
        ("the build is finally green", "The build is finally green."),
        ("we shipped it", "We shipped it."),
        ("the demo went really well", "The demo went really well."),
        ("we're live on both laptops", "We're live on both laptops."),
        ("that fixed the latency", "That fixed the latency."),
        ("the guard caught it", "The guard caught it."),
        ("we finished the deck", "We finished the deck."),
        ("i'm gonna push it tonight", "I'm gonna push it tonight."),
    ]

    for names, glyph in EMOJI:
        for _ in range(45):
            spoken, written = rng.choice(emoji_chat)
            name = rng.choice(names)
            ask = rng.choice(ASK_END).format(n=name)
            # The instruction is as likely to come first as last.
            raw = f"{ask} {spoken}" if rng.random() < 0.4 else f"{spoken} {ask}"
            add(sprinkle_fillers(raw, rng, rng.randint(0, 2)),
                f"{written} {glyph}", tag="emoji")

        # In a list, "at the end" means after the last bullet and "each point"
        # means every one of them. Both get said, so both get trained.
        for _ in range(25):
            n = rng.randint(2, 3)
            chosen = rng.sample(TASKS, n)
            ordinals = rng.choice(ORDINALS)
            name = rng.choice(names)
            each = rng.random() < 0.5
            ask = rng.choice(ASK_EACH if each else ASK_END).format(n=name)
            parts = [f"{ordinals[i]} {c[0]}" for i, c in enumerate(chosen)]
            raw = f"so there are {spell_number(n)} things {' '.join(parts)} {ask}"
            if each:
                clean = "\n".join(f"- {c[1]} {glyph}" for c in chosen)
            else:
                lines = [f"- {c[1]}" for c in chosen]
                lines[-1] = f"{lines[-1]} {glyph}"
                clean = "\n".join(lines)
            add(sprinkle_fillers(raw, rng, rng.randint(1, 3)), clean,
                style="bullets", tag="emoji")

    # The negative case, which matters more than the positive one: saying the
    # word "fire" is not asking for 🔥. Without these the model decorates any
    # sentence that happens to mention a rocket.
    NOT_ASKS = [
        ("the server is on fire again", "The server is on fire again."),
        ("we need to fire up the second laptop", "We need to fire up the second laptop."),
        ("the rocket launch is on tuesday", "The rocket launch is on Tuesday."),
        ("she gave me a thumbs up in the standup", "She gave me a thumbs up in the standup."),
        ("there was a party after the demo", "There was a party after the demo."),
        ("put a star next to the ones we finished", "Put a star next to the ones we finished."),
        ("the warning in the logs is harmless", "The warning in the logs is harmless."),
        ("keep your eyes on the latency graph", "Keep your eyes on the latency graph."),
        ("he was laughing about the typo", "He was laughing about the typo."),
        ("my heart rate sensor is off by ten", "My heart rate sensor is off by 10."),
        ("check the tick box on the form", "Check the tick box on the form."),
        ("the emoji picker is broken in the app", "The emoji picker is broken in the app."),
    ]
    for spoken, written in NOT_ASKS:
        for _ in range(16):
            add(sprinkle_fillers(spoken, rng, rng.randint(0, 2)), written,
                tag="emoji_negative")

    return rows


def main() -> None:
    rng = random.Random(20260912)
    rows = gen(rng)

    seen: set[tuple[str, str]] = set()
    unique = []
    for row in rows:
        key = (row["raw"], row["style"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for row in unique:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_tag: dict[str, int] = {}
    for row in unique:
        by_tag[row["tag"]] = by_tag.get(row["tag"], 0) + 1
    print(f"wrote {len(unique)} synthetic pairs to {OUT.relative_to(ROOT)}")
    for tag, n in sorted(by_tag.items(), key=lambda kv: -kv[1]):
        print(f"  {tag:26s} {n}")


if __name__ == "__main__":
    main()
