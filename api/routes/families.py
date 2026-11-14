"""Family routes: create a family, invite links, join, claim a person, member roles.

ARCHITECTURE.md section 4 (start a family, invite, join and claim, people without
accounts, roles) and the section 6 "Accounts and families" rows other than auth.

Access rules:
- Family-scoped routes depend on `auth.family_member` (or `auth.family_admin`), so a
  family the caller does not belong to is a 404, exactly like one that does not exist.
- Person ids are always looked up inside the path's family, so another family's person
  id is a 404 too.
- `GET /invites/{code}` is public: the code itself is the secret.

Invite answers, for `GET /invites/{code}` and `POST /invites/{code}/join`:
- an unknown code (or an empty-ish one) is a 404 INVITE_NOT_FOUND;
- a code that exists but can no longer be used is a 410 Gone, INVITE_REVOKED when an admin
  revoked it (even if it has also expired) and INVITE_EXPIRED when it is past its
  expiresAt (the boundary itself counts as expired). The detail is all a 410 says: never
  the family's name or id, so a stale link does not reveal whose it was.
- `DELETE /families/{fid}/invites/{code}` (admin revoke) stays a 404 INVITE_NOT_FOUND for
  a code that is not this family's, and revoking an already revoked or expired code is a
  204 as before.

Size limits (route level; schemas.py is unbounded), checked after trimming: a family name
MAX_FAMILY_NAME_CHARS, a person's name MAX_PERSON_NAME_CHARS, and a character sheet's
free text (signature item, notes) MAX_PERSON_TEXT_CHARS. Over a limit is a 422 whose
detail names the limit. A blank name stays the 400 it always was. A character color must
be a hex code (#rgb or #rrggbb), since the stage paints the puppet with it.

A character sheet's `itemPropId` (the signature item worn in the head socket) must name a
prop of this family or a built-in library prop, looked up with db.props.get_prop after
strip(); anything else is a 422 ITEM_PROP_UNKNOWN, whether the id is missing or belongs to
another family (the same sentence for both, so nothing leaks). null, absent, blank or
whitespace-only means "no item prop" and is stored as null. Every route that writes a
sheet checks it: creating a family (library props only, since the family has none yet),
adding a person, patching one, and joining by adding yourself, always before anything is
written.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from pymongo.database import Database

import schemas
from auth import FamilyAccess, current_user, family_admin, family_member
from db import families as families_db
from db import people as people_db
from db import props as props_db
from db.client import get_db
from db.families import LastAdmin
from db.people import AlreadyClaimed, InvalidRelation
from screenplay import compiler as screenplay_compiler
from screenplay.character import speed_scale
from screenplay.schema import Screenplay

router = APIRouter(tags=["families"])

INVITE_NOT_FOUND = "Invite not found"
INVITE_EXPIRED = "This invite has expired"
INVITE_REVOKED = "This invite was revoked"
LAST_ADMIN = "A family needs at least one admin"
PERSON_TAKEN = "Someone has already claimed this person"
ALREADY_HAVE_PERSON = "You already have a person in this family"
BAD_COLOR = "Color must be a hex code like #3f6fb5"
ITEM_PROP_UNKNOWN = "That item is not one of this family's props"
BUILD_BREAKS_SCENE = 'That build would no longer fit the saved scene "{title}". Keep the current build.'
ITEM_BREAKS_SCENE = 'That worn item would no longer fit the saved scene "{title}". Keep the current item.'
NOTES_BREAKS_SCENE = (
    'That description of how {name} moves would no longer fit the saved scene "{title}". Keep the current notes.'
)

MAX_FAMILY_NAME_CHARS = 100
MAX_PERSON_NAME_CHARS = 100
MAX_PERSON_TEXT_CHARS = 2_000

_HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
# Character sheet free text: field name and how a limit sentence names it.
_CHARACTER_TEXT = (("item", "A signature item"), ("notes", "Notes"))


# ------------------------------------------------------------------------------ helpers


def _not_found(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _gone(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_410_GONE, detail=detail)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _check_length(value: str, limit: int, what: str) -> None:
    if len(value) > limit:
        raise _unprocessable(f"{what} must be at most {limit:,} characters")


def _clean_person_name(raw: str) -> str:
    """The trimmed name. 400 if blank, 422 if over MAX_PERSON_NAME_CHARS."""
    name = raw.strip()
    if not name:
        raise _bad_request("A person needs a name")
    _check_length(name, MAX_PERSON_NAME_CHARS, "A name")
    return name


def _clean_character(character: schemas.CharacterSheet) -> schemas.CharacterSheet:
    """The sheet with a trimmed hex color, trimmed free text (blank text becomes None) and a
    trimmed itemPropId (blank becomes None). Whether that prop exists is _check_item_prop's job.

    422 for a color that is not a hex code, or free text over MAX_PERSON_TEXT_CHARS.
    """
    updates: dict[str, str | None] = {}
    if character.item_prop_id is not None:
        item_prop_id = character.item_prop_id.strip() or None
        if item_prop_id != character.item_prop_id:
            updates["item_prop_id"] = item_prop_id
    color = character.color.strip()
    if not _HEX_COLOR.fullmatch(color):
        raise _unprocessable(BAD_COLOR)
    if color != character.color:
        updates["color"] = color
    for field, what in _CHARACTER_TEXT:
        value = getattr(character, field)
        if value is None:
            continue
        trimmed = value.strip()
        _check_length(trimmed, MAX_PERSON_TEXT_CHARS, what)
        if trimmed != value:
            updates[field] = trimmed or None
    return character.model_copy(update=updates) if updates else character


def item_prop_ok(db: Database, family_id: str | None, prop_id: str) -> bool:
    """True if prop_id names a prop of this family or a built-in library prop.

    `family_id` None (a family that does not exist yet) accepts library props only.
    """
    prop_id = prop_id.strip()
    if not prop_id:
        return False
    if family_id is None:
        return prop_id in props_db.library_props()
    prop = props_db.get_prop(db, family_id, prop_id)
    return prop is not None and prop.family_id in (None, family_id)


def _check_item_prop(db: Database, family_id: str | None, character: schemas.CharacterSheet | None) -> None:
    """422 ITEM_PROP_UNKNOWN when a (cleaned) sheet names an item prop that is not this family's."""
    if character is None or character.item_prop_id is None:
        return
    if not item_prop_ok(db, family_id, character.item_prop_id):
        raise _unprocessable(ITEM_PROP_UNKNOWN)


