"""
Engagement metrics — likes, comments, shares and views for a published post.

Each fetcher reads public counters with the token Postly already holds for
that platform. A counter the platform does not expose to this app comes back
as None (shown as "—"), and a fetch that fails entirely returns a short
`note` instead of raising, so one platform never blocks the others.

  Facebook   reactions, comments, shares      (pages_read_engagement)
  Instagram  likes, comments; shares/views via insights when permitted
  YouTube    likes, comments, views           (no share count in the API)
  LinkedIn   likes, comments via socialActions — needs r_member_social,
             which LinkedIn only grants to approved partners
  X          posted through the share intent, so there is no post id
"""
import logging

import httpx

logger = logging.getLogger(__name__)

GRAPH_FB = "https://graph.facebook.com/v21.0"
GRAPH_IG = "https://graph.instagram.com/v21.0"


def _empty(note: str | None = None) -> dict:
    return {"likes": None, "comments": None, "shares": None, "views": None, "note": note}


async def _facebook(client: httpx.AsyncClient, token: str, post_id: str) -> dict:
    counts = "reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)"
    r = await client.get(f"{GRAPH_FB}/{post_id}", params={"fields": counts + ",shares", "access_token": token})
    has_shares = r.status_code == 200
    if not has_shares:   # videos and reels have no shares field
        r = await client.get(f"{GRAPH_FB}/{post_id}", params={"fields": counts, "access_token": token})
    if r.status_code != 200:
        return _empty(f"Facebook: {r.json().get('error', {}).get('message', r.text)[:160]}")
    d = r.json()
    return {
        "likes":    d.get("reactions", {}).get("summary", {}).get("total_count", 0),
        "comments": d.get("comments", {}).get("summary", {}).get("total_count", 0),
        # Facebook leaves shares out when there are none.
        "shares":   d.get("shares", {}).get("count", 0) if has_shares else None,
        "views":    None,
        "note":     None,
    }


async def _instagram(client: httpx.AsyncClient, token: str, media_id: str) -> dict:
    r = await client.get(f"{GRAPH_IG}/{media_id}", params={
        "fields": "like_count,comments_count", "access_token": token,
    })
    if r.status_code != 200:
        return _empty(f"Instagram: {r.json().get('error', {}).get('message', r.text)[:160]}")
    d = r.json()
    out = {"likes": d.get("like_count"), "comments": d.get("comments_count"),
           "shares": None, "views": None, "note": None}

    # Shares and views live in insights, which needs instagram_business_manage_insights.
    ir = await client.get(f"{GRAPH_IG}/{media_id}/insights", params={
        "metric": "shares,views", "access_token": token,
    })
    if ir.status_code == 200:
        for m in ir.json().get("data", []):
            value = (m.get("values") or [{}])[0].get("value", m.get("total_value", {}).get("value"))
            if m.get("name") in ("shares", "views"):
                out[m["name"]] = value
    else:
        out["note"] = "Shares/views need the Instagram insights permission."
    return out


async def _youtube(client: httpx.AsyncClient, token: str, video_id: str) -> dict:
    r = await client.get("https://www.googleapis.com/youtube/v3/videos", params={
        "part": "statistics", "id": video_id,
    }, headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        return _empty(f"YouTube: {r.json().get('error', {}).get('message', r.text)[:160]}")
    items = r.json().get("items", [])
    if not items:
        return _empty("YouTube: video not found (still processing or deleted).")
    s = items[0].get("statistics", {})
    num = lambda k: int(s[k]) if k in s else None   # hidden counts are omitted
    return {"likes": num("likeCount"), "comments": num("commentCount"),
            "shares": None, "views": num("viewCount"), "note": None}


async def _linkedin(client: httpx.AsyncClient, token: str, urn: str) -> dict:
    from urllib.parse import quote
    r = await client.get(f"https://api.linkedin.com/v2/socialActions/{quote(urn, safe='')}",
                         headers={"Authorization": f"Bearer {token}", "X-Restli-Protocol-Version": "2.0.0"})
    if r.status_code != 200:
        return _empty("LinkedIn only shares post stats with approved partner apps.")
    d = r.json()
    return {
        "likes":    d.get("likesSummary", {}).get("totalLikes"),
        "comments": d.get("commentsSummary", {}).get("aggregatedTotalComments"),
        "shares":   None,
        "views":    None,
        "note":     None,
    }


FETCHERS = {
    "facebook":  _facebook,
    "instagram": _instagram,
    "youtube":   _youtube,
    "linkedin":  _linkedin,
}


async def fetch_metrics(platform: str, access_token: str, external_post_id: str) -> dict:
    fetcher = FETCHERS.get(platform)
    if not fetcher:
        return _empty("Stats are not available for this platform.")
    if not external_post_id:
        return _empty("No post id was saved for this post.")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            return await fetcher(client, access_token, external_post_id)
    except Exception as e:
        logger.error("[%s] metrics fetch failed: %s", platform, e)
        return _empty(f"Could not reach {platform}.")
