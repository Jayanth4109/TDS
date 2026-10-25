# IITM TDS GA3 submissions

Source requirements: <https://exam.sanand.workers.dev/tds-2026-09-ga3>.
The published API contracts are preserved under `docs/`.

## Status and limitations

The protocol implementations have automated tests for validation, durable replay, concurrent
deduplication, user isolation, cancel/result races, Ed25519 receipts, retries, approval gates,
and OTLP correlation. Tests inject controlled model outputs to exercise protocol behavior.
They do **not** prove decision accuracy on personalized or hidden grader cases.
The initial deployment failed real grader requests with HTTP 503. Its anonymous provider
enforces one reply every 30 seconds per IP. Small smoke requests did not expose that limit
or validate the full workloads. The revised pipeline is not ready until its authenticated
model connection and full-sized live validation pass. No exam Check or Save is submitted
automatically by this code.

The deployment now requires the repository Actions secret `LLM_API_KEY`. Its default backend
is Gemini 3.5 Flash-Lite through Google's OpenAI-compatible API. Obtain a key through
<https://aistudio.google.com/app/apikey> and enter it at
<https://github.com/Jayanth4109/TDS/settings/secrets/actions>. Do not put the key in chat,
source files, prompts or command arguments. Alternative OpenAI-compatible providers can be
selected with repository variables `LLM_CHAT_URL` and `LLM_MODEL` and a matching API key.
An HTTP 200 alone is insufficient: the provider must return a valid completion envelope.

The revised pipeline processes bounded groups in parallel, preserves validated partial results
if another group fails, and releases database write locks while waiting for inference. Per-ID
locks preserve concurrent replay guarantees. Upstream authentication, quota, input and timeout
failures have distinct error codes. An optional RSA/AES-encrypted diagnostic channel retains
synthetic cases and upstream errors for private troubleshooting; the decryption key is outside
the repository and is never uploaded to Actions artifacts.

A small local Qwen2.5-3B model was tested; it was too slow and produced invalid evidence.

The workflow requires an authenticated key and passes full-sized live workloads (12 invoices,
64 varied mailroom dossiers, and a long incident transcript) within the grader deadlines before
publishing the deployment artifact. This is still separate from the personalized grader.
The temporary GitHub Actions service lasts at most four hours after startup. A new deployment
changes its public URL and database. Keep the same run alive through Check and Save. This is
not permanent application hosting. Media files committed under `submissions/` have independent,
stable raw GitHub URLs. A Docker image is supplied for future permanent hosting with a `/data`
volume; no hosting account or paid resources are provisioned by the Dockerfile.

The Nixon model has inferred facial depth and an estimated rear head. Its appearance from the
source view is stronger than its side/back appearance. The single photograph cannot establish
unseen geometry. It is a valid technical submission candidate, not a guarantee of peer-review
marks or a photogrammetric scan.

## Run locally

Use the existing checkout. A cloud task is already isolated: do not create a worktree unless
the user requests one.

```bash
cd /workspace/TDS/assignment
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt 'pytest>=8,<9'
.venv/bin/python -m pytest tests -q
export LLM_CHAT_URL=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
export LLM_MODEL=gemini-3.5-flash-lite
export LLM_REASONING_EFFORT=minimal
# Inject LLM_API_KEY through a secure environment binding before starting the server.
export STATE_DB=/workspace/tds-assignment/runtime/state.sqlite3
export MEDIA_DIR=/workspace/TDS/assignment/submissions
.venv/bin/python -m uvicorn agents.app:app --host 0.0.0.0 --port 8000 --no-access-log
```

`GET /healthz` checks HTTP startup only. A genuine readiness check also sends a representative
case through inference and the receipt lifecycle. Never expose `/healthz` as proof of model
accuracy or grader readiness. Services have no access to email/payment/production incident
systems; they implement the synthetic grader's proposal/receipt protocols.

For another OpenAI-compatible provider, set `LLM_BASE_URL` to its API root (for example one
ending in `/v1`), `LLM_MODEL`, and, if required, inject `LLM_API_KEY` securely. `LLM_CHAT_URL`
overrides the derived chat endpoint. Do not commit a key or put it in a prompt. Send only
the synthetic case data to inference. There is no model call for retries, receipts, reads,
lists, cancels, or exact replays. Incident planning calls the configured provider on each
new run; stable invoice/mailroom decisions are cached by canonical content.

## Submitted URL paths

* Q1: zero marks; optional number guessing demonstration. No solution is required to proceed.
* Q2: `https://PUBLIC_HOST/a2a/`
* Q3: `https://PUBLIC_HOST/v1/mailroom/actions`
* Q4: `https://PUBLIC_HOST`
* Q5: use the game's own signed result after actual participation; typing `played` cannot
  create that result. The drawing and guessing windows shown in the screenshots have ended.
* Q6: direct raw HTTPS URL to `submissions/nixon.glb`, with CORS.
* Q7: direct raw HTTPS URL to `submissions/morph.mp4`.
* Q8: provisional visual estimate, original Pair labels: `9,7,1,3,4,6,2,5,10,8`.
  This is subjective; extra marks depend on agreement with classmates' actual morph judgments.

The grader has limited attempts. Use one Check after deployment validation, inspect its
feedback, and preserve at least one remaining attempt for Save. Only the latest Save counts.
The screenshots give a Save deadline of **25 October 2026, 11:59 p.m. IST**.

## Media reproduction and attribution

Install `requirements-media.txt` in a separate Python 3.12 environment with FFmpeg available.
See `submissions/ATTRIBUTION.md` for the exact Wikimedia source URLs and license notes.

```bash
python media/morph.py /path/to/Dario-Fo.jpg /path/to/Steinmeier.jpg output/morph.mp4
python media/reconstruct.py /path/to/Nixon.jpg output/nixon.glb
blender -b --factory-startup --python media/render_review.py -- output/nixon.glb output/review
ffprobe -v error -count_frames -show_streams -show_format output/morph.mp4
ffmpeg -v error -i output/morph.mp4 -f null -
```

The morph aligns eye positions, interpolates facial landmark geometry with piecewise affine
warps, blends appearance, and encodes 120 H.264/yuv420p frames over four seconds. Its file size
is checked against the strict 100,000-byte ceiling. The GLB generator checks size and reloads
the geometry; the final file was also validated with Khronos glTF Validator.