def _clean_new_person(new: schemas.NewPerson) -> schemas.NewPerson:
    """The same person with a trimmed name and a cleaned character sheet.

    400 if the name is blank; 422 over a size limit or for a bad color.
    """
    updates: dict[str, object] = {}
    name = _clean_person_name(new.name)
    if name != new.name:
        updates["name"] = name
    if new.character is not None:
        character = _clean_character(new.character)
        if character is not new.character:
            updates["character"] = character
    return new.model_copy(update=updates) if updates else new


def _clean_patch(patch: schemas.PersonPatch) -> schemas.PersonPatch:
    """The same patch with a trimmed name and a cleaned character sheet, where it sets them.

    400 if it sets a blank name; 422 over a size limit or for a bad color.
    """
    updates: dict[str, object] = {}
    if patch.name is not None:
        name = _clean_person_name(patch.name)
        if name != patch.name:
            updates["name"] = name
    if patch.character is not None:
        character = _clean_character(patch.character)
        if character is not patch.character:
            updates["character"] = character
    return patch.model_copy(update=updates) if updates else patch


def _summary(db: Database, family: schemas.Family, user_id: str, person_id: str) -> schemas.FamilySummary:
    """The caller's view of a family they just joined, with their actual role."""
    role = families_db.get_member_role(db, family.id, user_id) or "member"
    return schemas.FamilySummary(id=family.id, name=family.name, role=role, person_id=person_id)


