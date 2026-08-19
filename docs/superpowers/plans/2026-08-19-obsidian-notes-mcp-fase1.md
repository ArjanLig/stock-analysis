# Obsidian notes-MCP — fase 1 (sync + lezen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** De vijf Obsidian-vaults staan in Supabase Storage, synchroniseren naar Mac en iPhone, en zijn vanaf de telefoon via Claude te doorzoeken en te lezen.

**Architecture:** Supabase Storage is de enige waarheid; Remotely Save synct Obsidian op beide apparaten met de bucket; een aparte Cloud Run-service leest diezelfde objecten via het S3-protocol en biedt ze aan als MCP-tools. De OAuth-brug naar Supabase Auth wordt gedeeld met de bestaande LazyTheta-server in plaats van gekopieerd. Er wordt in deze fase niets geschreven.

**Tech Stack:** Python 3.13, Starlette, boto3 (S3-protocol tegen Supabase Storage), PyJWT, Cloud Run, Supabase Storage + Auth, Obsidian met de Remotely Save-plugin.

**Spec:** `docs/superpowers/specs/2026-08-19-obsidian-notes-mcp-design.md`

## Global Constraints

- Repo-root: `/Users/administrator/Documents/GitHub/stock-analysis`
- Python-basisimage: `python:3.13-slim`
- `python3 -m ruff check .` moet slagen vóór elke commit (repo-regel uit `CLAUDE.md`)
- Geen secrets in code of image; alles via Google Secret Manager
- GCP-project `stock-analysis-489016`, regio `europe-west4`
- Supabase-project `dacmqkjvofqqjfsfrtlp`, regio `eu-west-3`
- S3-endpoint: `https://dacmqkjvofqqjfsfrtlp.storage.supabase.co/storage/v1/s3`
- Bucket: `vaults`
- Objectsleutel: `{user_id}/{vault}/{pad}` — de `user_id` komt uit het JWT en nooit uit een argument
- Nieuwe service heet `notes-mcp`; de bestaande `lazytheta-mcp` blijft ongemoeid
- Fase 1 schrijft niet: er komt geen `put_object` in deze code

---

## File Structure

| bestand | verantwoordelijkheid |
|---|---|
| `mcp_auth.py` (verplaatst naar root) | OAuth 2.1 + PKCE-brug naar Supabase Auth, gedeeld door beide services |
| `notes-mcp-cloudrun/vault_paths.py` | Sleutels bouwen en paden valideren. Geen I/O, volledig puur |
| `notes-mcp-cloudrun/vault_storage.py` | Objecten oplijsten en ophalen uit de bucket. Client geïnjecteerd |
| `notes-mcp-cloudrun/notes_tools.py` | De drie tools: `list_vaults`, `read_note`, `search_notes` |
| `notes-mcp-cloudrun/mcp_handler.py` | JSON-RPC-dispatch en de tool-schema's |
| `notes-mcp-cloudrun/main.py` | Starlette-app, routes, auth-middleware |
| `notes-mcp-cloudrun/requirements.txt` | Afhankelijkheden van deze service |
| `notes-mcp-cloudrun/test_notes_mcp.py` | Tests, draaien zonder Supabase |
| `notes-mcp-cloudrun/README.md` | Deploy-instructies en secrets |
| `Dockerfile.notes` (root) | Build met de repo-root als context, zodat `mcp_auth.py` meekomt |
| `cloudbuild.notes.yaml` (root) | Wijst die Dockerfile aan; `--source .` doet dat niet |

De scheiding tussen `vault_paths`, `vault_storage` en `notes_tools` is bewust: padveiligheid is de plek waar een fout een lek wordt, en die wil je zonder netwerk kunnen testen.

---

## Task 1: Vaults naar de bucket, sync op beide apparaten

Geen code. Levert op zichzelf al waarde: de vaults staan overal, ook zonder MCP.

**Files:** geen

**Interfaces:**
- Produces: bucket `vaults` met objecten onder `{user_id}/{vault}/...`; S3-sleutelpaar in Secret Manager

- [ ] **Step 1: Maak een kopie vóór er iets verandert**

```bash
cd /Users/administrator/Documents
cp -R Obsidian "Obsidian-backup-$(date +%Y%m%d)"
du -sh Obsidian-backup-*
```

Verwacht: ~13 MB. Deze kopie blijft staan tot fase 1 helemaal werkt.

- [ ] **Step 2: Maak de bucket en zet het S3-protocol aan**

In het Supabase-dashboard van project `dacmqkjvofqqjfsfrtlp`:
1. Storage → New bucket → naam `vaults`, **niet** publiek.
2. Storage → Configuration → S3 → S3-protocol inschakelen.
3. Genereer een access key + secret. **De secret wordt één keer getoond.**

Noteer endpoint (`https://dacmqkjvofqqjfsfrtlp.storage.supabase.co/storage/v1/s3`) en regio (`eu-west-3`).

- [ ] **Step 3: Zet de sleutels in Secret Manager**

```bash
printf '%s' 'PLAK_ACCESS_KEY_ID' | gcloud secrets create SUPABASE_S3_ACCESS_KEY_ID \
    --project stock-analysis-489016 --data-file=-
printf '%s' 'PLAK_SECRET_ACCESS_KEY' | gcloud secrets create SUPABASE_S3_SECRET_ACCESS_KEY \
    --project stock-analysis-489016 --data-file=-
```

Bestaat een secret al, gebruik dan `gcloud secrets versions add <naam> --data-file=-`.

- [ ] **Step 4: Installeer Remotely Save op de Mac en configureer één vault**

Begin met `prins-social-tracker-vault` — 7 bestanden, 20 kB, de kleinste. Loopt het mis, dan is de schade nul.

In Obsidian: Settings → Community plugins → Browse → "Remotely Save" → Install → Enable. Dan in de plugin-instellingen:
- Remote service: **S3**
- Endpoint: `https://dacmqkjvofqqjfsfrtlp.storage.supabase.co/storage/v1/s3`
- Region: `eu-west-3`
- Access key / Secret key: het paar uit Step 2
- Bucket: `vaults`
- **End-to-end encryption: uit.** Staat die aan, dan kan de server niets lezen.
- Sync on startup: aan. Auto sync interval: 5 minuten.

Draai één sync.

- [ ] **Step 5: Controleer wat er in de bucket staat**

