"""routes/props.py: saved props and sets, PATCH prop, photo upload. Runs on mongomock."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import auth
import schemas
from db import client as db_client
from db import families as families_db
from db import media as media_db
from db import people as people_db
from db import props as props_db
from db import users as users_db
from db.client import get_db
from routes import props as props_routes

REPO = Path(__file__).resolve().parents[3]
SAMPLE = json.loads((REPO / "web" / "public" / "samples" / "hymn_books.memory.json").read_text())

FAM_A = "fam_a"
FAM_B = "fam_b"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def library_dir(tmp_path, monkeypatch):
    """A library with one prop, `chair`, used instead of shared/props/."""
    lib = tmp_path / "props"
    lib.mkdir()
    (lib / "chair.json").write_text(
        json.dumps({"name": "chair", "size": "furniture", "art": {"body": None}})
    )
    monkeypatch.setattr(props_db, "LIBRARY_DIR", lib)
    props_db.library_props.cache_clear()
    yield lib
    props_db.library_props.cache_clear()


def _prop(pid: str, name: str, description: str | None = None) -> schemas.Prop:
    return schemas.Prop(id=pid, name=name, description=description, size="hand", art={"body": None},
                        color="#2f4a7a")


@pytest.fixture
def env(mdb, library_dir):
    """Two families. Alice is in A, Bob in B; each family has its own props, sets and photo."""
    db_client.ensure_indexes(mdb)
    alice = users_db.create_user(mdb, email="alice@x.com", password_hash=auth.hash_password("pw-alice"),
                                 name="Alice")
    bob = users_db.create_user(mdb, email="bob@x.com", password_hash=auth.hash_password("pw-bob"), name="Bob")
    families_db.create_family(mdb, name="Family A", creator_user_id=alice.id, family_id=FAM_A)
    families_db.create_family(mdb, name="Family B", creator_user_id=bob.id, family_id=FAM_B)
    people_db.create_person(mdb, FAM_A, schemas.NewPerson(name="Alice"), added_by=alice.id, user_id=alice.id)
    people_db.create_person(mdb, FAM_B, schemas.NewPerson(name="Bob"), added_by=bob.id, user_id=bob.id)

    sample_prop = schemas.Prop.model_validate(SAMPLE["props"][0])
    sample_set = schemas.SetDef.model_validate(SAMPLE["sets"][0])
    props_db.insert_prop(mdb, FAM_A, sample_prop, prop_id=sample_prop.id)
    props_db.insert_prop(mdb, FAM_A, _prop("trumpet_case", "trumpet case", "an old black case"),
                         prop_id="trumpet_case")
    props_db.insert_set(mdb, FAM_A, sample_set, set_id=sample_set.id)
    props_db.insert_prop(mdb, FAM_B, _prop("b_lamp", "brass lamp"), prop_id="b_lamp")
    props_db.insert_set(mdb, FAM_B, sample_set.model_copy(update={"name": "B's kitchen"}), set_id="b_kitchen")
    b_photo = media_db.put_media(mdb, FAM_B, PNG, content_type="image/png", filename="b.png")

    app = FastAPI()
    app.include_router(props_routes.router)
    app.dependency_overrides[get_db] = lambda: mdb
    tc = TestClient(app)
    tc.cookies.set(auth.COOKIE_NAME, auth.create_session_token(alice.id))
    return {"tc": tc, "db": mdb, "alice": alice, "bob": bob, "b_photo": b_photo}


def _upload(tc: TestClient, fid: str, data: bytes = PNG, content_type: str = "image/png",
            filename: str = "photo.png"):
    return tc.post(f"/families/{fid}/media", files={"file": (filename, data, content_type)})


# --------------------------------------------------------------------- list


def test_list_props_returns_only_the_familys_saved_props(env):
    resp = env["tc"].get(f"/families/{FAM_A}/props")
    assert resp.status_code == 200
    props = resp.json()["props"]
    assert [p["id"] for p in props] == ["hymn_books", "trumpet_case"]  # by name; no library chair, no b_lamp
    assert all(p["familyId"] == FAM_A for p in props)
    assert props[0]["art"] == {"body": None}
    assert "embedding" not in props[0] and "_id" not in props[0]
    schemas.PropsResponse.model_validate(resp.json())


def test_list_sets_returns_only_the_familys_sets(env):
    resp = env["tc"].get(f"/families/{FAM_A}/sets")
    assert resp.status_code == 200
    sets = resp.json()["sets"]
    assert [s["id"] for s in sets] == ["church_steps_1988"]
    assert sets[0]["marks"]["steps_foot"] == [0.5, 0.0]
    schemas.SetsResponse.model_validate(resp.json())


def test_empty_family_lists_are_empty(env):
    db = env["db"]
    families_db.create_family(db, name="Empty", creator_user_id=env["alice"].id, family_id="fam_empty")
    assert env["tc"].get("/families/fam_empty/props").json() == {"props": []}
    assert env["tc"].get("/families/fam_empty/sets").json() == {"sets": []}


def test_signed_out_is_401(env):
    tc = env["tc"]
    tc.cookies.clear()
    assert tc.get(f"/families/{FAM_A}/props").status_code == 401
    assert tc.get(f"/families/{FAM_A}/sets").status_code == 401
    assert tc.patch(f"/families/{FAM_A}/props/hymn_books", json={"name": "x"}).status_code == 401
    assert _upload(tc, FAM_A).status_code == 401


# --------------------------------------------------------------------- patch


def test_rename_trims_and_persists(env):
    resp = env["tc"].patch(f"/families/{FAM_A}/props/hymn_books", json={"name": "  grandma's hymn books  "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "grandma's hymn books"
    assert body["id"] == "hymn_books" and body["familyId"] == FAM_A
    assert body["source"] == "generated"  # a rename leaves the art alone
    stored = props_db.get_prop(env["db"], FAM_A, "hymn_books")
    assert stored.name == "grandma's hymn books"
    schemas.Prop.model_validate(body)


@pytest.mark.parametrize("payload", [{}, {"name": None}, {"name": None, "photoId": None}])
def test_empty_patch_is_422(env, payload):
    resp = env["tc"].patch(f"/families/{FAM_A}/props/hymn_books", json=payload)
    assert resp.status_code == 422


@pytest.mark.parametrize("name", ["", "   ", "\n\t"])
def test_blank_name_is_422(env, name):
    resp = env["tc"].patch(f"/families/{FAM_A}/props/hymn_books", json={"name": name})
    assert resp.status_code == 422
    assert props_db.get_prop(env["db"], FAM_A, "hymn_books").name == "hymn books"


def test_unknown_field_is_422(env):
    resp = env["tc"].patch(f"/families/{FAM_A}/props/hymn_books", json={"title": "x"})
    assert resp.status_code == 422


@pytest.mark.parametrize("as_ref", [False, True])
def test_replace_with_uploaded_photo(env, as_ref):
    tc = env["tc"]
    media_id = _upload(tc, FAM_A).json()["id"]
    photo_id = f"media/{media_id}" if as_ref else media_id
    resp = tc.patch(f"/families/{FAM_A}/props/hymn_books", json={"photoId": photo_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["art"] == {"body": f"media/{media_id}"}
    assert body["source"] == "photo"
    assert body["states"] == {} and body["parts"] == {}  # hymn_books has no parts or states
    assert body["name"] == "hymn books"
    assert body["grip"] == [0.5, 0.0]  # the rig is kept
    stored = props_db.get_prop(env["db"], FAM_A, "hymn_books")
    assert stored.art == {"body": f"media/{media_id}"} and stored.source == "photo"


def test_photo_keeps_parts_part_art_and_state_names(env):
    """A pull tab on a wheel or a set_state in a saved scene must still find its target."""
    tc, db = env["tc"], env["db"]
    bike = schemas.Prop(
        id="red_bike", name="red bike", size="furniture", color="#c0392b",
        art={"body": "media/old_body", "wheel": "media/old_wheel", "ringing": "media/old_ring"},
        parts={"wheel_front": schemas.PropPart(art="wheel", at=[0.8, 0.8], spin=True),
               "wheel_back": schemas.PropPart(art="wheel", at=[0.2, 0.8], spin=True),
               "bell": schemas.PropPart(art="bell", at=[0.7, 0.2])},
        states={"quiet": {}, "ringing": {"body": "media/old_ring"},
                "spinning": {"body": "media/b2", "wheel": "media/blur"}},
        sockets={"seat": [0.45, 0.35]}, grip=[0.75, 0.25],
    )
    props_db.insert_prop(db, FAM_A, bike, prop_id="red_bike")
    media_id = _upload(tc, FAM_A).json()["id"]
    resp = tc.patch(f"/families/{FAM_A}/props/red_bike", json={"photoId": media_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["art"] == {"body": f"media/{media_id}", "wheel": "media/old_wheel", "bell": None}
    assert body["parts"] == {
        "wheel_front": {"art": "wheel", "at": [0.8, 0.8], "spin": True},
        "wheel_back": {"art": "wheel", "at": [0.2, 0.8], "spin": True},
        "bell": {"art": "bell", "at": [0.7, 0.2], "spin": False},
    }
    assert body["states"] == {"quiet": {}, "ringing": {}, "spinning": {"wheel": "media/blur"}}
    assert body["source"] == "photo"
    assert (body["sockets"], body["grip"], body["color"], body["size"]) == (
        {"seat": [0.45, 0.35]}, [0.75, 0.25], "#c0392b", "furniture")
    assert props_db.get_prop(db, FAM_A, "red_bike") == schemas.Prop.model_validate(body)


def test_rename_and_photo_together(env):
    tc = env["tc"]
    media_id = _upload(tc, FAM_A).json()["id"]
    resp = tc.patch(f"/families/{FAM_A}/props/trumpet_case", json={"name": " the case ", "photoId": media_id})
    assert resp.status_code == 200
    assert resp.json()["name"] == "the case"
    assert resp.json()["art"] == {"body": f"media/{media_id}"}


@pytest.mark.parametrize("photo_id", ["not-an-id", "", "media/", "000000000000000000000000"])
def test_unknown_photo_is_400(env, photo_id):
    resp = env["tc"].patch(f"/families/{FAM_A}/props/hymn_books", json={"photoId": photo_id})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown photo"
    assert props_db.get_prop(env["db"], FAM_A, "hymn_books").art == {"body": None}


def test_other_familys_photo_is_400(env):
    b_photo = env["b_photo"]
    for photo_id in (b_photo, f"media/{b_photo}"):
        resp = env["tc"].patch(f"/families/{FAM_A}/props/hymn_books", json={"photoId": photo_id})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unknown photo"
    assert props_db.get_prop(env["db"], FAM_A, "hymn_books").source == "generated"


@pytest.mark.parametrize("prop_id", ["chair", "b_lamp", "no_such_prop"])
def test_library_other_family_or_unknown_prop_is_404(env, prop_id):
    tc = env["tc"]
    assert tc.patch(f"/families/{FAM_A}/props/{prop_id}", json={"name": "renamed"}).status_code == 404
    media_id = _upload(tc, FAM_A).json()["id"]
    assert tc.patch(f"/families/{FAM_A}/props/{prop_id}", json={"photoId": media_id}).status_code == 404
    # Nothing changed anywhere.
    assert props_db.get_prop(env["db"], FAM_B, "b_lamp").name == "brass lamp"
    assert props_db.library_props()["chair"].name == "chair"


# --------------------------------------------------------------------- photo aspect


def _image(width: int, height: int, fmt: str = "PNG", orientation: int | None = None) -> bytes:
    """A real image made in memory, optionally with an EXIF orientation tag."""
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), (180, 90, 40))
    if orientation is None:
        img.save(buf, format=fmt)
    else:
        exif = Image.Exif()
        exif[0x0112] = orientation
        img.save(buf, format=fmt, exif=exif.tobytes())
    return buf.getvalue()


def _listed(tc: TestClient, prop_id: str) -> dict:
    return next(p for p in tc.get(f"/families/{FAM_A}/props").json()["props"] if p["id"] == prop_id)


def test_photo_sets_the_aspect_from_its_pixel_size(env):
    tc = env["tc"]
    before = _listed(tc, "hymn_books")
    upload = _upload(tc, FAM_A, data=_image(800, 200), filename="wide.png")
    assert upload.status_code == 201
    media_id = upload.json()["id"]
    resp = tc.patch(f"/families/{FAM_A}/props/hymn_books", json={"photoId": media_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["aspect"] == 4.0
    assert body["art"] == {"body": f"media/{media_id}"}
    assert body["source"] == "photo"
    # The rig is kept.
    for key in ("size", "anchor", "grip", "sockets", "color"):
        assert body[key] == before[key], key
    assert _listed(tc, "hymn_books")["aspect"] == 4.0


@pytest.mark.parametrize(("data", "content_type", "expected"), [
    (_image(300, 600), "image/png", 0.5),
    # 400x200 pixels, EXIF orientation 6: a phone portrait, shown rotated 90 degrees.
    (_image(400, 200, fmt="JPEG", orientation=6), "image/jpeg", 0.5),
    (_image(400, 200, fmt="JPEG"), "image/jpeg", 2.0),
])
def test_photo_aspect_follows_the_displayed_image(env, data, content_type, expected):
    tc = env["tc"]
    media_id = _upload(tc, FAM_A, data=data, content_type=content_type).json()["id"]
    resp = tc.patch(f"/families/{FAM_A}/props/trumpet_case", json={"photoId": f"media/{media_id}"})
    assert resp.status_code == 200
    assert resp.json()["aspect"] == expected
    assert _listed(tc, "trumpet_case")["aspect"] == expected


def test_rename_leaves_the_aspect_alone(env):
    tc = env["tc"]
    media_id = _upload(tc, FAM_A, data=_image(800, 200)).json()["id"]
    assert tc.patch(f"/families/{FAM_A}/props/hymn_books", json={"photoId": media_id}).json()["aspect"] == 4.0
    resp = tc.patch(f"/families/{FAM_A}/props/hymn_books", json={"name": "hymnals"})
    assert resp.status_code == 200
    assert resp.json()["aspect"] == 4.0 and resp.json()["name"] == "hymnals"


def test_photo_that_cannot_be_decoded_keeps_the_aspect(env):
    tc = env["tc"]
    before = _listed(tc, "hymn_books")["aspect"]
    media_id = _upload(tc, FAM_A, data=b"not an image").json()["id"]  # stored as image/png
    resp = tc.patch(f"/families/{FAM_A}/props/hymn_books", json={"photoId": media_id})
    assert resp.status_code == 200
    assert resp.json()["aspect"] == before
    assert resp.json()["source"] == "photo"


def test_same_prop_id_in_two_families_stays_separate(env):
    """Family B saves a prop under A's slug; each family sees and patches only its own."""
    db = env["db"]
    props_db.insert_prop(db, FAM_B, _prop("trumpet_case", "B's trumpet case"), prop_id="trumpet_case")
    resp = env["tc"].patch(f"/families/{FAM_A}/props/trumpet_case", json={"name": "A's case"})
    assert resp.status_code == 200 and resp.json()["familyId"] == FAM_A
    assert props_db.get_prop(db, FAM_B, "trumpet_case").name == "B's trumpet case"
    assert [p["name"] for p in env["tc"].get(f"/families/{FAM_A}/props").json()["props"]
            if p["id"] == "trumpet_case"] == ["A's case"]


