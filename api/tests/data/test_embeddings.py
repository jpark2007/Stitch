"""db/embeddings.py (the Voyage embedder) and the vectors db/props.py stores on save.

Offline: every embedder here gets an injected `post`, and urllib is booby-trapped so a
real HTTP call fails the test.
"""

from __future__ import annotations

import urllib.request

import pytest

import schemas
from db import client, embeddings, search
from db import props as P

FAM_A = "fam_a"
KEY = "pa-test-not-a-real-key"


@pytest.fixture(autouse=True)
def _offline_and_no_embedder(monkeypatch: pytest.MonkeyPatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("a test tried to make a real HTTP call")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    for name in ("VOYAGE_API_KEY", "VOYAGE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    search.set_embedder(None)
    yield
    search.set_embedder(None)


class Recorder:
    """A fake transport: records each call and replies with a vector of the requested size."""

    def __init__(self, reply=None):
        self.calls: list[dict] = []
        self.reply = reply

    def __call__(self, url: str, payload: dict, headers: dict, timeout: float) -> dict:
        self.calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        if self.reply is not None:
            return self.reply
        size = payload["output_dimension"]
        return {"object": "list", "data": [{"object": "embedding", "index": 0,
                                            "embedding": [0.5] * (size - 1) + [1]}]}


# ------------------------------------------------------------------ voyage_embedder


def test_voyage_embedder_posts_the_request_and_returns_the_vector():
    post = Recorder()
    embed = embeddings.voyage_embedder(KEY, post=post)
    vector = embed("hymn books. Two blue hymn books")

    assert vector == [0.5] * (client.VECTOR_DIM - 1) + [1.0]
    assert all(isinstance(x, float) for x in vector)
    [call] = post.calls
    assert call["url"] == "https://api.voyageai.com/v1/embeddings" == embeddings.VOYAGE_URL
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["payload"] == {
        "input": ["hymn books. Two blue hymn books"],
        "model": "voyage-3.5",
        "output_dimension": client.VECTOR_DIM,
    }
    assert call["timeout"] == 10.0


def test_voyage_embedder_model_dimensions_and_timeout_are_passed_through():
    post = Recorder()
    embed = embeddings.voyage_embedder(f"  {KEY} ", model="voyage-3.5-lite", dimensions=4, timeout=2.5, post=post)
    assert embed("trumpet") == [0.5, 0.5, 0.5, 1.0]
    [call] = post.calls
    assert call["payload"] == {"input": ["trumpet"], "model": "voyage-3.5-lite", "output_dimension": 4}
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"  # stripped
    assert call["timeout"] == 2.5


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"data": []},
        {"data": [{}]},
        {"data": [{"embedding": None}]},
        {"data": [{"embedding": "0.1,0.2"}]},
        {"data": [{"embedding": []}]},
        {"data": [{"embedding": [0.1, "x", 0.3, 0.4]}]},
        {"data": [{"embedding": [0.1, True, 0.3, 0.4]}]},
        {"data": [{"embedding": [0.1, 0.2]}]},  # wrong length
        ["not", "a", "dict"],
        None,
    ],
)
def test_voyage_embedder_raises_on_a_malformed_reply(reply):
    post = Recorder(reply=reply if reply is not None else {"data": None})
    embed = embeddings.voyage_embedder(KEY, dimensions=4, post=post)
    with pytest.raises(ValueError) as info:
        embed("hymn books")
    assert KEY not in str(info.value)


def test_voyage_embedder_needs_a_key():
    for key in ("", "   "):
        with pytest.raises(ValueError):
            embeddings.voyage_embedder(key, post=Recorder())


def test_a_failing_transport_through_search_embed_gives_none():
    def boom(url, payload, headers, timeout):
        raise OSError("connection refused")

    search.set_embedder(embeddings.voyage_embedder(KEY, post=boom))
    assert search.embed("hymn books") is None

    search.set_embedder(embeddings.voyage_embedder(KEY, post=Recorder(reply={"data": [{"embedding": "x"}]})))
    assert search.embed("hymn books") is None

    post = Recorder()
    search.set_embedder(embeddings.voyage_embedder(KEY, dimensions=3, post=post))
    assert search.embed("hymn books") == [0.5, 0.5, 1.0]


