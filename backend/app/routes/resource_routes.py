# route: resource_routes | tr: konu bazlı dış kaynak (youtube, web) link keşfi http endpoint'i — main.py'de /study prefix ile bağlanır / en: topic-based external resource link discovery endpoint mounted at /study prefix in main.py

from typing import Annotated, Optional

from fastapi import APIRouter, Header, Query

from app.deps.auth import require_user_id
from app.services.resource_discovery_service import discover_best_links

router = APIRouter()


# fn: get_best_resources | tr: GET /study/best-resources — konu için video ve web kaynak linkleri / en: GET best video and web resource links for topic
@router.get("/best-resources")
def get_best_resources(
    topic: str = Query(default="", description="Study topic"),
    locale: str = Query(default="en", description="en | tr"),
    authorization: Annotated[Optional[str], Header()] = None,
):
    require_user_id(authorization)
    return discover_best_links(topic=topic, locale=locale)