# --------------------------------------------------------------------- upload


@pytest.mark.parametrize("content_type", sorted(props_routes.ALLOWED_IMAGE_TYPES))
def test_upload_returns_201_and_is_retrievable(env, content_type):
    data = PNG + content_type.encode()
    resp = _upload(env["tc"], FAM_A, data=data, content_type=content_type, filename="steps.jpg")
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id"}
    assert media_db.parse_media_ref(body["id"]) == body["id"]  # bare id, not 'media/<id>'
    stored = media_db.get_media(env["db"], FAM_A, body["id"])
    assert stored is not None
    assert stored.data == data
    assert stored.content_type == content_type
    assert stored.kind == "photo"
    assert stored.filename == "steps.jpg"
    assert media_db.get_media(env["db"], FAM_B, body["id"]) is None


def test_upload_heif_is_201_and_stored_as_heif(env):
    resp = _upload(env["tc"], FAM_A, data=PNG, content_type="image/heif", filename="IMG_0001.heif")
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id"} and body["id"]
    stored = media_db.get_media(env["db"], FAM_A, body["id"])
    assert stored is not None
    assert stored.content_type == "image/heif"


def test_upload_is_typed_as_media_upload_response(env):
    """The 201 response is schemas.MediaUploadResponse in the OpenAPI document and on the wire."""
    route = next(
        r for r in props_routes.router.routes
        if getattr(r, "path", None) == "/families/{fid}/media" and "POST" in getattr(r, "methods", ())
    )
    assert route.response_model is schemas.MediaUploadResponse
    assert route.status_code == 201

    openapi = env["tc"].get("/openapi.json").json()
    created = openapi["paths"]["/families/{fid}/media"]["post"]["responses"]["201"]
    assert created["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/MediaUploadResponse"}
    assert openapi["components"]["schemas"]["MediaUploadResponse"]["required"] == ["id"]

    resp = _upload(env["tc"], FAM_A)
    assert resp.status_code == 201
    body = resp.json()
    assert list(body) == ["id"] and isinstance(body["id"], str) and body["id"]
    assert schemas.MediaUploadResponse.model_validate(body).id == body["id"]


