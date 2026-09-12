"""Merge public disfluency corpora + our synthetic pairs into mlx-lm chat JSONL.

Sources
  1. google-research-datasets/disfl_qa   'disfluent question' -> 'original question'
  2. nyralabs/disfluency_speech_english  verbatim -> intended (text columns only;
     we read the parquet with column pruning so the ~1GB of audio never lands)
  3. data/synthetic/synthetic.jsonl      rupees, Indian names, code, PRESERVE

Output: data/processed/{train,valid,fixtures}.jsonl
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from style import build_messages  # noqa: E402

OUT_DIR = ROOT / "data" / "processed"
SYNTHETIC = ROOT / "data" / "synthetic" / "synthetic.jsonl"
FIXTURES = ROOT / "data" / "fixtures.jsonl"

NYRA_FILES = [
    "datasets/nyralabs/disfluency_speech_english/data/train-00000-of-00002.parquet",
    "datasets/nyralabs/disfluency_speech_english/data/train-00001-of-00002.parquet",
    "datasets/nyralabs/disfluency_speech_english/data/validation-00000-of-00001.parquet",
]

# Sound events an ASR engine would never emit as words.
DROP_TAGS = re.compile(
    r"\[(laughter|breath|cough|throatclearing|lipsmack|noise|silence|sigh|sniff)\]",
    re.IGNORECASE,
)
FILLER_TAGS = re.compile(r"\[(UH|UM|MM|HM)\]", re.IGNORECASE)
MAX_CHARS = 600

SYNTHETIC_REPEAT = 3
DISFL_CAP = 4000
NYRA_CAP = 3000
LIST_REPEAT = 2


def asrify(text: str) -> str:
    """Make a punctuated reference look like what comes out of an ASR engine."""
    out = text.lower()
    out = re.sub(r"[.,!?;:\"()\[\]]", " ", out)
    out = out.replace(" - ", " ").replace("--", " ")
    return " ".join(out.split())


def normalise_verbatim(text: str) -> str:
    out = DROP_TAGS.sub(" ", text)
    out = FILLER_TAGS.sub(lambda m: m.group(1).lower(), out)
    out = out.replace("*", "")  # cutoffs: th* -> th
    return asrify(out)


def load_disfl_qa() -> list[dict]:
    from datasets import load_dataset

    rows: list[dict] = []
    ds = load_dataset("google-research-datasets/disfl_qa")
    for split in ("train", "validation"):
        for row in ds[split]:
            raw = asrify(row["disfluent question"])
            clean = row["original question"].strip()
            if not raw or not clean or len(clean) > MAX_CHARS:
                continue
            rows.append({"raw": raw, "clean": clean, "style": "chat", "tag": "disfl_qa"})
    print(f"disfl_qa: {len(rows)}")
    return rows


def load_nyralabs() -> list[dict]:
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    fs = HfFileSystem()
    rows: list[dict] = []
    for path in NYRA_FILES:
        with fs.open(path, "rb") as handle:
            table = pq.ParquetFile(handle).read(
                columns=["verbatim_transcript", "intended_transcript"]
            )
        for verbatim, intended in zip(
            table["verbatim_transcript"].to_pylist(),
            table["intended_transcript"].to_pylist(),
        ):
            if not verbatim or not intended:
                continue
            raw = normalise_verbatim(verbatim)
            clean = intended.strip()
            if not raw or not clean or len(clean) > MAX_CHARS:
                continue
            rows.append({"raw": raw, "clean": clean, "style": "chat", "tag": "nyralabs"})
    print(f"nyralabs: {len(rows)}")
    return rows


# Words a cleaner is allowed to drop without touching the speaker's voice.
DISFLUENCY = {
    "um", "uh", "er", "erm", "mm", "hmm", "like", "you", "know", "i", "mean",
    "so", "okay", "ok", "well", "right", "basically", "actually", "just",
    "kind", "of", "sort", "a", "the", "and", "then", "that", "is", "it",
}
MIN_RETENTION = 0.75
MIN_LENGTH_RATIO = 0.7


def is_faithful(raw: str, clean: str) -> bool:
    """Did the output keep the speaker's words, or did it summarise them?"""
    raw_words = [w for w in re.findall(r"[a-z0-9']+", raw.lower()) if w not in DISFLUENCY]
    if not raw_words:
        return True
    kept = set(re.findall(r"[a-z0-9']+", clean.lower()))
    retention = sum(1 for w in raw_words if w in kept) / len(raw_words)
    return retention >= MIN_RETENTION and len(clean) / max(1, len(raw)) >= MIN_LENGTH_RATIO