```bash
AWS_ACCESS_KEY_ID='...' AWS_SECRET_ACCESS_KEY='...' \
aws s3 ls s3://vaults/ --recursive \
    --endpoint-url https://dacmqkjvofqqjfsfrtlp.storage.supabase.co/storage/v1/s3 \
    --region eu-west-3
```

Verwacht: 7 `.md`-objecten. **Let op de sleutelvorm.** Remotely Save schrijft standaard vanaf de vault-root, dus je krijgt vermoedelijk `Notitie.md` en niet `{user_id}/prins-social-tracker-vault/Notitie.md`.

Zet daarom in de plugin het **remote prefix** (in Remotely Save: "Remote Base Directory" of gelijkwaardig) op `{user_id}/{vaultnaam}`, met je eigen Supabase-user-id: `02d70077-9c3f-4494-a37d-0fa3e8f68f98`.

Herhaal de sync en de listing tot de sleutels de vorm `02d70077-.../prins-social-tracker-vault/....md` hebben. Dit moet kloppen vóór Task 3, want daar wordt die vorm vastgelegd in code.

- [ ] **Step 6: Doe de overige vier vaults, portfolio-vault als laatste**

Zelfde configuratie, per vault een eigen remote prefix. `portfolio-vault` (177 bestanden) als laatste, omdat dat de vault is waar de meeste geschiedenis in zit.

- [ ] **Step 7: Richt de iPhone in**

Obsidian op iOS → vault aanmaken → community plugins inschakelen → Remotely Save installeren en identiek configureren. Sync draaien.

- [ ] **Step 8: Bewijs dat de sync in beide richtingen werkt**

Maak op de iPhone een notitie `sync-test.md` met de tekst `hallo van de telefoon`. Sync. Sync op de Mac. Het bestand staat in `~/Documents/Obsidian/prins-social-tracker-vault/`.

Doe daarna hetzelfde omgekeerd en verwijder `sync-test.md` als het werkt.

---

## Task 2: `mcp_auth.py` delen in plaats van kopiëren

Puur verplaatsen, geen gedragsverandering. De bestaande tests bewijzen dat.

**Files:**
- Move: `lazytheta-mcp-cloudrun/mcp_auth.py` → `mcp_auth.py`
- Modify: `Dockerfile`
- Test: `lazytheta-mcp-cloudrun/test_app.py` (ongewijzigd, moet groen blijven)

**Interfaces:**
- Produces: `mcp_auth` importeerbaar vanaf de repo-root met o.a. `verify_jwt(token) -> dict | None`, `sign_jwt(payload) -> str`, en de OAuth-routehandlers die `main.py` importeert

- [ ] **Step 1: Draai de bestaande tests, zodat je weet dat ze nu groen zijn**

Run: `cd /Users/administrator/Documents/GitHub/stock-analysis && python3 -m pytest lazytheta-mcp-cloudrun/test_app.py -q`
Expected: PASS

- [ ] **Step 2: Verplaats het bestand**

```bash
cd /Users/administrator/Documents/GitHub/stock-analysis
git mv lazytheta-mcp-cloudrun/mcp_auth.py mcp_auth.py
```

- [ ] **Step 3: Pas de Dockerfile aan**

In `Dockerfile`, haal `lazytheta-mcp-cloudrun/mcp_auth.py` weg uit de handler-COPY en voeg `mcp_auth.py` toe aan de gedeelde COPY:

```dockerfile
# Cloud Run handler files
COPY lazytheta-mcp-cloudrun/main.py \
     lazytheta-mcp-cloudrun/mcp_handler.py \
     /app/

# Shared modules from repo root, imported by mcp_server.py's _*_impl chain
COPY mcp_auth.py mcp_server.py valuation_lenses.py \
     config_store.py dcf_calculator.py gather_data.py \
     scorecard_utils.py robustness.py notifications.py \
     t212_api.py /app/
```

- [ ] **Step 4: Draai de tests opnieuw**

Run: `python3 -m pytest lazytheta-mcp-cloudrun/test_app.py -q`
Expected: PASS — `test_app.py` zet de repo-root al op `sys.path`, dus de import blijft werken.

- [ ] **Step 5: Controleer dat de image nog bouwt**

Run: `docker build -t lazytheta-check .`
Expected: succesvolle build. Geen Docker lokaal? Sla over; Task 7 valideert de build alsnog bij de deploy van de andere service.

- [ ] **Step 6: Lint en commit**

```bash
python3 -m ruff check .
git add -A
git commit -m "Move mcp_auth to the repo root so both services share it"
```

---

## Task 3: `vault_paths.py` — sleutels bouwen en paden weigeren

De plek waar een fout een lek wordt, dus zonder netwerk testbaar.

**Files:**
- Create: `notes-mcp-cloudrun/vault_paths.py`
- Test: `notes-mcp-cloudrun/test_notes_mcp.py`

**Interfaces:**
- Produces:
  - `UnsafePath(ValueError)`
  - `vault_prefix(user_id: str, vault: str | None = None) -> str`
  - `storage_key(user_id: str, vault: str, path: str) -> str`
  - `note_path(user_id: str, vault: str, key: str) -> str` — sleutel terug naar het pad zoals de aanroeper het kent

- [ ] **Step 1: Schrijf de falende tests**

Maak `notes-mcp-cloudrun/test_notes_mcp.py`:

```python
"""Tests voor de notities-MCP. Draaien zonder Supabase: de opslagclient
wordt geïnjecteerd, net zoals compute_screener(universe, fetch) dat doet."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))

USER = "02d70077-9c3f-4494-a37d-0fa3e8f68f98"


# ── vault_paths ────────────────────────────────────────────────────────────

def test_storage_key_prefixes_the_user():
    from vault_paths import storage_key
    assert storage_key(USER, "portfolio-vault", "Tickers/DECK.md") == (
        f"{USER}/portfolio-vault/Tickers/DECK.md")


def test_parent_traversal_is_refused():
    """Een pad dat uit de vault klimt komt bij de notities van iemand anders
    uit. Dit is de les uit het load_credential-lek: de user-id hoort niet
    beinvloedbaar te zijn door wat de aanroeper meestuurt."""
    from vault_paths import UnsafePath, storage_key
    for bad in ("../other/x.md", "Tickers/../../x.md", "..%2Fx.md"):
        with pytest.raises(UnsafePath):
            storage_key(USER, "portfolio-vault", bad)


def test_absolute_paths_are_refused():
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "portfolio-vault", "/etc/passwd.md")


def test_only_markdown_is_addressable():
    """De tools werken op notities. Bijlagen synchroniseren wel mee, maar
    zijn geen tekst die je kunt lezen of doorzoeken."""
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "portfolio-vault", "Attachments/plaatje.png")


def test_a_vault_name_cannot_contain_a_separator():
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "../andere-user", "x.md")


def test_vault_prefix_without_vault_is_the_user_root():
    from vault_paths import vault_prefix
    assert vault_prefix(USER) == f"{USER}/"
    assert vault_prefix(USER, "lazytheta-vault") == f"{USER}/lazytheta-vault/"


def test_note_path_is_the_inverse_of_storage_key():
    from vault_paths import note_path, storage_key
    key = storage_key(USER, "lazytheta-vault", "06 Open Issues.md")
    assert note_path(USER, "lazytheta-vault", key) == "06 Open Issues.md"
```

