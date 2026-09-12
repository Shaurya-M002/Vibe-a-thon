# Google Cloud connection

The local Governor environment is connected to **Default Gemini Project**
(`gen-lang-client-0663392387`). Billing and `aiplatform.googleapis.com` were verified
as enabled on 12 September 2026. The configured model is `gemini-3.5-flash`, using
the Google Cloud backend in the `global` location.

## Connect another development machine

Install the [Google Cloud CLI](https://docs.cloud.google.com/sdk/docs/install-sdk),
then authenticate with an account that can use the project:

```bash
gcloud auth login --update-adc
gcloud config set project gen-lang-client-0663392387
gcloud auth application-default set-quota-project gen-lang-client-0663392387
```

`--update-adc` saves both CLI and Application Default Credentials through the
browser flow. Python's Google SDK uses ADC without a service-account key file.
[Google's authentication reference](https://docs.cloud.google.com/sdk/gcloud/reference/auth/login).

From `vibes/markets`, create `.env` from `.env.example` if it does not already
exist, and set these values:

```dotenv
GEMINI_BACKEND=vertex
GOOGLE_CLOUD_PROJECT=gen-lang-client-0663392387
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.5-flash
```

Keep the existing wallet paths and budget limits. A Gemini Developer API key is
not needed for this backend. Authentication lives in the user's local gcloud
credential store; neither that store nor `.env` belongs in the repository.

## Verify

```bash
governor run "Call get_budget exactly once, then report the available atomic USDC budget in one sentence. Do not purchase anything."
```

This was verified against live Gemini: two model turns, one `get_budget` tool
call, and zero payment authorizations. The agent returned the configured 10000
atomic-unit allowance. This verifies the model connection and tool round trip;
the payment adapter remains simulated.

On the current machine the CLI is installed in
`~/.local/share/google-cloud-sdk`, with `gcloud` available through `~/.local/bin`.