def load_aawaaz() -> list[dict]:
    """Dictated lists -> markdown lists. NOT USED — kept so the finding is not lost.

    shantanugoel/aawaaz-transcript-cleanup-dataset (MIT) is the closest public
    data to our task, and we trained on it. It was a mistake. It does not clean a
    dictated list, it *catalogues* one:

        "basmati rice, the ten pound bag, Daawat brand if they have it"
        -> "- Basmati rice — 10 lb bag (Daawat if available)"

    111 of the 129 rows that survive `is_faithful` use that `item — qty (note)`
    notation. Training on it taught the model that a list item is a terse index
    entry, and from there it started compressing aggressively and dropping whole
    items off the end of long lists. `gen_synthetic.py` section 19 writes
    shopping lists in the speaker's own words instead.
    """
    import json as _json

    from huggingface_hub import hf_hub_download

    rows: list[dict] = []
    for name, limit in (("shopping_lists", 1000), ("recipe_cooking", 400)):
        path = hf_hub_download(
            "shantanugoel/aawaaz-transcript-cleanup-dataset",
            f"{name}.jsonl",
            repo_type="dataset",
        )
        kept = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip() or kept >= limit:
                    continue
                row = _json.loads(line)
                raw, clean = row["input"].strip(), row["output"].strip()
                if "#" in clean or not re.search(r"^\s*[-*]\s", clean, re.M):
                    continue
                # Their headings are `**Produce:**` or plain `Produce:`.
                # Ours are `**Produce**`.
                clean = re.sub(r"^\*\*(.+?):\*\*$", r"**\1**", clean, flags=re.M)
                clean = re.sub(r"^([^-*\n][^\n]{0,48}):$", r"**\1**", clean, flags=re.M)
                # Either a flat list, or bold-titled groups. Nothing else.
                if not re.match(r"^\s*(?:[-*]\s|\*\*)", clean):
                    continue
                if len(raw) + len(clean) > 1400:
                    continue
                # Much of this corpus summarises rather than cleans: it turns
                # "we go through two gallons a week with the kids" into
                # "- Milk — 2 gallons". That teaches terseness, and terseness is
                # how we lost the speaker's voice. Keep only the faithful rows.
                if not is_faithful(raw, clean):
                    continue
                rows.append({"raw": raw, "clean": clean, "style": "bullets", "tag": "aawaaz_list"})
                kept += 1
    print(f"aawaaz lists: {len(rows)}")
    return rows


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def key(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.lower()).strip()


def to_chat(row: dict) -> dict:
    return {"messages": build_messages(row["raw"], row.get("style", "chat"), row["clean"])}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fixtures = load_jsonl(FIXTURES)
    held_out = {key(f["raw"]) for f in fixtures}

    rng = random.Random(20260912)

    seen: set[tuple[str, str]] = set()

    def dedupe(rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            k = key(row["raw"])
            if k in held_out:  # never train on a fixture
                continue
            pair = (k, row.get("style", "chat"))
            if pair in seen:
                continue
            seen.add(pair)
            out.append(row)
        return out

    def cap(rows: list[dict], n: int) -> list[dict]:
        if len(rows) <= n:
            return rows
        rng.shuffle(rows)
        return rows[:n]

    # The public corpora are American telephone speech: plenty of "um" and
    # restarts, zero rupees and zero identifiers. Cap them so they do not drown
    # out the cases the demo actually rests on, and repeat ours.
    synthetic = dedupe(load_jsonl(SYNTHETIC))
    disfl = cap(dedupe(load_disfl_qa()), DISFL_CAP)
    nyra = cap(dedupe(load_nyralabs()), NYRA_CAP)

    kept = synthetic * SYNTHETIC_REPEAT + disfl + nyra
    rng.shuffle(kept)

    n_valid = min(500, len(kept) // 10)
    valid, train = kept[:n_valid], kept[n_valid:]

    for name, subset in (("train", train), ("valid", valid)):
        path = OUT_DIR / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for row in subset:
                f.write(json.dumps(to_chat(row), ensure_ascii=False) + "\n")
        print(f"{name}: {len(subset)} -> {path.relative_to(ROOT)}")

    path = OUT_DIR / "fixtures.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for row in fixtures:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"fixtures: {len(fixtures)} -> {path.relative_to(ROOT)}")

    counts: dict[str, int] = {}
    for row in kept:
        tag = row.get("tag", "?")
        bucket = tag if tag in ("disfl_qa", "nyralabs", "aawaaz_list") else "synthetic"
        counts[bucket] = counts.get(bucket, 0) + 1
    print("mix:", counts)


if __name__ == "__main__":
    main()