- [ ] **Step 2: Draai de tests en zie ze falen**

Run: `python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py -q`
Expected: FAIL met `ModuleNotFoundError: No module named 'vault_paths'`

- [ ] **Step 3: Schrijf de implementatie**

Maak `notes-mcp-cloudrun/vault_paths.py`:

```python
"""Objectsleutels bouwen voor de vault, en paden weigeren die dat niet mogen.

De user-id wordt hier voorgezet en is voor de aanroeper onbereikbaar. Dat is
geen stijlkeuze: deze server draait met een sleutel die alle buckettoegang
heeft, dus "de opslag regelt het wel" is een aanname en geen bescherming --
dezelfde fout die load_credential maakte toen het zonder user_id draaide.

Geen I/O, zodat de gevaarlijkste code in dit project zonder netwerk te
testen is.
"""

from urllib.parse import unquote

NOTE_SUFFIX = ".md"


class UnsafePath(ValueError):
    """Het pad klimt uit de vault, is absoluut, of is geen notitie."""


def _check_segment(name: str, what: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise UnsafePath(f"{what} is leeg")
    if "/" in cleaned or "\\" in cleaned or cleaned in (".", ".."):
        raise UnsafePath(f"{what} mag geen padscheiding bevatten: {name!r}")
    return cleaned


def vault_prefix(user_id: str, vault: str | None = None) -> str:
    """De sleutelprefix van een gebruiker, of van een vault daarbinnen."""
    user = _check_segment(user_id, "user_id")
    if vault is None:
        return f"{user}/"
    return f"{user}/{_check_segment(vault, 'vault')}/"


def storage_key(user_id: str, vault: str, path: str) -> str:
    """Volledige objectsleutel voor een notitie. Werpt UnsafePath."""
    prefix = vault_prefix(user_id, vault)

    # Percent-encoding eerst weghalen: "..%2Fx.md" is hetzelfde pad als
    # "../x.md" en moet dus dezelfde weigering krijgen.
    candidate = unquote(path or "").strip()
    if not candidate:
        raise UnsafePath("pad is leeg")
    if candidate.startswith("/") or candidate.startswith("\\"):
        raise UnsafePath(f"pad moet relatief zijn: {path!r}")
    if not candidate.lower().endswith(NOTE_SUFFIX):
        raise UnsafePath(f"alleen {NOTE_SUFFIX}-notities: {path!r}")

    parts = [p for p in candidate.replace("\\", "/").split("/") if p != ""]
    if any(p == ".." for p in parts):
        raise UnsafePath(f"pad klimt uit de vault: {path!r}")

    return prefix + "/".join(parts)


def note_path(user_id: str, vault: str, key: str) -> str:
    """Sleutel terug naar het pad zoals de aanroeper het kent."""
    prefix = vault_prefix(user_id, vault)
    if not key.startswith(prefix):
        raise UnsafePath(f"sleutel hoort niet bij {vault}: {key!r}")
    return key[len(prefix):]
```

- [ ] **Step 4: Draai de tests en zie ze slagen**

Run: `python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py -q`
Expected: PASS, 7 tests

- [ ] **Step 5: Lint en commit**

```bash
python3 -m ruff check .
git add notes-mcp-cloudrun/vault_paths.py notes-mcp-cloudrun/test_notes_mcp.py
git commit -m "Build vault keys with the user prefix the caller cannot reach"
```

---

## Task 4: `vault_storage.py` — objecten oplijsten en ophalen

**Files:**
- Create: `notes-mcp-cloudrun/vault_storage.py`
- Modify: `notes-mcp-cloudrun/test_notes_mcp.py`

**Interfaces:**
- Consumes: niets uit eerdere tasks
- Produces:
  - `StorageUnavailable(RuntimeError)`
  - `NoteNotFound(LookupError)`
  - `VaultStore(client, bucket: str)` met
    - `list_keys(prefix: str) -> list[str]`
    - `get(key: str) -> tuple[str, str]` — (tekst, revisie)
    - `get_many(keys: list[str], max_workers: int = 16) -> dict[str, str]`
  - `make_client()` — boto3-client uit omgevingsvariabelen

- [ ] **Step 1: Schrijf de falende tests**

Voeg toe aan `notes-mcp-cloudrun/test_notes_mcp.py`:

```python
# ── vault_storage ──────────────────────────────────────────────────────────

class FakeS3:
    """Genoeg van de boto3-client om VaultStore te testen. Elke sleutel
    bevat (inhoud, etag); een sleutel in `broken` gooit een transportfout."""

    def __init__(self, objects=None, broken=()):
        self.objects = objects or {}
        self.broken = set(broken)
        self.calls = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class _Pager:
            def paginate(self, Bucket, Prefix):
                if "LIST" in outer.broken:
                    raise OSError("bucket unreachable")
                keys = sorted(k for k in outer.objects if k.startswith(Prefix))
                # twee pagina's, zodat paginering echt getest wordt
                half = max(1, len(keys) // 2)
                for chunk in (keys[:half], keys[half:]):
                    yield {"Contents": [{"Key": k} for k in chunk]} if chunk else {}

        return _Pager()

    def get_object(self, Bucket, Key):
        self.calls.append(Key)
        if Key in self.broken:
            raise OSError("connection reset")
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        body, etag = self.objects[Key]

        class _Body:
            def read(self_inner):
                return body.encode("utf-8")

        return {"Body": _Body(), "ETag": etag}


def test_list_keys_walks_every_page():
    """Eén pagina lezen en stoppen laat notities onzichtbaar achter -- het
    soort stille verlies waar de floor-guard in refresh_universe voor is."""
    from vault_storage import VaultStore
    objects = {f"{USER}/v/n{i}.md": (f"tekst {i}", f'"e{i}"') for i in range(9)}
    store = VaultStore(FakeS3(objects), "vaults")
    assert len(store.list_keys(f"{USER}/v/")) == 9


def test_get_returns_text_and_revision():
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("hallo", '"abc123"')}), "vaults")
    text, revision = store.get(f"{USER}/v/a.md")
    assert text == "hallo"
    assert revision == "abc123"          # zonder de aanhalingstekens van S3


def test_a_missing_note_is_not_an_empty_note():
    from vault_storage import NoteNotFound, VaultStore
    store = VaultStore(FakeS3({}), "vaults")
    with pytest.raises(NoteNotFound):
        store.get(f"{USER}/v/weg.md")


def test_a_transport_failure_never_looks_like_data():
    """Een lege lijst teruggeven terwijl de bucket plat ligt is een onware
    uitspraak over de vault. Zelfde regel als EdgarFetchError."""
    from vault_storage import StorageUnavailable, VaultStore
    store = VaultStore(FakeS3({}, broken=["LIST"]), "vaults")
    with pytest.raises(StorageUnavailable):
        store.list_keys(f"{USER}/")


def test_get_raises_when_the_connection_breaks():
    from vault_storage import StorageUnavailable, VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("x", '"e"')}, broken=[f"{USER}/v/a.md"]),
                       "vaults")
    with pytest.raises(StorageUnavailable):
        store.get(f"{USER}/v/a.md")


def test_get_many_returns_every_requested_note():
    from vault_storage import VaultStore
    objects = {f"{USER}/v/n{i}.md": (f"tekst {i}", f'"e{i}"') for i in range(20)}
    store = VaultStore(FakeS3(objects), "vaults")
    got = store.get_many(list(objects))
    assert len(got) == 20
    assert got[f"{USER}/v/n7.md"] == "tekst 7"
```

- [ ] **Step 2: Draai de tests en zie ze falen**

Run: `python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py -q -k "list_keys or revision or missing or transport or connection or get_many"`
Expected: FAIL met `ModuleNotFoundError: No module named 'vault_storage'`

- [ ] **Step 3: Schrijf de implementatie**

Maak `notes-mcp-cloudrun/vault_storage.py`:

```python
"""Objecten uit de vault-bucket oplijsten en ophalen.

De client wordt geinjecteerd, zodat alles zonder Supabase te testen is --
hetzelfde patroon als compute_screener(universe, fetch).

Eén regel is niet onderhandelbaar: een transportfout mag nooit op data
lijken. Een lege lijst teruggeven terwijl de bucket onbereikbaar is, is een
uitspraak over de vault die niet waar is, en die leugen blijft hangen zolang
de aanroeper hem gelooft.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor


class StorageUnavailable(RuntimeError):
    """De bucket kon niet bevraagd worden. Niet te verwarren met leeg."""


class NoteNotFound(LookupError):
    """De sleutel bestaat niet. Niet te verwarren met een leeg bestand."""


def _is_missing(exc) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in ("NoSuchKey", "404", "NoSuchBucket")


class VaultStore:
    def __init__(self, client, bucket: str):
        self._client = client
        self._bucket = bucket

    def list_keys(self, prefix: str) -> list[str]:
        """Alle objectsleutels onder prefix, over alle pagina's heen."""
        keys: list[str] = []
        try:
            pages = self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=prefix)
            for page in pages:
                for item in page.get("Contents") or []:
                    keys.append(item["Key"])
        except Exception as e:
            raise StorageUnavailable(f"kon {prefix!r} niet oplijsten: {e}") from e
        return keys

    def get(self, key: str) -> tuple[str, str]:
        """(tekst, revisie) van één object."""
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as e:
            if _is_missing(e):
                raise NoteNotFound(key) from e
            raise StorageUnavailable(f"kon {key!r} niet ophalen: {e}") from e
        text = obj["Body"].read().decode("utf-8", "replace")
        return text, (obj.get("ETag") or "").strip('"')

    def get_many(self, keys: list[str], max_workers: int = 16) -> dict[str, str]:
        """Meerdere objecten parallel. Sleutels die intussen weg zijn vallen
        stil af; een transportfout doet dat niet en komt naar boven."""
        if not keys:
            return {}

        def one(key):
            try:
                return key, self.get(key)[0]
            except NoteNotFound:
                return key, None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            pairs = list(pool.map(one, keys))
        return {k: v for k, v in pairs if v is not None}


def make_client():
    """boto3-client tegen Supabase Storage, uit de omgeving."""
    import boto3

    endpoint = os.environ["SUPABASE_S3_ENDPOINT"]
    region = os.environ.get("SUPABASE_S3_REGION", "eu-west-3")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=os.environ["SUPABASE_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["SUPABASE_S3_SECRET_ACCESS_KEY"],
    )
```

- [ ] **Step 4: Voeg boto3 toe aan de requirements**

Maak `notes-mcp-cloudrun/requirements.txt`:

```
starlette==0.40.0
uvicorn==0.31.1
httpx==0.27.2
pyjwt==2.10.1
python-multipart==0.0.12
boto3==1.35.49
```

- [ ] **Step 5: Installeer boto3 lokaal en draai de tests**

```bash
python3 -m pip install boto3==1.35.49
python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py -q
```

Expected: PASS, 13 tests

- [ ] **Step 6: Lint en commit**

```bash
python3 -m ruff check .
git add notes-mcp-cloudrun/vault_storage.py notes-mcp-cloudrun/requirements.txt \
        notes-mcp-cloudrun/test_notes_mcp.py
git commit -m "Read vault objects; a transport failure never returns as empty"
```

---

## Task 5: `notes_tools.py` — de drie tools

**Files:**
- Create: `notes-mcp-cloudrun/notes_tools.py`
- Modify: `notes-mcp-cloudrun/test_notes_mcp.py`

