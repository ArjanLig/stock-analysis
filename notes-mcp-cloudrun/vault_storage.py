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
    """Alleen een ontbrekende sleutel telt als 'niet gevonden'.

    NoSuchBucket stond hier eerder ook in, en dan meldde de server "die notitie
    bestaat niet" terwijl de hele bucket weg of verkeerd geconfigureerd was --
    een storing die als data leest. Datzelfde geldt voor de kale "404" van
    sommige S3-compatibele endpoints: te grof om er een uitspraak over één
    notitie op te baseren. Alles wat geen NoSuchKey is wordt StorageUnavailable.

    getattr(...) or {}: botocore-excepties hebben niet altijd een .response, en
    soms is hij None. Die AttributeError ontsnapte aan de except van get().
    """
    response = getattr(exc, "response", None) or {}
    if not isinstance(response, dict):
        return False
    code = (response.get("Error") or {}).get("Code", "")
    return code == "NoSuchKey"


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