def _undo_join(db: Database, family_id: str, user_id: str, was_member: bool) -> None:
    """Take back a membership this request added, so a failed join leaves nothing behind.

    Not when the user is linked to a person here by now: a concurrent join by the same
    user won (often the very reason this one failed with AlreadyClaimed), and removing
    the membership would lock them out and unclaim the winner's person.
    """
    if was_member or people_db.get_person_for_user(db, family_id, user_id) is not None:
        return
    try:
        families_db.remove_member(db, family_id, user_id)
    except LastAdmin:  # only if they were promoted meanwhile; then they stay
        pass


# ----------------------------------------------------------------------------- families


@router.post("/families", status_code=status.HTTP_201_CREATED)
def create_family(
    body: schemas.CreateFamilyRequest,
    user: schemas.User = Depends(current_user),
    db: Database = Depends(get_db),
) -> schemas.CreateFamilyResponse:
    """Start a family. The caller becomes its admin and its first person."""
    name = body.name.strip()
    if not name:
        raise _bad_request("A family needs a name")
    _check_length(name, MAX_FAMILY_NAME_CHARS, "A family name")
    # The creator is the first node, so there is nobody yet to be related to. Checked
    # before anything is written, so a bad request never leaves an empty family behind.
    if body.person.parent_ids or body.person.spouse_ids:
        raise _bad_request("The first person in a new family cannot have parents or spouses yet")
    new = _clean_new_person(body.person)
    try:
        people_db.check_lifespan(new.born, new.died)
    except InvalidRelation as exc:
        raise _bad_request(str(exc)) from exc
    _check_item_prop(db, None, new.character)  # the family has no props of its own yet

    family = families_db.create_family(db, name=name, creator_user_id=user.id)
    try:
        person = people_db.create_person(db, family.id, new, added_by=user.id, user_id=user.id)
    except InvalidRelation as exc:
        # Any person check not caught above: take the new family back so nothing is left.
        families_db.purge_family(db, family.id)
        raise _bad_request(str(exc)) from exc
    return schemas.CreateFamilyResponse(
        family=schemas.FamilySummary(id=family.id, name=family.name, role="admin", person_id=person.id),
        person=person,
    )


