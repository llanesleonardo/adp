RANK = {"VIEWER": 1, "EDITOR": 2, "EDITOR_PLUS": 3, "SUPER_ADMIN": 4}


def at_least(user, role: str) -> bool:
    return RANK.get(user.role, 0) >= RANK[role]


def can_edit(user) -> bool:
    return at_least(user, "EDITOR")


def can_ingest(user) -> bool:
    return at_least(user, "EDITOR_PLUS")


def can_approve(user) -> bool:
    return user.role == "SUPER_ADMIN"


def can_admin(user) -> bool:
    return user.role == "SUPER_ADMIN"


def can_create_campaign(user) -> bool:
    return user.role in {"EDITOR_PLUS", "SUPER_ADMIN"}


def can_send_email(user) -> bool:
    if user.role == "SUPER_ADMIN":
        return True
    return user.role == "EDITOR_PLUS" and bool(user.can_send_public_email)


def can_read_document(user, document) -> bool:
    if document.status == "published":
        return True
    return can_edit(user)


def permission_map(user) -> dict:
    return {
        "edit": can_edit(user),
        "ingest": can_ingest(user),
        "approve": can_approve(user),
        "admin": can_admin(user),
        "draft_email": can_create_campaign(user),
        "send_email": can_send_email(user),
    }
