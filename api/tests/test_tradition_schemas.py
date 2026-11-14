"""The traditions contract: request bounds, the category and `when` enums, PATCH shape."""

from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

import schemas
from schemas import CreateTraditionRequest, Tradition, TraditionsResponse, UpdateTraditionRequest

OK = {"title": "Sunday pancakes", "category": "food", "when": {"kind": "weekly", "label": "every Sunday"}}


def fields_of(exc: ValidationError) -> set[str]:
    return {str(e["loc"][-1]) if not isinstance(e["loc"][-1], int) else str(e["loc"][-2]) for e in exc.errors()}


def test_enums_are_the_documented_ones():
    assert get_args(schemas.TraditionCategory) == ("food", "holiday", "trip", "music", "game", "faith", "craft", "other")
    assert get_args(schemas.TraditionWhenKind) == ("yearly", "seasonal", "monthly", "weekly", "occasional")


def test_create_defaults_trim_and_camel_case():
    req = CreateTraditionRequest.model_validate({**OK, "title": "  Sunday pancakes  ", "startedYear": 1995})
    assert req.title == "Sunday pancakes"
    assert req.description == "" and req.participant_ids == [] and req.photo_ids == []
    dumped = req.model_dump(mode="json")
    assert dumped["startedYear"] == 1995 and dumped["when"]["kind"] == "weekly"


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"title": "   "}, "title"),
        ({"title": "x" * (schemas.MAX_TRADITION_TITLE_CHARS + 1)}, "title"),
        ({"description": "x" * (schemas.MAX_TRADITION_DESCRIPTION_CHARS + 1)}, "description"),
        ({"category": "sport"}, "category"),
        ({"when": {"kind": "daily"}}, "kind"),
        ({"when": {"kind": "yearly", "month": 13}}, "month"),
        ({"when": {"kind": "yearly", "month": 0}}, "month"),
        ({"when": {"kind": "yearly", "label": "x" * (schemas.MAX_TRADITION_LABEL_CHARS + 1)}}, "label"),
        ({"participantIds": [f"p{i}" for i in range(schemas.MAX_TRADITION_PEOPLE + 1)]}, "participantIds"),
        ({"photoIds": [f"m{i}" for i in range(schemas.MAX_TRADITION_PHOTOS + 1)]}, "photoIds"),
        ({"startedYear": schemas.MIN_TRADITION_YEAR - 1}, "startedYear"),
        ({"startedYear": datetime.now(UTC).year + 1}, "startedYear"),
        ({"color": "red"}, "color"),
    ],
)
def test_create_rejects_out_of_bounds(patch, field):
    with pytest.raises(ValidationError) as exc:
        CreateTraditionRequest.model_validate({**OK, **patch})
    assert field in fields_of(exc.value)


def test_create_requires_title_category_and_when():
    with pytest.raises(ValidationError) as exc:
        CreateTraditionRequest.model_validate({})
    assert fields_of(exc.value) == {"title", "category", "when"}


def test_bounds_are_inclusive():
    CreateTraditionRequest.model_validate(
        {
            **OK,
            "title": "x" * schemas.MAX_TRADITION_TITLE_CHARS,
            "description": "x" * schemas.MAX_TRADITION_DESCRIPTION_CHARS,
            "when": {"kind": "yearly", "month": 12, "label": "x" * schemas.MAX_TRADITION_LABEL_CHARS},
            "photoIds": ["m"] * schemas.MAX_TRADITION_PHOTOS,
            "startedYear": datetime.now(UTC).year,
        }
    )


def test_update_is_all_optional_and_tracks_what_was_sent():
    assert UpdateTraditionRequest.model_validate({}).model_fields_set == set()
    req = UpdateTraditionRequest.model_validate({"startedYear": None, "title": " Card night "})
    assert req.model_fields_set == {"started_year", "title"}
    assert req.title == "Card night"
    for bad in ({"title": ""}, {"category": "sport"}, {"when": {"kind": "yearly", "month": 13}}, {"startedYear": 1700}):
        with pytest.raises(ValidationError):
            UpdateTraditionRequest.model_validate(bad)


def test_tradition_round_trips():
    at = "2026-11-13T12:00:00Z"
    raw = {
        "id": "t1",
        "familyId": "f1",
        "title": "Tamales on Christmas Eve",
        "description": "",
        "category": "food",
        "when": {"kind": "yearly", "label": "Christmas Eve", "month": 12},
        "participantIds": ["rosa"],
        "photoIds": [],
        "startedYear": 1962,
        "createdBy": "u1",
        "createdAt": at,
        "updatedAt": at,
    }
    resp = TraditionsResponse.model_validate({"traditions": [raw]})
    again = TraditionsResponse.model_validate(resp.model_dump(mode="json"))
    assert again == resp
    assert isinstance(resp.traditions[0], Tradition)
    assert resp.model_dump(mode="json")["traditions"][0]["participantIds"] == ["rosa"]
