# What the cleaner may and may not change

A model that rewrites your words is only useful if you can predict what it will
do. This is the contract. The training data is generated to match it and
`tests/test_guard.py` enforces the parts that must never break.

## It may

| Change | Raw | Cleaned |
|---|---|---|
| Drop fillers | `um uh like you know the build is green` | `The build is green.` |
| Drop stutters and restarts | `the the build is green` | `The build is green.` |
| Keep only the corrected value | `send it to rahul no wait rohan` | `Send it to Rohan.` |
| Restore punctuation and case | `can you check the barometer` | `Can you check the barometer?` |
| Write money in figures | `twenty thousand rupees` | `₹20,000` |
| Use Indian digit grouping | `three lakh rupees` | `₹3,00,000` |
| Write times | `nine thirty p m` | `9:30pm` |
| Write dates | `march third twenty twenty six` | `March 3, 2026` |
| Turn spoken symbols into real ones | `dash b`, `slash`, `underscore`, `dot` | `-b`, `/`, `_`, `.` |
| Return nothing for nothing | `um uh like` | *(empty)* |

## It may not change your voice

Cleaning removes disfluency and fixes mechanics. It is not a promotion into
business English, and it is not a summary. The speaker's words, verbs and point
of view come out the other side:

```
raw     : yeah so um i'm gonna push the branch tonight and uh see if it breaks
cleaned : Yeah, I'm gonna push the branch tonight and see if it breaks.
NOT     : I will push the branch tonight and check for failures.
```

Three things follow from that:

- **First person stays first person.** "I need to come back to Vijayawada" is
  not rewritten as "Come back to Vijayawada", in a sentence or in a bullet.
  Splitting text into a list is not licence to reword it.
- **Register survives.** `gonna`, `gotta`, `wanna` and `dunno` are how the
  speaker talks. `yeah`, `nah` and `honestly` carry their stance and stay.
  Fillers — `um`, `uh`, `like`, `so`, `you know` — are not stance, and go.
- **Nothing is compressed.** If the speaker spent a clause on something, the
  clause survives. A cleaner that shortens is a summariser wearing a disguise.

This is the rule that most of the public list-cleanup data gets wrong: it turns
"we go through two gallons a week with the kids" into `- Milk — 2 gallons`.
`scripts/build_dataset.py` measures how much of the speaker survives each pair
and drops the ones that summarise, which is why only 129 of ~1,950 rows of the
`aawaaz` corpus make it in.

## Lists

`bullets` style restructures what was said into a **markdown list**, one item
per point, using `- `. It never drops an item.

```
raw     : so there are three points first confirm the venue second charge the
          laptops and third order the sensors
cleaned : - Confirm the venue
          - Charge the laptops
          - Order the sensors
```

If the speaker **names** the list, the name becomes a bold title — and each
bullet is still the speaker's own clause:

```
raw     : okay so there are three things in my plan first i need to go to
          hyderabad and then from there i need to go to orissa and then from
          there i need to come back to vijayawada
cleaned : **Plan**
          - I need to go to Hyderabad
          - Then from there, I need to go to Orissa
          - Then from there, I need to come back to Vijayawada
```

Note what did *not* happen: "I need to" was not deleted, "come back to" was not
flattened into "go to", and "then from there" was kept because the speaker said
it. An earlier version stripped all three and the result read like a machine had
filled in a template.

Five rules make this predictable:

- **A named list gets a title, an unnamed one does not.** "three things in my
  plan" names it, so you get `**Plan**`. "there are three points" names nothing,
  and inventing a title is inventing content.
- **Scaffolding is dropped, content is not.** "there are three points",
  "action items are" and "todo for today" are how you announce a list, so they
  disappear — except for the name inside them, which becomes the title. A real
  statement before the list — "I'm just testing this" — is content and becomes
  its own bullet.
- **Asking out loud works in any style.** "make this bullet points …" produces a
  list even when style is `chat`, and the request itself is not part of the
  output.
- **`chat` never turns into a list on its own.** Enumerating things in `chat`
  gives you prose: `First, call the landlord. Second, pay the vendor.` If you
  want bullets, either switch the style or say so.
- **A bullet is a clause, not a command.** Only the enumeration word ("first",
  "second") and a leading "and" are dropped. Everything else the speaker put in
  the clause stays in the bullet.

Formatting survives restructuring: money, times and names are written the same
inside a bullet as they are in a sentence.

## It may not

**Invent a recipient's contact details.** This is the one that would actually
hurt you. If the speaker names a person but never says an address, the name
stays a name:

```
raw     : uh mail priya about the delay
cleaned : Mail Priya about the delay.
NOT     : Mail priya@company.com about the delay.
```

If the address *was* spoken, it must appear:

```
raw     : send the deck to ananya at skan dot ai before the call
cleaned : Send the deck to ananya@skan.ai before the call.
```

The same rule covers phone numbers and links. `call nikhil and tell him we're
late` never grows a mobile number.

**Invent content.** Cleaning never adds a clause the speaker did not say.

**Correct a proper noun.** If the transcript says `Bhoneswar`, that is what comes
out. Rewriting it to "Bhubaneswar" is the same bet as guessing an email address,
and it is wrong exactly when it matters.

**Touch casing or add full stops in `code` style.** You are dictating into an
editor, so `the endpoint is post slash v one slash clean` becomes
`the endpoint is POST /v1/clean` — identifier fixed, prose left alone.

**One spoken "underscore" is one `_`.** Words after an identifier stay ordinary
words: `call clean underscore transcript with temperature zero` becomes
`call clean_transcript with temperature 0`, not one long snake_case token.

## The guard

Prompting does not reliably stop a 0.6B model from being helpful, so
`server/guard.py` checks every output before it is returned. It is plain string
matching, no model involved:

- Every email, URL and phone number in the output must be traceable to something
  in the input. Spoken forms count (`skan dot ai` supports `skan.ai`, and
  `nine eight seven six…` supports `9876…`).
- Anything unsupported is stripped and reported in `violations`, which the UI
  shows as a red banner. The demo is the refusal, not the rewrite.
- If the model loops — output more than three times the input length, or the
  last words repeating — the raw transcript is returned unchanged. A messy
  transcript beats a confident hallucination.

The guard fires zero times on the 30 fixtures because the model behaves. It is
there for the case where it does not.
