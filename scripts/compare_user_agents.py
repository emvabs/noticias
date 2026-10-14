"""Fetch every enabled feed with two User-Agents and compare what comes back.

Usage:  python scripts/compare_user_agents.py [--old UA] [--new UA]

Defaults compare the browser-like string the app used to send with the honest
one. For sources with author_from: page it also fetches one article page, since
the byline lookup needs those. A source that gets less with the new UA should
keep a browser-like ``user_agent:`` of its own in sources.yaml.
"""
import argparse
import sys
from pathlib import Path

import feedparser
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, profiles  # noqa: E402

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36"
              " (KHTML, like Gecko) Chrome/126 Safari/537.36")
HONEST_UA = "Noticias/1.0 (+https://github.com/emvabs/noticias)"


def get(client, url, ua):
    """(status, response or None); status is the exception name when the request failed."""
    try:
        resp = client.get(url, headers={"User-Agent": ua})
        return resp.status_code, resp
    except httpx.HTTPError as ex:
        return type(ex).__name__, None


def feed_items(client, url, ua):
    status, resp = get(client, url, ua)
    items = len(feedparser.parse(resp.content).entries) if resp is not None and resp.is_success else 0
    return status, items, resp


def page_bylines(client, url, ua):
    status, resp = get(client, url, ua)
    names = profiles.page_authors(resp.text) if resp is not None and resp.is_success else []
    return status, len(names)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", default=BROWSER_UA)
    parser.add_argument("--new", default=HONEST_UA)
    args = parser.parse_args()
    timeout = config.load_config()["fetch"].get("timeout_seconds", 20)
    worse, unknown = [], []
    print(f"old: {args.old}\nnew: {args.new}\n")
    print(f"{'source':<16} {'what':<6} {'old':>12} {'new':>12}  url")
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for src in config.load_sources():
            for url in src["feeds"]:
                old_status, old_n, old_resp = feed_items(client, url, args.old)
                new_status, new_n, _ = feed_items(client, url, args.new)
                flag = "  <-- fewer with new UA" if new_n < old_n else ""
                if not old_n and not flag:
                    unknown.append(src["id"])
                    flag = "  <-- nothing with either UA: not compared"
                print(f"{src['id']:<16} {'feed':<6} {f'{old_status}/{old_n}':>12}"
                      f" {f'{new_status}/{new_n}':>12}  {url}{flag}")
                if "fewer" in flag:
                    worse.append(src["id"])
                if src.get("author_from") != "page" or old_resp is None or not old_n:
                    continue
                page = feedparser.parse(old_resp.content).entries[0].get("link")
                if not page:
                    continue
                old_status, old_n = page_bylines(client, page, args.old)
                new_status, new_n = page_bylines(client, page, args.new)
                flag = "  <-- fewer with new UA" if new_n < old_n or (
                    old_status == 200 and new_status != 200) else ""
                print(f"{src['id']:<16} {'page':<6} {f'{old_status}/{old_n}':>12}"
                      f" {f'{new_status}/{new_n}':>12}  {page}{flag}")
                if flag:
                    worse.append(src["id"])
    print("\nstatus/items for feeds, status/bylines found for pages")
    if unknown:
        print(f"\nNot compared (no items with the old UA either): {', '.join(unknown)}")
    if not worse:
        print("No source that answered gets less with the new User-Agent.")
        return 2 if unknown else 0
    print("\nThese sources get less with the new User-Agent. In sources.yaml, add to each:")
    for ident in dict.fromkeys(worse):
        print(f"  - id: {ident}\n    user_agent: \"{args.old}\"")
    return 1


if __name__ == "__main__":
    sys.exit(main())