**Interfaces:**
- Consumes: `vault_paths.storage_key`, `vault_paths.vault_prefix`, `vault_paths.note_path`, `vault_storage.VaultStore`, `vault_storage.NoteNotFound`
- Produces:
  - `snippet(text: str, query: str, radius: int = 120) -> str`
  - `list_vaults(store, user_id: str) -> list[dict]` — elk `{"vault", "notes", "claude_md"}`
  - `read_note(store, user_id: str, vault: str, path: str) -> dict` — `{"vault", "path", "revision", "content"}`
  - `search_notes(store, user_id: str, query: str, vault: str | None = None, max_results: int = 20) -> list[dict]` — elk `{"vault", "path", "snippet"}`

- [ ] **Step 1: Schrijf de falende tests**

Voeg toe aan `notes-mcp-cloudrun/test_notes_mcp.py`:

```python
# ── notes_tools ────────────────────────────────────────────────────────────

def _store_with_vaults():
    from vault_storage import VaultStore
    objects = {
        f"{USER}/portfolio-vault/CLAUDE.md": ("# regels\nfrontmatter verplicht", '"c1"'),
        f"{USER}/portfolio-vault/Tickers/DECK.md": (
            "---\nticker: DECK\n---\nDe wheel op DECK liep goed dit jaar.", '"d1"'),
        f"{USER}/portfolio-vault/Tickers/MSFT.md": ("MSFT is een compounder.", '"m1"'),
        f"{USER}/lazytheta-vault/06 Open Issues.md": ("Kapotte fair values.", '"o1"'),
        f"{USER}/portfolio-vault/Attachments/plaatje.png": ("binair", '"p1"'),
    }
    return VaultStore(FakeS3(objects), "vaults")


def test_list_vaults_counts_notes_and_finds_the_rules():
    """De CLAUDE.md van een vault beschrijft hoe erin geschreven hoort te
    worden. Die moet vindbaar zijn voordat fase 2 iets wegschrijft."""
    from notes_tools import list_vaults
    got = {v["vault"]: v for v in list_vaults(_store_with_vaults(), USER)}
    assert set(got) == {"portfolio-vault", "lazytheta-vault"}
    assert got["portfolio-vault"]["notes"] == 3        # de png telt niet mee
    assert got["portfolio-vault"]["claude_md"] == "CLAUDE.md"
    assert got["lazytheta-vault"]["claude_md"] is None


def test_read_note_returns_content_and_revision():
    from notes_tools import read_note
    got = read_note(_store_with_vaults(), USER, "portfolio-vault", "Tickers/DECK.md")
    assert "wheel op DECK" in got["content"]
    assert got["revision"] == "d1"
    assert got["path"] == "Tickers/DECK.md"


def test_read_note_refuses_to_leave_the_vault():
    from notes_tools import read_note
    from vault_paths import UnsafePath
    with pytest.raises(UnsafePath):
        read_note(_store_with_vaults(), USER, "portfolio-vault", "../lazytheta-vault/x.md")


def test_search_finds_the_note_and_shows_why():
    from notes_tools import search_notes
    hits = search_notes(_store_with_vaults(), USER, "wheel")
    assert len(hits) == 1
    assert hits[0]["path"] == "Tickers/DECK.md"
    assert hits[0]["vault"] == "portfolio-vault"
    assert "wheel" in hits[0]["snippet"].lower()


def test_search_is_case_insensitive():
    from notes_tools import search_notes
    assert search_notes(_store_with_vaults(), USER, "COMPOUNDER")


def test_search_can_be_scoped_to_one_vault():
    """Alles doorzoeken kost bij portfolio-vault 177 objecten; op naam
    beperken scheelt tijd en egress."""
    from notes_tools import search_notes
    assert search_notes(_store_with_vaults(), USER, "fair values",
                        vault="portfolio-vault") == []
    assert search_notes(_store_with_vaults(), USER, "fair values",
                        vault="lazytheta-vault")


def test_search_skips_attachments():
    from notes_tools import search_notes
    assert search_notes(_store_with_vaults(), USER, "binair") == []


def test_snippet_shows_context_around_the_match():
    from notes_tools import snippet
    text = "a" * 300 + "NAALD" + "b" * 300
    got = snippet(text, "naald", radius=20)
    assert "NAALD" in got
    assert len(got) < 100
    assert got.startswith("…") and got.endswith("…")


def test_snippet_without_a_match_returns_the_opening():
    from notes_tools import snippet
    assert snippet("korte notitie", "bestaat niet").startswith("korte")
```

- [ ] **Step 2: Draai de tests en zie ze falen**

Run: `python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py -q`
Expected: FAIL met `ModuleNotFoundError: No module named 'notes_tools'`

- [ ] **Step 3: Schrijf de implementatie**

Maak `notes-mcp-cloudrun/notes_tools.py`:

```python
"""De drie leestools, gebouwd op VaultStore.

Zoeken heeft geen index. Dat kan omdat de schaal het toelaat: 1,63 MB over
244 bestanden, dus alles ophalen kost per zoekopdracht een fractie van de
gratis egress. Een index zou een tweede kopie van de notities zijn, en
tweede kopieen van dezelfde waarheid hebben in dit project al drie keer een
bug opgeleverd.

Herzien zodra de gezamenlijke markdown boven ~10 MB komt of het aantal
bestanden boven ~1.000.
"""

from __future__ import annotations

from vault_paths import NOTE_SUFFIX, note_path, storage_key, vault_prefix
from vault_storage import NoteNotFound

CLAUDE_MD = "CLAUDE.md"


def _vault_of(user_id: str, key: str) -> str | None:
    prefix = vault_prefix(user_id)
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    return rest.split("/", 1)[0] if "/" in rest else None


def _is_note(key: str) -> bool:
    return key.lower().endswith(NOTE_SUFFIX)


def snippet(text: str, query: str, radius: int = 120) -> str:
    """Het stukje tekst rond de treffer, zodat zichtbaar is waarom een
    notitie in de uitkomst staat."""
    lowered = text.lower()
    at = lowered.find((query or "").lower())
    if at < 0:
        head = text[: radius * 2].strip()
        return head + ("…" if len(text) > radius * 2 else "")
    start = max(0, at - radius)
    end = min(len(text), at + len(query) + radius)
    body = text[start:end].strip()
    return ("…" if start > 0 else "") + body + ("…" if end < len(text) else "")


def list_vaults(store, user_id: str) -> list[dict]:
    """Welke vaults er zijn, hoeveel notities erin staan, en of er een
    CLAUDE.md ligt met de schrijfregels van die vault."""
    keys = store.list_keys(vault_prefix(user_id))
    vaults: dict[str, dict] = {}
    for key in keys:
        vault = _vault_of(user_id, key)
        if not vault or not _is_note(key):
            continue
        entry = vaults.setdefault(vault, {"vault": vault, "notes": 0, "claude_md": None})
        entry["notes"] += 1
        if note_path(user_id, vault, key) == CLAUDE_MD:
            entry["claude_md"] = CLAUDE_MD
    return [vaults[v] for v in sorted(vaults)]


def read_note(store, user_id: str, vault: str, path: str) -> dict:
    """Eén notitie, met revisie. De revisie doet in fase 1 niets, maar staat
    er zodat het contract bij het toevoegen van schrijven niet verandert."""
    key = storage_key(user_id, vault, path)
    text, revision = store.get(key)
    return {"vault": vault, "path": path, "revision": revision, "content": text}


def search_notes(store, user_id: str, query: str, vault: str | None = None,
                 max_results: int = 20) -> list[dict]:
    """Zoek op inhoud. Zonder vault doorzoekt hij alles."""
    needle = (query or "").strip()
    if not needle:
        return []

    keys = [k for k in store.list_keys(vault_prefix(user_id, vault)) if _is_note(k)]
    contents = store.get_many(keys)

    hits = []
    for key in keys:
        text = contents.get(key)
        if text is None or needle.lower() not in text.lower():
            continue
        found_in = vault or _vault_of(user_id, key)
        if not found_in:
            continue
        hits.append({
            "vault": found_in,
            "path": note_path(user_id, found_in, key),
            "snippet": snippet(text, needle),
        })
        if len(hits) >= max_results:
            break
    return hits


__all__ = ["NoteNotFound", "list_vaults", "read_note", "search_notes", "snippet"]
```

- [ ] **Step 4: Draai de tests en zie ze slagen**

Run: `python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py -q`
Expected: PASS, 22 tests

- [ ] **Step 5: Lint en commit**

```bash
python3 -m ruff check .
git add notes-mcp-cloudrun/notes_tools.py notes-mcp-cloudrun/test_notes_mcp.py
git commit -m "Search, list and read notes straight from the bucket"
```

---

## Task 6: MCP-handler en app

**Files:**
- Create: `notes-mcp-cloudrun/mcp_handler.py`
- Create: `notes-mcp-cloudrun/main.py`
- Modify: `notes-mcp-cloudrun/test_notes_mcp.py`

**Interfaces:**
- Consumes: `notes_tools.list_vaults`, `notes_tools.read_note`, `notes_tools.search_notes`; `mcp_auth.verify_jwt` en de OAuth-handlers uit de repo-root
- Produces: `mcp_handler.TOOLS`, `mcp_handler.mcp_endpoint`, `main.create_app()`

- [ ] **Step 1: Schrijf de falende tests**

Voeg toe aan `notes-mcp-cloudrun/test_notes_mcp.py`:

```python
# ── MCP-laag ───────────────────────────────────────────────────────────────

import asyncio


def test_the_three_tools_are_advertised():
    import mcp_handler
    assert {t["name"] for t in mcp_handler.TOOLS} == {
        "list_vaults", "search_notes", "read_note"}


def test_no_write_tool_exists_in_phase_one():
    """Fase 1 schrijft niet. Een tool die er per ongeluk in sluipt is de
    enige manier waarop deze fase notities kan beschadigen."""
    import mcp_handler
    names = {t["name"] for t in mcp_handler.TOOLS}
    assert not (names & {"write_note", "append_to_note", "delete_note"})


def test_every_tool_declares_a_schema():
    import mcp_handler
    for tool in mcp_handler.TOOLS:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


def test_a_call_without_a_user_is_refused():
    """user_id komt uit het JWT. Geen JWT, geen toegang tot notities."""
    import mcp_handler
    resp = asyncio.run(mcp_handler._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_vaults", "arguments": {}}}, None))
    assert resp["error"]["code"] == -32001


def test_the_user_id_in_the_arguments_is_ignored(monkeypatch):
    """Meesturen van een andere user_id mag niets doen -- de server neemt
    hem uit het token. Dit is het load_credential-lek in testvorm."""
    import mcp_handler
    seen = {}

    def fake_list_vaults(store, user_id):
        seen["user_id"] = user_id
        return []

    monkeypatch.setattr(mcp_handler, "_store", lambda: object())
    monkeypatch.setattr(mcp_handler.notes_tools, "list_vaults", fake_list_vaults)
    asyncio.run(mcp_handler._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_vaults",
                    "arguments": {"user_id": "iemand-anders"}}}, USER))
    assert seen["user_id"] == USER


def test_tools_list_needs_no_user():
    import mcp_handler
    resp = asyncio.run(mcp_handler._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, None))
    assert len(resp["result"]["tools"]) == 3


def test_the_app_exposes_health_and_mcp():
    import main
    app = main.create_app()
    assert app is not None
```

- [ ] **Step 2: Draai de tests en zie ze falen**

Run: `python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py -q`
Expected: FAIL met `ModuleNotFoundError: No module named 'mcp_handler'`

- [ ] **Step 3: Schrijf de handler**

Maak `notes-mcp-cloudrun/mcp_handler.py`:

