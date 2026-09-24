"""Slider mixing: choose articles with the requested share of positive news.

The share is always honoured. When one side runs short, the feed gets shorter
rather than being padded with articles from the other side: asking for 100%
positive shows only positive news, however few, instead of quietly filling the
rest of the page with the negative news you just said you did not want.
"""
import math


def round_half_up(x):
    return int(math.floor(x + 0.5))


def mix(positive, non_positive, positive_pct, limit):
    """Pick articles from two pools, each sorted newest first.

    Returns (articles sorted newest first, stats dict). `count` can be smaller
    than `limit`: `limited_by` then says which side ran out.
    """
    positive_pct = max(0.0, min(100.0, float(positive_pct)))
    limit = max(0, int(limit))
    share = positive_pct / 100

    # The biggest feed that still respects the share: walk down from the
    # requested size until both sides can supply their part of it.
    take_pos = take_non = 0
    for total in range(limit, 0, -1):
        wants_pos = round_half_up(total * share)
        if wants_pos <= len(positive) and total - wants_pos <= len(non_positive):
            take_pos, take_non = wants_pos, total - wants_pos
            break

    chosen = positive[:take_pos] + non_positive[:take_non]
    chosen.sort(key=lambda a: a["published_at"], reverse=True)
    count = len(chosen)

    limited_by = None
    if count < limit:
        # Which side could not supply its share of a full page?
        wants_pos = round_half_up(limit * share)
        if share > 0 and len(positive) < wants_pos:
            limited_by = "positive"
        elif share < 1 and len(non_positive) < limit - wants_pos:
            limited_by = "non_positive"

    return chosen, {
        "requested_pct": positive_pct,
        "actual_pct": round(100 * take_pos / count, 1) if count else 0.0,
        "limit": limit,
        "count": count,
        "positive_count": take_pos,
        "non_positive_count": take_non,
        "available_positive": len(positive),
        "available_non_positive": len(non_positive),
        # Nothing to mix is not a shortfall; it is an empty database.
        "shortfall": count < limit and bool(positive or non_positive),
        "limited_by": limited_by,
    }
