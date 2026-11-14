"""Family routes: create a family, invite links, join, claim a person, member roles.

ARCHITECTURE.md section 4 (start a family, invite, join and claim, people without
accounts, roles) and the section 6 "Accounts and families" rows other than auth.

Access rules:
- Family-scoped routes depend on `auth.family_member` (or `auth.family_admin`), so a
  family the caller does not belong to is a 404, exactly like one that does not exist.
- Person ids are always looked up inside the path's family, so another family's person
  id is a 404 too.
- `GET /invites/{code}` is public: the code itself is the secret.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

import schemas
from auth import FamilyAccess, current_user, family_admin, family_member
from db import families as families_db
from db import people as people_db
from db.client import get_db
from db.families import LastAdmin
from db.people import AlreadyClaimed, InvalidRelation

router = APIRouter(tags=["families"])

INVITE_NOT_FOUND = "Invite not found or expired"
LAST_ADMIN = "A family needs at least one admin"


# ------------------------------------------------------------------------------ helpers


def _not_found(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _clean_new_person(new: schemas.NewPerson) -> schemas.NewPerson:
    """The same person with a trimmed name. 400 if the name is blank."""
    name = new.name.strip()
    if not name:
        raise _bad_request("A person needs a name")
    return new if name == new.name else new.model_copy(update={"name": name})


def _clean_patch(patch: schemas.PersonPatch) -> schemas.PersonPatch:
    """The same patch with a trimmed name. 400 if it sets a blank name."""
    if patch.name is None:
        return patch
    name = patch.name.strip()
    if not name:
        raise _bad_request("A person needs a name")
    return patch if name == patch.name else patch.model_copy(update={"name": name})


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
    # The creator is the first node, so there is nobody yet to be related to. Checked
    # before anything is written, so a bad request never leaves an empty family behind.
    if body.person.parent_ids or body.person.spouse_ids:
        raise _bad_request("The first person in a new family cannot have parents or spouses yet")
    new = _clean_new_person(body.person)
    try:
        people_db.check_lifespan(new.born, new.died)
    except InvalidRelation as exc:
        raise _bad_request(str(exc)) from exc

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


@router.get("/invites/{code}")
def preview_invite(code: str, db: Database = Depends(get_db)) -> schemas.InvitePreview:
    """Public preview of an invite: the family name and who is left to claim."""
    found = families_db.find_active_invite(db, code)
    if found is None:
        raise _not_found(INVITE_NOT_FOUND)
    family, _ = found
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
    """Join through an invite, then claim a person (`personId`) or add yourself (`person`)."""
    person_id, new = body.person_id, body.person
    if (person_id is None) == (new is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Send exactly one of personId (claim) or person (add yourself)",
        )
    found = families_db.find_active_invite(db, code)
    if found is None:
        raise _not_found(INVITE_NOT_FOUND)
    family, _ = found

    if new is not None:
        return _join_by_adding(db, family, user, _clean_new_person(new))
    return _join_by_claim(db, family, user, str(person_id))


def _join_by_claim(
    db: Database, family: schemas.Family, user: schemas.User, person_id: str
) -> schemas.JoinResponse:
    target = people_db.get_person(db, family.id, person_id)
    if target is None:
        raise _not_found("Person not found")
    if target.user_id is not None and target.user_id != user.id:
        raise _conflict("Someone has already claimed this person")

    was_member = families_db.get_member_role(db, family.id, user.id) is not None
    families_db.add_member(db, family.id, user.id, "member")
    try:
        person = people_db.claim_person(db, family.id, person_id, user.id)
    except AlreadyClaimed as exc:
        _undo_join(db, family.id, user.id, was_member)
        raise _conflict(str(exc)) from exc
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
        raise _conflict("You already have a person in this family") from exc
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
    try:
        return people_db.create_person(db, access.family_id, new, added_by=access.user.id, user_id=None)
    except InvalidRelation as exc:
        raise _bad_request(str(exc)) from exc


@router.patch("/families/{fid}/people/{person_id}")
def patch_person(
    person_id: str,
    body: schemas.PersonPatch,
    access: FamilyAccess = Depends(family_member),
    db: Database = Depends(get_db),
) -> schemas.Person:
    """Edit a person, their relationships, or their character sheet."""
    patch = _clean_patch(body)
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