```python
"""Stateless MCP JSON-RPC-dispatcher voor de Obsidian-vaults.

Elke /mcp-aanroep draagt een JWT; de middleware in main.py haalt daar de
user_id uit en zet hem in scope["state"]. Die user_id gaat naar elke tool.
Een user_id in de argumenten wordt genegeerd -- dat is de les uit het
load_credential-lek, waar een ongefilterde lezing de rijen van alle
gebruikers matchte.

Fase 1 leest alleen. Er is met opzet geen schrijftool.
"""

from __future__ import annotations

import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import notes_tools
from vault_paths import UnsafePath
from vault_storage import NoteNotFound, StorageUnavailable, VaultStore, make_client

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "notes-mcp"
SERVER_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

_CACHED_STORE = None


def _store() -> VaultStore:
    global _CACHED_STORE
    if _CACHED_STORE is None:
        _CACHED_STORE = VaultStore(make_client(),
                                   os.environ.get("VAULT_BUCKET", "vaults"))
    return _CACHED_STORE


async def _tool_list_vaults(user_id: str, args: dict):
    return notes_tools.list_vaults(_store(), user_id)


async def _tool_search_notes(user_id: str, args: dict):
    return notes_tools.search_notes(
        _store(), user_id, query=args["query"], vault=args.get("vault"),
        max_results=int(args.get("max_results", 20)))


async def _tool_read_note(user_id: str, args: dict):
    return notes_tools.read_note(_store(), user_id, args["vault"], args["path"])


TOOL_HANDLERS = {
    "list_vaults": _tool_list_vaults,
    "search_notes": _tool_search_notes,
    "read_note": _tool_read_note,
}

TOOLS: list[dict] = [
    {
        "name": "list_vaults",
        "description": (
            "List the Obsidian vaults, how many notes each holds, and whether "
            "it carries a CLAUDE.md with that vault's own conventions. Read "
            "that file before writing anything into a vault."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_notes",
        "description": (
            "Search note contents across the vaults. Returns the path plus the "
            "surrounding text of each match. Pass `vault` to narrow the search "
            "when you already know where to look."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "vault": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_note",
        "description": (
            "Read one note in full. `path` is relative to the vault root, for "
            "example 'Tickers/DECK.md'. Returns the content and a revision "
            "marker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["vault", "path"],
        },
    },
]


async def _handle_one(message: dict, user_id: str | None) -> dict | None:
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled",
                  "notifications/progress"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        if not user_id:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32001, "message": "Authenticated user required"}}
        tool_name = params.get("name")
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"}}
        try:
            result = await handler(user_id, params.get("arguments") or {})
        except (UnsafePath, NoteNotFound, KeyError, ValueError) as e:
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": f"Error: {e}"}],
                               "isError": True}}
        except StorageUnavailable as e:
            # Nadrukkelijk geen lege uitkomst: de vault is niet leeg, hij is
            # onbereikbaar, en dat verschil moet de aanroeper zien.
            logger.exception("storage unavailable")
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32002, "message": f"Vault storage unavailable: {e}"}}
        except Exception:
            logger.exception("Tool %s failed", tool_name)
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32603, "message": "Internal server error"}}

        import json
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text",
                                        "text": json.dumps(result, ensure_ascii=False)}]}}

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def mcp_endpoint(request: Request) -> Response:
    if request.method == "GET":
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "GET not supported"}},
                            status_code=405)
    if request.method == "DELETE":
        return Response(status_code=200)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error"}},
                            status_code=400)

    user_id = request.scope.get("state", {}).get("user_id")

    if isinstance(body, list):
        responses = [r for r in
                     [await _handle_one(m, user_id) for m in body] if r is not None]
        return JSONResponse(responses) if responses else Response(status_code=202)

    if not isinstance(body, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "Invalid request"}},
                            status_code=400)

    response = await _handle_one(body, user_id)
    return JSONResponse(response) if response is not None else Response(status_code=202)
```

- [ ] **Step 4: Schrijf de app**

Maak `notes-mcp-cloudrun/main.py`. Dit is dezelfde vorm als `lazytheta-mcp-cloudrun/main.py`; alleen de servicenaam en de handler-import verschillen. De middleware is bewust identiek, want de auth-regels horen niet per service te verschillen:

```python
"""Obsidian notes MCP -- multi-user, OAuth-brug naar Supabase Auth.

Dezelfde auth-opzet als de LazyTheta-server, met dezelfde mcp_auth-module uit
de repo-root. Wat verschilt is wat erachter zit: deze service leest notities
en heeft niets met beleggingsdata te maken. Dat is ook de reden dat het een
aparte service is -- schrijfrechten op de complete kennisbank is een veel
bredere bevoegdheid dan de portefeuilletools hebben.
"""

import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_auth import (
    oauth_authorize,
    oauth_authorize_magic,
    oauth_authorize_password,
    oauth_magic_callback,
    oauth_magic_finalize,
    oauth_register,
    oauth_token,
    verify_jwt,
    well_known_authorization_server,
    well_known_protected_resource,
)
from mcp_handler import mcp_endpoint

PUBLIC_PREFIXES = ("/oauth/", "/.well-known/", "/health")


class SmartAuthMiddleware:
    """Publieke paden gaan door; al het andere vereist een Bearer-JWT. De
    user_id uit dat token belandt in scope, zodat handlers hem niet uit de
    argumenten hoeven te halen -- en dus ook niet kunnen."""

    def __init__(self, app):
        self.app = app

    async def _passthrough(self, scope, receive, send):
        try:
            return await self.app(scope, receive, send)
        except Exception:
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
            try:
                await send({"type": "http.response.start", "status": 500,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body",
                            "body": b'{"error":"internal_server_error"}'})
            except Exception:
                pass

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await self._passthrough(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")

        if auth.startswith("Bearer "):
            payload = verify_jwt(auth[7:])
            if payload and payload.get("type") == "access_token" and payload.get("user_id"):
                scope.setdefault("state", {})["user_id"] = payload["user_id"]
                return await self._passthrough(scope, receive, send)

        host = headers.get(b"x-forwarded-host", headers.get(b"host", b"")).decode("latin-1")
        proto = headers.get(b"x-forwarded-proto", b"https").decode("latin-1")
        www_auth = (f'Bearer resource_metadata="{proto}://{host}'
                    f'/.well-known/oauth-protected-resource"')

        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"www-authenticate", www_auth.encode("latin-1"))]})
        await send({"type": "http.response.body", "body": b"Unauthorized"})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "notes-mcp"})


def create_app():
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server",
              well_known_authorization_server, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource",
              well_known_protected_resource, methods=["GET"]),
        Route("/oauth/register", oauth_register, methods=["POST"]),
        Route("/oauth/authorize", oauth_authorize, methods=["GET"]),
        Route("/oauth/authorize/magic", oauth_authorize_magic, methods=["POST"]),
        Route("/oauth/authorize/password", oauth_authorize_password, methods=["POST"]),
        Route("/oauth/magic-callback", oauth_magic_callback, methods=["GET"]),
        Route("/oauth/magic-finalize", oauth_magic_finalize, methods=["POST"]),
        Route("/oauth/token", oauth_token, methods=["POST"]),
        Route("/mcp", mcp_endpoint, methods=["POST", "GET", "DELETE"]),
    ]
    return SmartAuthMiddleware(Starlette(routes=routes))


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
```

