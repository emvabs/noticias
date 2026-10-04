import pytest

from app.mixing import mix


def pool(prefix, n, start_hour=0):
    """n articles, newest first, with distinct ISO timestamps."""
    return [{"id": f"{prefix}{i}", "published_at": f"2026-09-21T{23 - start_hour - i:02d}:00:00Z"}
            for i in range(n)]


def ids(articles, prefix):
    return [a["id"] for a in articles if a["id"].startswith(prefix)]


def test_exact_split():
    arts, s = mix(pool("p", 20), pool("n", 20), 60, 10)
    assert s["positive_count"] == 6 and s["non_positive_count"] == 4
    assert s["actual_pct"] == 60 and not s["shortfall"]
    assert s["limited_by"] is None


def test_takes_most_recent_from_each_pool():
    arts, _ = mix(pool("p", 20), pool("n", 20), 50, 6)
    assert ids(arts, "p") == ["p0", "p1", "p2"]
    assert ids(arts, "n") == ["n0", "n1", "n2"]


def test_result_sorted_by_publish_time():
    arts, _ = mix(pool("p", 10, start_hour=1), pool("n", 10), 50, 10)
    times = [a["published_at"] for a in arts]
    assert times == sorted(times, reverse=True)


@pytest.mark.parametrize("pct,expected_pos", [(0, 0), (100, 50), (25, 13), (5, 3), (35, 18)])
def test_rounding_half_up(pct, expected_pos):
    _, s = mix(pool("p", 60), pool("n", 60), pct, 50)
    assert s["positive_count"] == expected_pos
    assert s["count"] == 50


def test_a_short_positive_pool_shortens_the_feed_instead_of_padding_it():
    """Asking for 80% positive must not hand back a feed that is 70% negative."""
    arts, s = mix(pool("p", 3), pool("n", 20), 80, 10)
    assert s["positive_count"] == 3          # everything the pool had
    assert s["non_positive_count"] == 1      # and only its 20% share
    assert s["count"] == 4 and s["count"] < s["limit"]
    assert s["actual_pct"] == 75             # the requested share, give or take rounding
    assert s["shortfall"] and s["limited_by"] == "positive"


def test_a_short_non_positive_pool_shortens_the_feed_too():
    """Asking for 20% positive with only 2 non-positive articles available."""
    arts, s = mix(pool("p", 20), pool("n", 2), 20, 10)
    assert s["non_positive_count"] == 2                 # everything that side had
    assert s["count"] < s["limit"] and s["limited_by"] == "non_positive"
    assert s["actual_pct"] <= 40                        # still mostly non-positive


def test_both_pools_short_keeps_the_requested_share():
    arts, s = mix(pool("p", 2), pool("n", 3), 50, 10)
    assert (s["positive_count"], s["non_positive_count"]) == (2, 2)
    assert s["count"] == 4 and s["actual_pct"] == 50


def test_hundred_percent_shows_only_positive_news():
    """The case that started this: all the way right means all the way right."""
    arts, s = mix(pool("p", 44), pool("n", 150), 100, 100)
    assert s["count"] == 44 and s["actual_pct"] == 100
    assert s["non_positive_count"] == 0 and s["limited_by"] == "positive"


def test_empty_positive_pool_at_100_pct_shows_nothing():
    arts, s = mix([], pool("n", 5), 100, 10)
    assert arts == [] and s["count"] == 0 and s["shortfall"]
    assert s["limited_by"] == "positive"


def test_zero_percent_shows_no_positive_news():
    arts, s = mix(pool("p", 20), pool("n", 20), 0, 10)
    assert s["positive_count"] == 0 and s["count"] == 10


def test_empty_everything():
    arts, s = mix([], [], 50, 10)
    assert arts == [] and s["count"] == 0 and s["actual_pct"] == 0 and not s["shortfall"]


def test_pct_is_clamped():
    _, s = mix(pool("p", 10), pool("n", 10), 150, 10)
    assert s["requested_pct"] == 100 and s["positive_count"] == 10
    _, s = mix(pool("p", 10), pool("n", 10), -5, 10)
    assert s["positive_count"] == 0


# ---------- one item per story ----------
from app.mixing import collapse, lead  # noqa: E402


def story(id_, cluster, source, hour, label="positive"):
    return {"id": id_, "cluster_id": cluster, "source": source, "label": label,
            "published_at": f"2026-09-21T{hour:02d}:00:00Z"}


def test_collapse_keeps_the_newest_per_story():
    side = [story(3, 1, "rtp", 12), story(2, 1, "publico", 11), story(5, 5, "dn", 10), story(1, 1, "cnn", 9)]
    out = collapse(side)
    assert [a["id"] for a in out] == [3, 5]
    assert [a["id"] for a in out[0]["also"]] == [2, 1]


def test_articles_without_cluster_stand_alone():
    out = collapse([{"id": 1, "published_at": "x"}, {"id": 2, "published_at": "x"}])
    assert len(out) == 2


def test_the_share_counts_stories_and_also_never_crosses_sides():
    """Five positive versions of one story are one positive item; the negative
    version of that same story stays on its own side and is never attached."""
    positive = collapse([story(10 + i, 1, f"o{i}", 20 - i) for i in range(5)]
                        + [story(20 + i, 20 + i, "o9", 10 - i) for i in range(4)])
    negative = collapse([story(30, 1, "o7", 19, "negative")]
                        + [story(40 + i, 40 + i, "o8", 15 - i, "negative") for i in range(10)])
    arts, s = mix(positive, negative, 50, 10)
    assert s["positive_count"] == 5 and s["non_positive_count"] == 5
    for a in arts:
        assert all(o["label"] == a["label"] for o in a["also"])
    arts, s = mix(positive, negative, 100, 10)
    assert s["count"] == 5 and all(a["label"] == "positive" for a in arts)


def test_lead_is_the_story_with_most_outlets():
    arts = collapse([story(1, 1, "rtp", 12), story(2, 1, "dn", 11), story(3, 3, "cnn", 13),
                     story(4, 3, "rtp", 10), story(5, 3, "dn", 9)])
    assert lead(arts)["id"] == 3
    assert lead(collapse([story(1, 1, "rtp", 12)])) is None     # a story told once is no lead
