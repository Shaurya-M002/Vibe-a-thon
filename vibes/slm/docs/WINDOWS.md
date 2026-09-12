# Running the same model on the Windows laptop

The point of this file: the demo must show one model behind one API on two
operating systems. Nothing here is Mac-specific — MLX stays on the Mac, Windows
runs the same weights as a GGUF through llama.cpp, and `POST /v1/clean` behaves
the same on both.

## What to copy over

One file: `dist/slm-clean-q5_k_m.gguf` (424 MB). It is gitignored, so put it on
a USB stick or share it over the LAN. Do not commit it.

Also copy the repo itself (or `git clone` and `git checkout vibe/slm`).

## On Windows

**1. Get llama.cpp.** Download a release build from
[ggml-org/llama.cpp/releases](https://github.com/ggml-org/llama.cpp/releases)
(pick `llama-*-bin-win-*.zip`) and unzip it. Or `winget install llama.cpp`.

**2. Start llama-server** with the GGUF:

```powershell
llama-server --model slm-clean-q5_k_m.gguf --port 8080 --ctx-size 4096 --alias slm-clean
```

**3. Start the sidecar** pointed at it. It needs no ML libraries on Windows —
just FastAPI and httpx, because all the inference happens in llama-server:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\pip install fastapi uvicorn httpx
set SLM_BACKEND=llamacpp
.venv\Scripts\python -m uvicorn server.app:app --host 127.0.0.1 --port 8742
```

Open <http://127.0.0.1:8742>. The header should read `backend llamacpp`.

**4. Prove it is the same model:**

```powershell
.venv\Scripts\python scripts\eval_fixtures.py --backend llamacpp
```

That writes `results/fixtures-llamacpp.md`. Diff it against
`results/fixtures-mlx.md` from the Mac — put the two windows side by side on the
TV and run the same sentence on both.

If `SLM_LLAMA_URL` needs to differ (llama-server on another machine), set it:

```powershell
set SLM_LLAMA_URL=http://192.168.1.50:8080
```

## What we measured

Both backends were run on the Mac against the same 30 fixtures, so the numbers
below compare the *model formats*, not the two laptops.

| | mlx (fused bf16) | llama.cpp (Q5_K_M) | llama.cpp (Q4_K_M) |
|---|---|---|---|
| Exact match | 30/30 | 30/30 | 29/30 |
| Median latency | 338 ms | 134 ms | 132 ms |
| On disk | 1.2 GB | 424 MB | 378 MB |

Quantising is about two and a half times faster, because llama.cpp runs a
quantised model on CPU while MLX runs bf16. The Windows laptop is not the slow
one here.

We ship Q5 rather than Q4 because Q4 loses a fixture: it runs the email sign-off
together into one line instead of `Thanks,\nMeera`. Nothing else moves, and 46 MB
is a cheap way to keep the two machines byte-identical on the demo set. Both
files are in `dist/` if you want to compare them yourself.

## Not yet verified

The llama.cpp path above was run end to end **on the Mac**, against the exact
GGUF that Windows will load. It has not yet been executed on the Windows laptop
itself — do step 4 there before the demo and paste the numbers into this file.