- [ ] **Step 5: Draai de tests en zie ze slagen**

Run: `python3 -m pytest notes-mcp-cloudrun/test_notes_mcp.py -q`
Expected: PASS, 29 tests

- [ ] **Step 6: Lint en commit**

```bash
python3 -m ruff check .
git add notes-mcp-cloudrun/mcp_handler.py notes-mcp-cloudrun/main.py \
        notes-mcp-cloudrun/test_notes_mcp.py
git commit -m "Serve the three read tools over MCP, user id from the token only"
```

---

## Task 7: Bouwen, deployen en aansluiten

**Files:**
- Create: `Dockerfile.notes`
- Create: `cloudbuild.notes.yaml`
- Create: `notes-mcp-cloudrun/README.md`

**Interfaces:**
- Consumes: alle voorgaande tasks
- Produces: draaiende service `notes-mcp` in `europe-west4`, aangesloten als connector in Claude

- [ ] **Step 1: Schrijf de Dockerfile**

Maak `Dockerfile.notes` in de repo-root. De build-context is de root, want anders is `mcp_auth.py` onbereikbaar:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY notes-mcp-cloudrun/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY notes-mcp-cloudrun/main.py \
     notes-mcp-cloudrun/mcp_handler.py \
     notes-mcp-cloudrun/notes_tools.py \
     notes-mcp-cloudrun/vault_paths.py \
     notes-mcp-cloudrun/vault_storage.py \
     /app/

# Gedeeld met lazytheta-mcp; nooit kopieren, altijd deze ene
COPY mcp_auth.py /app/

EXPOSE 8080
ENV PORT=8080

CMD ["python", "main.py"]
```

- [ ] **Step 2: Schrijf de build-configuratie**

`gcloud run deploy --source .` pakt altijd `Dockerfile` en kent geen vlag voor een andere. Daarom een expliciete build. Maak `cloudbuild.notes.yaml`:

```yaml
steps:
  - name: gcr.io/cloud-builders/docker
    args:
      - build
      - -f
      - Dockerfile.notes
      - -t
      - europe-west4-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/notes-mcp:latest
      - .
images:
  - europe-west4-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/notes-mcp:latest
options:
  logging: CLOUD_LOGGING_ONLY
```

Een vaste `:latest`-tag in plaats van `$BUILD_ID`, zodat het deploy-commando de tag niet hoeft op te zoeken. Terugrollen doe je met de revisies van Cloud Run, niet met image-tags.

- [ ] **Step 3: Bouw en deploy**

```bash
cd /Users/administrator/Documents/GitHub/stock-analysis

gcloud builds submit --config cloudbuild.notes.yaml \
    --project stock-analysis-489016 --region europe-west4

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

`SUPABASE_SERVICE_KEY` gaat er bewust **niet** in: deze service heeft geen database nodig.

**Let op de sleutel: `JWT_SIGNING_KEY` wordt gevuld uit een ánder secret dan bij LazyTheta.** Dat is geen slordigheid maar de kern van de scheiding. `mcp_auth.verify_jwt` controleert alleen de handtekening — geen `aud`, geen `iss`, geen service-claim. Delen beide services hetzelfde ondertekeningsgeheim, dan opent elk dertig dagen geldig token van de portefeuilleserver ook `/mcp` van de notitieserver, en andersom bedient een notitietoken alle 34 LazyTheta-tools inclusief de schrijvende. Daarmee zou de hele reden om dit als aparte service te bouwen vervallen: de blast radius wordt weer één geheel.

Twee aparte geheimen kosten niets in code — `mcp_auth` leest gewoon zijn eigen omgevingsvariabele — en maken de scheiding echt.

Maak dat geheim vóór de deploy:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))" \
  | tr -d '\n' \
  | gcloud secrets create NOTES_JWT_SIGNING_KEY \
        --project stock-analysis-489016 --data-file=-
```

Gevolg voor de gebruiker: je logt bij deze connector één keer apart in via Supabase. Dat is precies de bedoeling.

- [ ] **Step 4: Controleer dat hij leeft**

```bash
URL=$(gcloud run services describe notes-mcp --project stock-analysis-489016 \
      --region europe-west4 --format='value(status.url)')
curl -s "$URL/health"
```

Expected: `{"status":"ok","service":"notes-mcp"}`

- [ ] **Step 5: Controleer dat hij niet zonder token praat**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$URL/mcp" \
     -H 'content-type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Expected: `401`

- [ ] **Step 6: Sluit de connector aan**

Op claude.ai → Settings → Connectors → Add custom connector → de URL uit Step 4 met `/mcp` erachter. Doorloop de Supabase-login. Controleer daarna in een gesprek dat `list_vaults` de vijf vaults teruggeeft met de juiste aantallen (portfolio-vault 177, lazytheta-vault 34 na deze sessie).

- [ ] **Step 7: Bewijs het doel van fase 1 op de telefoon**

Open Claude op de iPhone en vraag: *"zoek in portfolio-vault wat ik over de wheel op DECK schreef"*. Verwacht: een treffer met pad en tekstfragment.

Pas daarna `~/Documents/Obsidian-backup-*` opruimen.

- [ ] **Step 8: Schrijf de README en commit**

Maak `notes-mcp-cloudrun/README.md` met: de service-URL, de deploy-commando's uit Step 3, de lijst secrets en env-vars, en een regel dat `Dockerfile.notes` niet via `--source .` gebouwd kan worden. Naar het voorbeeld van `lazytheta-mcp-cloudrun/README.md`.

```bash
python3 -m ruff check .
git add Dockerfile.notes cloudbuild.notes.yaml notes-mcp-cloudrun/README.md
git commit -m "Build and deploy notes-mcp as its own Cloud Run service"
```

---

## Wat er na fase 1 nog niet is

Schrijven. Dat krijgt een eigen plan zodra fase 1 draait, met `write_note`, `append_to_note` en `list_notes`, en de ETag-controle die weigert te overschrijven wat niet gelezen is.

Eén ding om dan uit te zoeken: of Supabase Storage `If-Match` op `PutObject` ondersteunt. Zo ja, dan is de controle atomair. Zo nee, dan is het vergelijken-dan-schrijven met een klein venster ertussen — voor een persoonlijke vault aanvaardbaar, maar het moet eerlijk in de code staan en niet als garantie worden opgeschreven.