def test_allowed_types_match_memory_submit():
    # The prop-art upload and the memory photo upload must accept the same formats.
    from routes import memories as memories_routes

    assert props_routes.ALLOWED_IMAGE_TYPES == memories_routes.ALLOWED_PHOTO_TYPES


def test_upload_content_type_with_parameters(env):
    resp = _upload(env["tc"], FAM_A, content_type="IMAGE/PNG; foo=bar")
    assert resp.status_code == 201
    assert media_db.get_media(env["db"], FAM_A, resp.json()["id"]).content_type == "image/png"


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/pdf", "image/svg+xml", "image/tiff", "video/mp4"]
)
def test_upload_wrong_type_is_415(env, content_type):
    resp = _upload(env["tc"], FAM_A, content_type=content_type, filename="notes.txt")
    assert resp.status_code == 415
    assert resp.json()["detail"] == "Upload a JPEG, PNG, WebP, GIF or HEIC/HEIF image"
    assert env["db"][media_db.INLINE].count_documents({"metadata.familyId": FAM_A}) == 0


def test_upload_empty_is_400(env):
    resp = _upload(env["tc"], FAM_A, data=b"")
    assert resp.status_code == 400
    assert env["db"][media_db.INLINE].count_documents({"metadata.familyId": FAM_A}) == 0


