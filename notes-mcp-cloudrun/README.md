# Obsidian Notes MCP — Cloud Run

Multi-user remote MCP for the Obsidian vaults. Authenticates via Supabase
Auth, same OAuth bridge (`mcp_auth.py`) as `lazytheta-mcp`, but its own
signing secret — see "Why a separate JWT secret" below. Fase 1 is read-only:
`list_vaults`, `search_notes`, `read_note`. No write tool exists yet.

## Deployed URL

Not deployed yet. After the first successful `gcloud run deploy notes-mcp`
(see below), fill in:

`https://notes-mcp-<HASH>.europe-west4.run.app/mcp`

## Local development

Run from the **repo root**, not from this directory. `main.py` imports
`mcp_auth`, which lives in the repo root — the Dockerfile copies it next to
`main.py` at build time, but locally it's only importable if the repo root is
on `PYTHONPATH`. Starting with `cd notes-mcp-cloudrun && python3 main.py`
fails with `ModuleNotFoundError: No module named 'mcp_auth'`.

```bash
cd /Users/administrator/Documents/github/stock-analysis
pip install -r notes-mcp-cloudrun/requirements.txt

# Set required env vars
export JWT_SIGNING_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export SUPABASE_URL="https://...supabase.co"
export SUPABASE_ANON_KEY="..."
export SUPABASE_S3_ENDPOINT="https://dacmqkjvofqqjfsfrtlp.storage.supabase.co/storage/v1/s3"
export SUPABASE_S3_REGION="eu-west-3"
export SUPABASE_S3_ACCESS_KEY_ID="..."
export SUPABASE_S3_SECRET_ACCESS_KEY="..."
export VAULT_BUCKET="vaults"

# Run dev server
PYTHONPATH=. python3 notes-mcp-cloudrun/main.py
# Server listens on http://localhost:8080

curl http://localhost:8080/health
# {"status":"ok","service":"notes-mcp"}
```

## Tests

```bash
cd notes-mcp-cloudrun
python3 -m pytest test_notes_mcp.py -v
```

Offline-mocked; no network or Supabase access required. The per-directory
`conftest.py` makes sure `import main` / `import mcp_handler` resolve to
*this* service and not to `lazytheta-mcp-cloudrun`, which has files by the
same names. Run both suites together from the repo root:

```bash
python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py \
                  lazytheta-mcp-cloudrun/test_app.py -q
```

## Build

`gcloud run deploy --source .` always picks up the top-level `Dockerfile`
(the `lazytheta-mcp` one) and has no flag to point at another. **This
service cannot be deployed with `--source .`.** It needs an explicit build
step against `Dockerfile.notes`, then a deploy from the resulting image:

```bash
cd /Users/administrator/Documents/GitHub/stock-analysis

gcloud builds submit --config cloudbuild.notes.yaml \
    --project stock-analysis-489016 --region europe-west4
```

## Deploy

```bash
gcloud run deploy notes-mcp \
    --project stock-analysis-489016 \
    --image europe-west4-docker.pkg.dev/stock-analysis-489016/cloud-run-source-deploy/notes-mcp:latest \
    --region europe-west4 \
    --platform managed \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --timeout 120 \
    --min-instances 0 \
    --max-instances 3 \
    --set-env-vars="SUPABASE_S3_ENDPOINT=https://dacmqkjvofqqjfsfrtlp.storage.supabase.co/storage/v1/s3,SUPABASE_S3_REGION=eu-west-3,VAULT_BUCKET=vaults" \
    --set-secrets="JWT_SIGNING_KEY=NOTES_JWT_SIGNING_KEY:latest,SUPABASE_URL=SUPABASE_URL:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest,SUPABASE_S3_ACCESS_KEY_ID=SUPABASE_S3_ACCESS_KEY_ID:latest,SUPABASE_S3_SECRET_ACCESS_KEY=SUPABASE_S3_SECRET_ACCESS_KEY:latest"
```

