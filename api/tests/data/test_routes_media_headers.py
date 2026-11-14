"""GET /families/{fid}/media/{id} always sends the sandbox Content-Security-Policy.

That header (routes.tree.MEDIA_CSP) plus `X-Content-Type-Options: nosniff` is what stops an
uploaded file from running as a page on the API origin. These tests drive the whole app
(main.create_app(), so every middleware is in the path) over mongomock: sign up, start a
family, upload through POST /families/{fid}/media, then read each file back.

The upload route takes images only (routes.props.ALLOWED_IMAGE_TYPES), so the two uploads
are a PNG and a JPEG. Narration audio, which the agent stores directly, is put in with
db.media and read through the same route.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from db import client as db_client
from db import media as media_db
from routes import tree as tree_routes

PASSWORD = "correct horse battery"


def _image_bytes(fmt: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 120, 40)).save(buf, format=fmt)
    return buf.getvalue()


PNG = _image_bytes("PNG")
JPEG = _image_bytes("JPEG")


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
def app(mdb):
    """The full app over mongomock. The lifespan is not run (TestClient is not entered)."""
    db_client.ensure_indexes(mdb)
    db_client.set_db(mdb)
    yield main.create_app()
    db_client.set_db(None)


def _signup(app, email: str, name: str) -> TestClient:
    tc = TestClient(app)
    res = tc.post("/auth/signup", json={"email": email, "password": PASSWORD, "name": name})
    assert res.status_code == 201, res.text
    return tc


def _create_family(tc: TestClient, name: str, person_name: str) -> str:
    res = tc.post("/families", json={"name": name, "person": {"name": person_name}})
    assert res.status_code == 201, res.text
    return res.json()["family"]["id"]


def _upload(tc: TestClient, fid: str, filename: str, data: bytes, content_type: str) -> str:
    res = tc.post(f"/families/{fid}/media", files={"file": (filename, data, content_type)})
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.fixture
def member(app):
    """(client signed in as the family's admin, family id)."""
    tc = _signup(app, "ada@example.com", "Ada")
    return tc, _create_family(tc, "The Lovelaces", "Ada")


def _assert_sandboxed(res) -> None:
    csp = res.headers.get("content-security-policy")
    assert csp == tree_routes.MEDIA_CSP
    directives = [d.strip() for d in csp.split(";")]
    assert "sandbox" in directives
    assert "default-src 'none'" in directives
    assert res.headers.get("x-content-type-options") == "nosniff"


# ------------------------------------------------------------------------------ the policy


def test_media_csp_constant_is_the_sandbox_policy():
    """Pins the constant itself, so weakening it is a visible test change."""
    directives = [d.strip() for d in tree_routes.MEDIA_CSP.split(";")]
    assert directives == [
        "default-src 'none'",
        "img-src 'self'",
        "media-src 'self'",
        "style-src 'unsafe-inline'",
        "sandbox",
    ]


# ------------------------------------------------------------------------------ success paths


@pytest.mark.parametrize(
    ("filename", "data", "content_type"),
    [("cake.png", PNG, "image/png"), ("porch.jpg", JPEG, "image/jpeg")],
    ids=["png", "jpeg"],
)
def test_uploaded_photo_is_served_sandboxed(member, filename, data, content_type):
    tc, fid = member
    media_id = _upload(tc, fid, filename, data, content_type)

    res = tc.get(f"/families/{fid}/media/{media_id}")

    assert res.status_code == 200
    assert res.content == data
    assert res.headers["content-type"] == content_type
    _assert_sandboxed(res)


def test_narration_audio_is_served_sandboxed(member, mdb):
    tc, fid = member
    audio = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" * 64
    media_id = media_db.put_media(mdb, fid, audio, content_type="audio/mpeg", filename="mom.mp3", kind="audio")

    res = tc.get(f"/families/{fid}/media/{media_id}")

    assert res.status_code == 200
    assert res.content == audio
    _assert_sandboxed(res)


def test_stored_html_is_still_sandboxed(member, mdb):
    """The case the header exists for: a stored file whose type a browser would render as a page."""
    tc, fid = member
    page = b"<!doctype html><script>fetch('/me')</script>"
    media_id = media_db.put_media(mdb, fid, page, content_type="text/html", filename="x.html")

    res = tc.get(f"/families/{fid}/media/{media_id}")

    assert res.status_code == 200
    _assert_sandboxed(res)


def test_encoded_media_ref_is_sandboxed(member):
    tc, fid = member
    media_id = _upload(tc, fid, "cake.png", PNG, "image/png")

    res = tc.get(f"/families/{fid}/media/media%2F{media_id}")

    assert res.status_code == 200
    _assert_sandboxed(res)


def test_byte_range_response_is_sandboxed(member):
    tc, fid = member
    media_id = _upload(tc, fid, "cake.png", PNG, "image/png")

    res = tc.get(f"/families/{fid}/media/{media_id}", headers={"Range": "bytes=0-7"})

    assert res.status_code == 206
    assert res.content == PNG[:8]
    _assert_sandboxed(res)


def test_unsatisfiable_range_is_sandboxed(member):
    tc, fid = member
    media_id = _upload(tc, fid, "cake.png", PNG, "image/png")

    res = tc.get(f"/families/{fid}/media/{media_id}", headers={"Range": f"bytes={len(PNG) + 10}-"})

    assert res.status_code == 416
    _assert_sandboxed(res)


def test_not_modified_has_no_body_to_sandbox(member):
    """A 304 carries no body, so it sends only the caching headers (as routes.tree does today)."""
    tc, fid = member
    media_id = _upload(tc, fid, "cake.png", PNG, "image/png")

    res = tc.get(f"/families/{fid}/media/{media_id}", headers={"If-None-Match": f'"{media_id}"'})

    assert res.status_code == 304
    assert res.content == b""


# ------------------------------------------------------------------------------ outsiders


def test_non_member_gets_404(app, member):
    tc, fid = member
    media_id = _upload(tc, fid, "cake.png", PNG, "image/png")
    outsider = _signup(app, "zoe@example.com", "Zoe")
    _create_family(outsider, "The Zs", "Zoe")

    res = outsider.get(f"/families/{fid}/media/{media_id}")

    assert res.status_code == 404
    assert res.content != PNG


def test_signed_out_gets_401(app, member):
    tc, fid = member
    media_id = _upload(tc, fid, "cake.png", PNG, "image/png")

    res = TestClient(app).get(f"/families/{fid}/media/{media_id}")

    assert res.status_code == 401
