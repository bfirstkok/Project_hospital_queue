from copy import deepcopy
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.urls import reverse
from django.utils import timezone

from .role_guides import ROLE_GUIDES


STAFF_GUIDE_VISIT_KEY = "staff_guide_visit"


def staff_guide_context(request, role):
    """Scope the guide to this account, effective role and Bangkok calendar day."""
    guide = ROLE_GUIDES.get(role) if request.user.is_authenticated else None
    if not guide:
        return {"staff_role_guide": None, "staff_role_guide_config": None}

    guide = deepcopy(guide)
    for section in guide["sections"]:
        for link in section.get("links", []):
            link["url"] = reverse(link["url_name"])
            if link.get("query"):
                link["url"] += "?" + urlencode(link["query"])

    # This random marker only identifies a guide visit; it is not a session key.
    visit = request.session.get(STAFF_GUIDE_VISIT_KEY)
    if not visit:
        visit = uuid4().hex
        request.session[STAFF_GUIDE_VISIT_KEY] = visit
    return {
        "staff_role_guide": guide,
        "staff_role_guide_config": {
            "accountId": str(request.user.pk),
            "role": role,
            "day": timezone.localdate(timezone.now(), ZoneInfo("Asia/Bangkok")).isoformat(),
            "visit": visit,
        },
    }