def test_the_default_transport_is_urllib(monkeypatch: pytest.MonkeyPatch):
    """Without `post`, the request goes through urllib.request (stubbed here, no network)."""
    seen = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"data": [{"embedding": [1, 2, 3]}]}'

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["auth"] = request.get_header("Authorization")
        seen["type"] = request.get_header("Content-type")
        seen["body"] = request.data
        seen["timeout"] = timeout
        return Reply()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert embeddings.voyage_embedder(KEY, dimensions=3, timeout=4.0)("hi") == [1.0, 2.0, 3.0]
    assert seen["url"] == embeddings.VOYAGE_URL
    assert seen["method"] == "POST"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["type"] == "application/json"
    assert seen["timeout"] == 4.0
    assert b'"input": ["hi"]' in seen["body"] and b'"output_dimension": 3' in seen["body"]


# ------------------------------------------------------------------ embedder_from_env


def test_embedder_from_env_is_none_without_a_key(monkeypatch: pytest.MonkeyPatch):
    assert embeddings.embedder_from_env() is None
    for blank in ("", "   ", "\t"):
        monkeypatch.setenv("VOYAGE_API_KEY", blank)
        assert embeddings.embedder_from_env() is None


def test_embedder_from_env_builds_an_embedder_without_calling_it(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VOYAGE_API_KEY", KEY)
    embed = embeddings.embedder_from_env()
    assert callable(embed)
    assert embed.model == "voyage-3.5"
    assert embed.dimensions == client.VECTOR_DIM

    monkeypatch.setenv("VOYAGE_MODEL", " voyage-3-large ")
    assert embeddings.embedder_from_env().model == "voyage-3-large"
    monkeypatch.setenv("VOYAGE_MODEL", "  ")
    assert embeddings.embedder_from_env().model == "voyage-3.5"


# ------------------------------------------------------------------ props and sets


class FakeEmbedder:
    """Deterministic vector from the text, recording every text it was given."""

    def __init__(self):
        self.texts: list[str] = []

    def __call__(self, text: str) -> list[float]:
        self.texts.append(text)
        return [float(len(text)), float(sum(map(ord, text)) % 97), 1.0]

    def vector(self, text: str) -> list[float]:
        return [float(len(text)), float(sum(map(ord, text)) % 97), 1.0]


@pytest.fixture
def fake_embedder():
    fake = FakeEmbedder()
    search.set_embedder(fake)
    yield fake
    search.set_embedder(None)


def _prop(name="trumpet case", description="a battered brown case", **kw) -> schemas.Prop:
    return schemas.Prop(id="ignored", name=name, description=description, size="carried",
                        art={"body": None}, **kw)


def _set(name="church steps", description="stone steps outside St. Mary's") -> schemas.SetDef:
    return schemas.SetDef(id="ignored", name=name, description=description,
                          layers=[schemas.SetLayer(art=None, depth=0, height=2.0)])


def _raw(mdb, coll, doc_id):
    return mdb[coll].find_one({"_id": doc_id})


def test_insert_prop_and_set_store_a_vector_from_name_and_description(mdb, fake_embedder):
    prop = P.insert_prop(mdb, FAM_A, _prop(), prop_id="case")
    s = P.insert_set(mdb, FAM_A, _set(), set_id="steps")

    assert fake_embedder.texts == ["trumpet case. a battered brown case",
                                   "church steps. stone steps outside St. Mary's"]
    assert _raw(mdb, "props", "case")["embedding"] == fake_embedder.vector("trumpet case. a battered brown case")
    assert _raw(mdb, "sets", "steps")["embedding"] == fake_embedder.vector(
        "church steps. stone steps outside St. Mary's")
    # The vector is storage-only: never on the model or what reads return.
    assert "embedding" not in prop.model_dump() and "embedding" not in s.model_dump()
    assert P.get_prop(mdb, FAM_A, "case") == prop


def test_insert_without_a_description_embeds_the_name(mdb, fake_embedder):
    P.insert_prop(mdb, FAM_A, _prop(name="accordion", description=None), prop_id="acc")
    P.insert_set(mdb, FAM_A, _set(name="kitchen", description="  "), set_id="kitchen")
    assert fake_embedder.texts == ["accordion", "kitchen"]
    assert _raw(mdb, "props", "acc")["embedding"] == fake_embedder.vector("accordion")


def test_an_explicit_embedding_wins_over_the_embedder(mdb, fake_embedder):
    P.insert_prop(mdb, FAM_A, _prop(), prop_id="case", embedding=[9, 8, 7])
    P.insert_set(mdb, FAM_A, _set(), set_id="steps", embedding=[1, 2])
    assert fake_embedder.texts == []
    assert _raw(mdb, "props", "case")["embedding"] == [9.0, 8.0, 7.0]
    assert _raw(mdb, "sets", "steps")["embedding"] == [1.0, 2.0]


def test_replace_with_a_changed_description_recomputes_the_vector(mdb, fake_embedder):
    saved = P.insert_prop(mdb, FAM_A, _prop(), prop_id="case")
    changed = saved.model_copy(update={"description": "a black case with a red lining"})
    assert P.replace_prop(mdb, FAM_A, changed) == changed
    assert fake_embedder.texts[-1] == "trumpet case. a black case with a red lining"
    assert _raw(mdb, "props", "case")["embedding"] == fake_embedder.vector(
        "trumpet case. a black case with a red lining")

    s = P.insert_set(mdb, FAM_A, _set(), set_id="steps")
    P.replace_set(mdb, FAM_A, s.model_copy(update={"description": "wet steps in the rain"}))
    assert _raw(mdb, "sets", "steps")["embedding"] == fake_embedder.vector("church steps. wet steps in the rain")


def test_replace_with_the_same_description_keeps_the_vector(mdb, fake_embedder):
    saved = P.insert_prop(mdb, FAM_A, _prop(), prop_id="case", embedding=[1, 2, 3])
    P.replace_prop(mdb, FAM_A, saved.model_copy(update={"name": "Dad's trumpet case", "color": "#553311"}))
    assert fake_embedder.texts == []
    assert _raw(mdb, "props", "case")["embedding"] == [1.0, 2.0, 3.0]


def test_replace_backfills_a_vector_the_prop_never_had(mdb, fake_embedder):
    search.set_embedder(None)
    saved = P.insert_prop(mdb, FAM_A, _prop(), prop_id="case")  # saved offline: no vector
    assert "embedding" not in _raw(mdb, "props", "case")
    search.set_embedder(fake_embedder)
    P.replace_prop(mdb, FAM_A, saved)
    assert _raw(mdb, "props", "case")["embedding"] == fake_embedder.vector("trumpet case. a battered brown case")


def test_no_embedder_writes_no_vector(mdb):
    saved = P.insert_prop(mdb, FAM_A, _prop(), prop_id="case")
    s = P.insert_set(mdb, FAM_A, _set(), set_id="steps")
    P.replace_prop(mdb, FAM_A, saved.model_copy(update={"description": "new"}))
    P.replace_set(mdb, FAM_A, s.model_copy(update={"description": "new"}))
    assert "embedding" not in _raw(mdb, "props", "case")
    assert "embedding" not in _raw(mdb, "sets", "steps")


def test_replace_with_a_changed_description_and_no_embedder_drops_the_stale_vector(mdb):
    saved = P.insert_prop(mdb, FAM_A, _prop(), prop_id="case", embedding=[1, 2, 3])
    P.replace_prop(mdb, FAM_A, saved.model_copy(update={"description": "different"}))
    assert "embedding" not in _raw(mdb, "props", "case")


def test_a_failing_embedder_never_stops_a_save(mdb):
    def boom(text):
        raise RuntimeError("Voyage is down")

    search.set_embedder(boom)
    saved = P.insert_prop(mdb, FAM_A, _prop(), prop_id="case")
    P.insert_set(mdb, FAM_A, _set(), set_id="steps")
    assert P.replace_prop(mdb, FAM_A, saved.model_copy(update={"description": "x"})) is not None
    assert "embedding" not in _raw(mdb, "props", "case")
    assert "embedding" not in _raw(mdb, "sets", "steps")

    # Even a voyage embedder whose transport fails: no vector, no error.
    search.set_embedder(embeddings.voyage_embedder(KEY, post=lambda *a: (_ for _ in ()).throw(OSError("down"))))
    P.insert_prop(mdb, FAM_A, _prop(name="bugle"), prop_id="bugle")
    assert "embedding" not in _raw(mdb, "props", "bugle")


def test_saving_through_a_voyage_embedder_stores_its_vector(mdb):
    post = Recorder()
    search.set_embedder(embeddings.voyage_embedder(KEY, dimensions=3, post=post))
    P.insert_set(mdb, FAM_A, _set(), set_id="steps")
    assert _raw(mdb, "sets", "steps")["embedding"] == [0.5, 0.5, 1.0]
    assert post.calls[0]["payload"]["input"] == ["church steps. stone steps outside St. Mary's"]