Subsequent deploys: rerun the `gcloud builds submit` step (it always
overwrites the `:latest` tag), then rerun the `gcloud run deploy` command
above. To roll back, use Cloud Run's revision history — the image tag stays
fixed at `:latest` on purpose, so rollbacks go through revisions, not tags.

### Secrets and env vars

| Name | Kind | Source |
| --- | --- | --- |
| `JWT_SIGNING_KEY` | secret | `NOTES_JWT_SIGNING_KEY:latest` — this service's own key, see below |
| `SUPABASE_URL` | secret | `SUPABASE_URL:latest` — shared with lazytheta-mcp |
| `SUPABASE_ANON_KEY` | secret | `SUPABASE_ANON_KEY:latest` — shared with lazytheta-mcp |
| `SUPABASE_S3_ACCESS_KEY_ID` | secret | `SUPABASE_S3_ACCESS_KEY_ID:latest` |
| `SUPABASE_S3_SECRET_ACCESS_KEY` | secret | `SUPABASE_S3_SECRET_ACCESS_KEY:latest` |
| `SUPABASE_S3_ENDPOINT` | env var | `https://dacmqkjvofqqjfsfrtlp.storage.supabase.co/storage/v1/s3` |
| `SUPABASE_S3_REGION` | env var | `eu-west-3` |
| `VAULT_BUCKET` | env var | `vaults` |

`SUPABASE_SERVICE_KEY` is deliberately **not** set — this service never
talks to the database, only to Supabase Storage (S3-compatible), so it has
no use for it.

### Why a separate JWT secret

`JWT_SIGNING_KEY` here is filled from `NOTES_JWT_SIGNING_KEY`, a different
secret than the one `lazytheta-mcp` uses. That's not an inconsistency —
`mcp_auth.verify_jwt` only checks the signature (no `aud`, no `iss`, no
service claim). A shared signing key would mean a token minted for the
portfolio server also opens `/mcp` on the notes server, and vice versa,
defeating the entire point of running these as two separate services.

## Logs

```bash
gcloud run services logs tail notes-mcp --region europe-west4
```

## Architecture

- **Starlette ASGI app** with `SmartAuthMiddleware` (from `mcp_auth.py`,
  the same module and the same middleware class `lazytheta-mcp` uses — not
  a copy)
- **OAuth 2.1 + PKCE bridge** to Supabase Auth (magic link + email/password)
- **JSON-RPC dispatcher** at `/mcp`, `mcp_handler.py`, routing three tools
  to `notes_tools.py` functions
- **Multi-user**: each request carries a JWT with `user_id`; the user_id
  comes from the token only — a `user_id` in the tool arguments is ignored
- **Read-only**: `list_vaults`, `search_notes`, `read_note`. No write path
  in fase 1.

## Registering on claude.ai

After first successful deploy:

1. Capture the URL from the deploy output.
2. Update this README's "Deployed URL" section.
3. Open claude.ai → Settings → Connectors → Add custom connector.
4. URL: `<service-url>/mcp`.
5. claude.ai auto-discovers OAuth via `/.well-known/oauth-authorization-server`.
6. Click "Authenticate" → redirects to `/oauth/authorize` → log in via
   Supabase (magic link or email+password) → connector connects.
7. The three tools appear under namespace `notes-mcp:<tool_name>` in claude.ai.

## Security

- **`JWT_SIGNING_KEY`** (from `NOTES_JWT_SIGNING_KEY`) is the only secret
  that, if leaked, allows token forgery for this service. Rotate via
  `gcloud secrets versions add NOTES_JWT_SIGNING_KEY` if compromised — this
  does not affect `lazytheta-mcp` tokens, and rotating lazytheta's key does
  not affect this service either.
- Vault access uses S3-compatible credentials scoped to Supabase Storage,
  not the database service-role key.
- All inter-service traffic is HTTPS via Cloud Run's automatic TLS.
