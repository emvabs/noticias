"""A shared password for when the site is reachable from outside this Mac.

The password lives in data/password.txt (data/ is not tracked by git). With no
file, or an empty one, the site is open, as it always was. Visits made directly
on this machine (http://localhost:8000) never ask: a request is local only when
it comes from the loopback address, names a local host, and carries no proxy
headers, because Tailscale Funnel also connects from the loopback address but
adds X-Forwarded-For.
"""
import base64
import binascii
import ipaddress
import secrets

from . import config

PASSWORD_FILE = config.ROOT / "data" / "password.txt"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Set by a reverse proxy (nginx, Tailscale Funnel). A request carrying one only
# looks local because the proxy runs on this machine; the visitor is elsewhere.
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded",
                 "tailscale-funnel-request")


def password():
    """The shared password, or None when the site is open."""
    try:
        value = PASSWORD_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def host_name(host_header):
    """'localhost:8000' -> 'localhost', '[::1]:8000' -> '::1'."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_loopback(host):
    """127.0.0.1, ::1, ::ffff:127.0.0.1 or "localhost"."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


def is_local(client_host, headers):
    """True for a visit made directly on this machine, not through a proxy."""
    if not is_loopback(client_host or ""):
        return False
    if any(h in headers for h in PROXY_HEADERS):
        return False
    return host_name(headers.get("host")) in LOCAL_HOSTS


def password_matches(authorization, expected):
    """Checks a Basic auth header. Any user name is accepted; only the password counts."""
    scheme, _, encoded = (authorization or "").partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, sep, given = decoded.partition(":")
    return bool(sep) and secrets.compare_digest(given.encode(), expected.encode())


def allowed(client_host, headers):
    expected = password()
    if expected is None or is_local(client_host, headers):
        return True
    return password_matches(headers.get("authorization"), expected)
