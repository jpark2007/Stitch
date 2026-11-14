"""Text embeddings for Atlas Vector Search, from Voyage AI (ARCHITECTURE.md section 5).

`db.search` holds the embedder that the whole API uses (`set_embedder` / `embed`): "ask the
family" turns its query into a vector with it, and db/props.py stores a vector on each prop
and set it saves. main.py installs `embedder_from_env()` at startup on a real database, so
vectors exist only when VOYAGE_API_KEY is set; otherwise everything uses keyword search.

The vector length is `client.VECTOR_DIM` (env EMBEDDING_DIM, default 1024), the
`numDimensions` of the vector indexes that db/client.py creates, unless a caller asks for
another. voyage-3.5 accepts 256, 512, 1024 or 2048.

No dependency beyond the standard library: the default transport is urllib.request.
Tests inject `post`, so nothing here makes a network call unless an embedder is called.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable

from db import client
from db.search import Embedder

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_MODEL = "voyage-3.5"

# post(url, payload, headers, timeout) -> the decoded JSON response. Raises on any failure.
Post = Callable[[str, dict, dict, float], dict]


def _urllib_post(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """POST payload as JSON and decode the JSON reply. HTTP errors raise urllib.error.HTTPError."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read().decode("utf-8"))


def voyage_embedder(
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    dimensions: int | None = None,
    timeout: float = 10.0,
    post: Post | None = None,
) -> Embedder:
    """An embedder (text -> vector) backed by the Voyage AI embeddings API.

    Each call POSTs {"input": [text], "model": model, "output_dimension": dimensions or
    client.VECTOR_DIM} to VOYAGE_URL with "Authorization: Bearer <api_key>" and returns
    data[0].embedding as floats. It raises on a transport error and on a reply that is not
    a list of numbers of the requested length; `db.search.embed` turns any of those into
    None (no vector, keyword search). The key is never put in an error message.
    """
    if not api_key or not api_key.strip():
        raise ValueError("voyage_embedder needs an API key")
    key = api_key.strip()
    size = int(dimensions or client.VECTOR_DIM)
    send: Post = post or _urllib_post

    def embed(text: str) -> list[float]:
        payload = {"input": [text], "model": model, "output_dimension": size}
        reply = send(VOYAGE_URL, payload, {"Authorization": f"Bearer {key}"}, timeout)
        try:
            vector = reply["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Voyage AI reply has no data[0].embedding") from exc
        if not isinstance(vector, list) or not vector:
            raise ValueError(f"Voyage AI embedding is not a non-empty list (got {type(vector).__name__})")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vector):
            raise ValueError("Voyage AI embedding has a non-numeric value")
        if len(vector) != size:
            raise ValueError(f"Voyage AI embedding has {len(vector)} dimensions, expected {size}")
        return [float(x) for x in vector]

    embed.model = model  # type: ignore[attr-defined] - for the startup log line
    embed.dimensions = size  # type: ignore[attr-defined]
    return embed


def embedder_from_env() -> Embedder | None:
    """The Voyage embedder when VOYAGE_API_KEY is set and non-blank, else None.

    VOYAGE_MODEL picks the model (default voyage-3.5); the vector length is EMBEDDING_DIM
    (client.VECTOR_DIM). Builds the function only: no call is made here.
    """
    key = (os.environ.get("VOYAGE_API_KEY") or "").strip()
    if not key:
        return None
    model = (os.environ.get("VOYAGE_MODEL") or "").strip() or DEFAULT_MODEL
    return voyage_embedder(key, model=model)