def test_upload_oversize_is_413(env, monkeypatch):
    monkeypatch.setattr(media_db, "MAX_BYTES", 32)
    assert _upload(env["tc"], FAM_A, data=b"x" * 33).status_code == 413
    assert env["db"][media_db.INLINE].count_documents({"metadata.familyId": FAM_A}) == 0
    exact = _upload(env["tc"], FAM_A, data=b"x" * 32)
    assert exact.status_code == 201


def test_upload_missing_file_is_422(env):
    resp = env["tc"].post(f"/families/{FAM_A}/media", data={"other": "x"})
    assert resp.status_code == 422


# --------------------------------------------------------------------- cross-family


def test_member_of_a_gets_404_for_family_b_on_every_route(env):
    tc = env["tc"]
    cases = [
        tc.get(f"/families/{FAM_B}/props"),
        tc.get(f"/families/{FAM_B}/sets"),
        tc.patch(f"/families/{FAM_B}/props/b_lamp", json={"name": "stolen"}),
        tc.patch(f"/families/{FAM_B}/props/b_lamp", json={"photoId": env["b_photo"]}),
        tc.patch(f"/families/{FAM_B}/props/b_lamp", json={}),  # 404 wins over 422
        _upload(tc, FAM_B),
        _upload(tc, FAM_B, content_type="text/plain"),  # 404 wins over 415
        tc.get("/families/no_such_family/props"),
    ]
    for resp in cases:
        assert resp.status_code == 404, (resp.request.method, resp.request.url, resp.text)
        assert resp.json() == {"detail": "Not found"}
    db = env["db"]
    assert props_db.get_prop(db, FAM_B, "b_lamp").name == "brass lamp"
    assert db[media_db.INLINE].count_documents({"metadata.familyId": FAM_B}) == 1


def test_b_prop_id_under_fid_a_is_404(env):
    resp = env["tc"].patch(f"/families/{FAM_A}/props/b_lamp", json={"name": "mine now"})
    assert resp.status_code == 404
    assert props_db.get_prop(env["db"], FAM_B, "b_lamp").name == "brass lamp"


def test_responses_never_contain_password_hash(env):
    tc = env["tc"]
    media_id = _upload(tc, FAM_A).json()["id"]
    for resp in (
        tc.get(f"/families/{FAM_A}/props"),
        tc.get(f"/families/{FAM_A}/sets"),
        tc.patch(f"/families/{FAM_A}/props/hymn_books", json={"name": "books", "photoId": media_id}),
    ):
        assert resp.status_code == 200
        assert "passwordHash" not in resp.text and "argon2" not in resp.text