@router.get("/families/{fid}")
def get_family(
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Family:
    """The family with its members. Invite codes are shown to admins only."""
    family = families_db.get_family(db, access.family_id)
    if family is None:
        raise _not_found()
    if not access.is_admin:
        family = family.model_copy(update={"invites": []})
    return family


# ------------------------------------------------------------------------------ invites


@router.post("/families/{fid}/invites", status_code=status.HTTP_201_CREATED)
def create_invite(
    access: FamilyAccess = Depends(family_admin),
    db: Database = Depends(get_db),
) -> schemas.InviteResponse:
    """Admin: a new invite link, valid for 14 days."""
    invite = families_db.create_invite(db, access.family_id, access.user.id)
    if invite is None:
        raise _not_found()
    return schemas.InviteResponse(code=invite.code, path=f"/join/{invite.code}", expires_at=invite.expires_at)


@router.delete("/families/{fid}/invites/{code}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(
    code: str,
    access: FamilyAccess = Depends(family_admin),
    db: Database = Depends(get_db),
) -> None:
    """Admin: revoke an invite code of this family."""
    if not families_db.revoke_invite(db, access.family_id, code):
        raise _not_found(INVITE_NOT_FOUND)


def _usable_invite_family(db: Database, code: str) -> schemas.Family:
    """The family an active invite code belongs to.

    404 INVITE_NOT_FOUND for an unknown code; 410 INVITE_REVOKED or INVITE_EXPIRED for a
    code that exists but can no longer be used (revoked wins when both apply). Reads only.
    """
    found = families_db.find_invite(db, code)
    if found is None:
        raise _not_found(INVITE_NOT_FOUND)
    family, _, state = found
    if state == "revoked":
        raise _gone(INVITE_REVOKED)
    if state == "expired":
        raise _gone(INVITE_EXPIRED)
    return family


@router.get("/invites/{code}")
def preview_invite(code: str, db: Database = Depends(get_db)) -> schemas.InvitePreview:
    """Public preview of an invite: the family name and who is left to claim.

    404 for an unknown code, 410 for an expired or revoked one (see the module docstring).
    """
    family = _usable_invite_family(db, code)
    return schemas.InvitePreview(
        family_id=family.id,
        family_name=family.name,
        unclaimed_people=people_db.unclaimed_people(db, family.id),
    )


@router.post("/invites/{code}/join")
def join_family(
    code: str,
    body: schemas.JoinRequest,
    user: schemas.User = Depends(current_user),
    db: Database = Depends(get_db),
) -> schemas.JoinResponse:
    """Join through an invite, then claim a person (`personId`) or add yourself (`person`).

    Order of answers, first match wins:
    1. 401 when signed out: `current_user` runs before anything else, so a signed-out
       caller learns nothing about the code here (the public preview tells them).
    2. 422 from FastAPI when the body does not parse as a JoinRequest (wrong types,
       unknown fields): that validation runs before this function.
    3. 404 for an unknown code, 410 for an expired or revoked one. Checked before the
       body's personId / person are looked at and before any write, so a stale link
       always explains itself, even when the body is also wrong, and a 410 writes nothing.
    4. 422 unless exactly one of personId and person is sent, then the person checks.
    """
    family = _usable_invite_family(db, code)
    person_id, new = body.person_id, body.person
    if (person_id is None) == (new is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Send exactly one of personId (claim) or person (add yourself)",
        )

    if new is not None:
        new = _clean_new_person(new)
        _check_item_prop(db, family.id, new.character)  # before the membership is added
        return _join_by_adding(db, family, user, new)
    return _join_by_claim(db, family, user, str(person_id))


def _join_by_claim(
    db: Database, family: schemas.Family, user: schemas.User, person_id: str
) -> schemas.JoinResponse:
    target = people_db.get_person(db, family.id, person_id)
    if target is None:
        raise _not_found("Person not found")
    if target.user_id is not None and target.user_id != user.id:
        raise _conflict(PERSON_TAKEN)

    was_member = families_db.get_member_role(db, family.id, user.id) is not None
    families_db.add_member(db, family.id, user.id, "member")
    try:
        person = people_db.claim_person(db, family.id, person_id, user.id)
    except AlreadyClaimed as exc:
        _undo_join(db, family.id, user.id, was_member)
        # A fixed sentence, never the db layer's text: which of the two it is depends on
        # whether the caller holds another person here (for example from a concurrent join).
        mine = people_db.get_person_for_user(db, family.id, user.id)
        raise _conflict(ALREADY_HAVE_PERSON if mine is not None and mine.id != person_id else PERSON_TAKEN) from exc
    if person is None:  # removed between the lookup and the claim
        _undo_join(db, family.id, user.id, was_member)
        raise _not_found("Person not found")
    return schemas.JoinResponse(family=_summary(db, family, user.id, person.id), person=person)


def _join_by_adding(
    db: Database, family: schemas.Family, user: schemas.User, new: schemas.NewPerson
) -> schemas.JoinResponse:
    was_member = families_db.get_member_role(db, family.id, user.id) is not None
    families_db.add_member(db, family.id, user.id, "member")
    try:
        person = people_db.create_person(db, family.id, new, added_by=user.id, user_id=user.id)
    except InvalidRelation as exc:
        _undo_join(db, family.id, user.id, was_member)
        raise _bad_request(str(exc)) from exc
    except AlreadyClaimed as exc:
        _undo_join(db, family.id, user.id, was_member)
        raise _conflict(ALREADY_HAVE_PERSON) from exc
    return schemas.JoinResponse(family=_summary(db, family, user.id, person.id), person=person)


# ------------------------------------------------------------------------------- people


@router.post("/families/{fid}/people", status_code=status.HTTP_201_CREATED)
def add_person(
    body: schemas.NewPerson,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Person:
    """Add a relative, who may never have an account. Anyone can claim them later."""
    new = _clean_new_person(body)
    _check_item_prop(db, access.family_id, new.character)
    try:
        return people_db.create_person(db, access.family_id, new, added_by=access.user.id, user_id=None)
    except InvalidRelation as exc:
        raise _bad_request(str(exc)) from exc


def _scene_inputs(
    db: Database, family_id: str, screenplay: Screenplay
) -> tuple[dict[str, schemas.SetDef], dict[str, schemas.Prop], dict[str, schemas.Person]] | None:
    """This family's sets, props and cast for a stored screenplay, or None if any is missing."""
    sets: dict[str, schemas.SetDef] = {}
    props: dict[str, schemas.Prop] = {}
    people: dict[str, schemas.Person] = {}
    for set_id in dict.fromkeys(shot.set for shot in screenplay.shots):
        set_def = props_db.get_set(db, family_id, set_id)
        if set_def is None:
            return None
        sets[set_id] = set_def
    for prop_id in dict.fromkeys(screenplay.props):
        prop = props_db.get_prop(db, family_id, prop_id)
        if prop is None:
            return None
        props[prop_id] = prop
    for cast_id in dict.fromkeys(screenplay.cast):
        person = people_db.get_person(db, family_id, cast_id)
        if person is None:
            return None
        people[cast_id] = person
    return sets, props, people


def _compiles(
    screenplay: Screenplay,
    sets: dict[str, schemas.SetDef],
    props: dict[str, schemas.Prop],
    people: dict[str, schemas.Person],
) -> bool:
    """True when the real compiler turns this screenplay into a timeline. Never raises."""
    try:
        result = screenplay_compiler.compile(screenplay, sets=sets, props=props, people=people)
    except Exception:  # noqa: BLE001 - a compiler crash is a failed compile, never a crashed request
        return False
    return bool(result.ok) and result.timeline is not None


def _guard_saved_scenes(
    db: Database, family_id: str, current: schemas.Person, sheet: schemas.CharacterSheet
) -> None:
    """409 when a scene saved with this person compiles today but not with the new sheet.

    Only called when the build, the worn item, or the walking-speed factor the compiler reads
    from the notes (screenplay.character.speed_scale) changes. The message names the cause in
    that order: a build change wins over an item change, which wins over a notes change. A
    scene that already fails to compile (or whose screenplay, sets, props or cast can no
    longer be loaded) never blocks the edit: it is broken either way, and refusing would
    leave no way to fix the person.
    """
    if sheet.build != current.character.build:
        message = BUILD_BREAKS_SCENE
    elif sheet.item_prop_id != current.character.item_prop_id:
        message = ITEM_BREAKS_SCENE
    else:
        message = NOTES_BREAKS_SCENE
    changed = current.model_copy(update={"character": sheet})
    cursor = db["memories"].find(
        {"familyId": family_id, "screenplay.cast": current.id}, {"_id": 1, "title": 1, "screenplay": 1}
    )
    for doc in cursor:
        try:
            screenplay = Screenplay.model_validate(doc.get("screenplay"))
        except ValidationError:
            continue
        loaded = _scene_inputs(db, family_id, screenplay)
        if loaded is None:
            continue
        sets, props, people = loaded
        if not _compiles(screenplay, sets, props, people):
            continue
        people[current.id] = changed
        if not _compiles(screenplay, sets, props, people):
            title = str(doc.get("title") or "a saved moment")
            raise _conflict(message.format(title=title, name=current.name))


@router.patch(
    "/families/{fid}/people/{person_id}",
    response_model=schemas.Person,
    responses={
        400: {"model": schemas.ErrorResponse, "description": "A blank name or an impossible relation"},
        404: {"model": schemas.ErrorResponse, "description": "Unknown family, or the person is not in it"},
        409: {
            "model": schemas.ErrorResponse,
            "description": (
                "The new build, worn item, or notes that change how fast this person walks would no "
                "longer fit a scene already saved with this person"
            ),
        },
    },
)
def patch_person(
    person_id: str,
    body: schemas.PersonPatch,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Person:
    """Edit a person, their relationships, or their character sheet.

    Build guard: when the patch changes the character's `build`, `itemPropId`, or the
    walking-speed factor the compiler reads from `notes` ("walks slowly with a cane" is
    0.6x, see screenplay.character.speed_scale), every scene saved in this family with this
    person in its cast is compiled again with the new sheet. If one that compiles today
    would stop compiling (a smaller build can no longer reach a hand_to, a slower walker is
    still walking when their next action starts), the patch is a 409 naming that moment and
    nothing is written, since every later telling of it would fail. Scenes that already fail
    to compile do not block the edit. A patch that keeps the build, the item prop and the
    speed factor (a cosmetic notes edit such as "loves gardening") compiles nothing.
    """
    patch = _clean_patch(body)
    _check_item_prop(db, access.family_id, patch.character)
    sheet = patch.character if "character" in patch.model_fields_set else None
    if sheet is not None:
        current = people_db.get_person(db, access.family_id, person_id)
        if current is None:
            raise _not_found("Person not found")
        if (
            sheet.build != current.character.build
            or sheet.item_prop_id != current.character.item_prop_id
            or speed_scale(sheet.notes) != speed_scale(current.character.notes)
        ):
            _guard_saved_scenes(db, access.family_id, current, sheet)
    try:
        person = people_db.update_person(db, access.family_id, person_id, patch)
    except InvalidRelation as exc:
        raise _bad_request(str(exc)) from exc
    if person is None:
        raise _not_found("Person not found")
    return person


# ------------------------------------------------------------------------------ members


@router.patch(
    "/families/{fid}/members/{user_id}",
    response_model=schemas.Family,
    responses={
        403: {"model": schemas.ErrorResponse, "description": "The caller is not an admin of this family"},
        404: {"model": schemas.ErrorResponse, "description": "Unknown family, or the user is not a member of it"},
        409: {"model": schemas.ErrorResponse, "description": "Demoting the family's last admin"},
    },
)
def set_member_role(
    user_id: str,
    body: schemas.MemberRolePatch,
    access: FamilyAccess = Depends(family_admin),
    db: Database = Depends(get_db),
) -> schemas.Family:
    """Admin: make a member an admin or a member. Returns the family, as GET does.

    Setting the role a member already has is a no-op. An admin may demote themselves
    while another admin remains; the response is then the member's view (no invites).
    """
    try:
        changed = families_db.set_member_role(db, access.family_id, user_id, body.role)
    except LastAdmin as exc:
        raise _conflict(LAST_ADMIN) from exc
    if not changed:
        raise _not_found("Member not found")
    family = families_db.get_family(db, access.family_id)
    if family is None:
        raise _not_found()
    if families_db.get_member_role(db, access.family_id, access.user.id) != "admin":
        family = family.model_copy(update={"invites": []})
    return family


@router.delete("/families/{fid}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: str,
    access: FamilyAccess = Depends(family_admin),
    db: Database = Depends(get_db),
) -> None:
    """Admin: remove a member. Their person stays in the tree, unclaimed."""
    try:
        removed = families_db.remove_member(db, access.family_id, user_id)
    except LastAdmin as exc:
        raise _conflict(LAST_ADMIN) from exc
    if not removed:
        raise _not_found("Member not found")
