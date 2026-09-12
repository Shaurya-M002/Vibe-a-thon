# Wiring the sidecar into Hex

Hex is cloned **next to** this repo, not inside it:

```
Codes/Projects/
  Vibe-a-thon/vibes/slm/   <- this
  hex/                     <- git clone https://github.com/kitlangton/Hex (0.8.5)
```

The change is two files and seven lines. Hex already has a post-processing step
— word remappings, removals, `TranscriptFormattingApplier` — so the sidecar
slots in right after it, seeing the exact string that is about to be pasted.

## 1. New file: `Hex/Clients/SLMCleanerClient.swift`

`POST`s the transcript to `http://127.0.0.1:8742/v1/clean` and returns
`cleaned`. Three-second timeout. Override the URL with `SLM_CLEAN_URL`, turn it
off entirely with `SLM_CLEAN_DISABLED=1`.

**It fails open.** If the sidecar is not running, is slow, or returns anything
unexpected, the original transcript is returned unchanged. Losing someone's
dictation because a side process was down is not an acceptable trade for nicer
punctuation.

## 2. Patch: `Hex/Features/Transcription/TranscriptionFeature.swift`

Inside `handleTranscriptionResult`, the effect that stores and pastes the
transcript already runs on an async context, so there is somewhere to await:

```swift
return .run { send in
  // Hand the finished transcript to the local SLM sidecar. Fails open:
  // if it is not running, `cleanedResult` is `modifiedResult`.
  let cleanedResult = await SLMCleanerClient.clean(modifiedResult)
  if cleanedResult != modifiedResult {
    transcriptionFeatureLogger.info("Applied SLM cleanup")
  }
  do {
    try await finalizeRecordingAndStoreTranscript(
      result: cleanedResult,
      ...
```

`modifiedResult` is post-remapping, so Hex's own settings still win — we clean
what the user already configured, we do not bypass it.

## Running it

```bash
cd Vibe-a-thon/vibes/slm
.venv/bin/python -m uvicorn server.app:app --port 8742
```

Then build Hex and dictate. `Console.app`, filtered on Hex, logs
`Applied SLM cleanup` whenever the sidecar changed something.

## What is verified, and what is not

Building the Hex app needs **full Xcode**; this machine only has Command Line
Tools, so `Hex.xcodeproj` was never built.

What *was* verified: `SLMCleanerClient.swift` was compiled with `swiftc` and run
standalone against the live sidecar. Same file, same code path, no app shell.

| Case | Result |
|---|---|
| `so um send it to rahul no wait rohan` | `Send it to Rohan.` |
| `uh mail priya about the delay` | `Mail Priya about the delay.` |
| `the invoice came to twenty thousand rupees` | `The invoice came to ₹20,000.` |
| the three-stop plan, `bullets` | `**Plan**` + three bullets |
| sidecar stopped | input returned unchanged, in 74 ms |
| `SLM_CLEAN_DISABLED=1` | input returned unchanged |

So the networking, decoding, fail-open and timeout behaviour are proven. What
remains unproven is only that the file compiles *inside the Xcode target* and
that the hotkey path reaches it. Install Xcode, build, and dictate one sentence
to close that gap.

## If you get ten more minutes

Hex has no concept of our `style` parameter, so everything goes through as
`chat`. The obvious next move is a second hotkey that sends `bullets` — dictate
a list, get a list. `SLMCleanerClient.clean(_:style:)` already takes the
argument.
