"""Every TLS connection that carries a secret is verified (F-2026-022/023/024): TrueNAS (the API key), Syncthing (the
API key) and Plex (the token) all answer, as people run them, with certificates no CA signed — TrueNAS generates its
own, Syncthing's is self-signed by design, and Plex's plex.direct certificate doesn't name a bare LAN IP — so "verify
normally" would refuse almost every real setup, and the app used to send the secrets with verification off instead.
What's here is the way out: each service's connection is verified one of two ways, the service's settings' choice.

* **Full verification** (the service's ``verify_tls``): the CA and the name, as any https client — for a real
  certificate (a reverse proxy, a CA of your own). Off by default, since the certificates above would all be refused.
* **Pinned, once confirmed** (``pin_tls``, the default): the first connection reads the certificate and stops there,
  before anything is sent (CertUnconfirmed); the System page shows its fingerprint, and once a person confirms it
  (Trust This Certificate) it's remembered — its SHA-256, in the ``tls`` settings section, by service — and every
  connection must answer with that very certificate. Nothing goes to whatever answers first, and an attacker on the
  network can't stand in for the server after the confirmation (F-2026-053). A changed certificate (TrueNAS regenerates its own now and then) is refused, the lights say so, and the
  System page's card offers **Trust New Certificate**, with the old and new fingerprints drawn as identicons
  (integrationLights), so what's being trusted can be seen before it is.

The two can't both be off: ``pin_tls`` is refused off while ``verify_tls`` is off (config: Settings.update), so a keyed
connection is never unverified. Plex has a third way, better than a pin where it applies: a server reached at a
plex.direct name — or at a bare address that a plex.direct name is known for (``direct_name``) — is fully verified for
that name, whatever address the connection goes to (Context.name sets the SNI and the check). A connection that sends
no secret (a hello, an identity probe) isn't held to any of this: there's nothing to take.
"""

import hashlib
import ipaddress
import ssl
import urllib.parse

from .i18n import M


class CertChanged(ssl.SSLCertVerificationError):
    """The service answered with a certificate other than the pinned one: refused, to be trusted by hand (the System
    page's Trust New Certificate), never silently."""


class CertUnconfirmed(CertChanged):
    """The service answered, but no certificate is pinned for it yet: refused before anything is sent, until a person
    confirms the one it answered with (the System page's Trust This Certificate, F-2026-053)."""


class Context(ssl.SSLContext):
    """An ``ssl.SSLContext`` that can verify by pin after the handshake (``pinning``), and check the certificate for a
    name other than the address reached (``name``: Plex's plex.direct). ``seen`` carries the last handshake's
    certificate fingerprint — what to pin on first use, and what was refused on a change."""

    pinning = False
    pin = ""
    seen = ""
    observe = False  # (a connection that sends no secret: its certificate read for the page, never refused for want of a pin)
    name: str | None = None

    def wrap_socket(self, sock, *args, server_hostname=None, **kw):
        if self.name:
            server_hostname = self.name
        s = super().wrap_socket(sock, *args, server_hostname=server_hostname, **kw)
        if self.pinning:
            got = hashlib.sha256(s.getpeercert(binary_form=True) or b"").hexdigest()
            self.seen = got
            if not self.pin and not self.observe:
                s.close()
                raise CertUnconfirmed(str(M("The server answered with a certificate ({new}) that hasn’t been confirmed yet: nothing was sent to it. "
                                            "Check it and trust it on the System page.", new=short(got))))
            if self.pin and got != self.pin:
                s.close()
                raise CertChanged(str(M("The server’s certificate changed: {new} answered where {old} is pinned. If the service really has "
                                        "a new certificate, trust it on the System page.", new=short(got), old=short(self.pin))))
        return s


def on_this_machine(url: str) -> bool:
    """Whether a URL's host is this machine, the only place a secret may go without TLS (host_on_this_machine)."""
    return host_on_this_machine(urllib.parse.urlsplit(url).hostname or "")


def host_on_this_machine(host: str) -> bool:
    """"localhost" or a loopback address (an address, not a name that merely starts like one)."""
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def context(verify: bool, pin: str = "", name: str | None = None) -> Context:
    """The context a service's connection is made with: full verification (``verify``, or a ``name`` to check the
    certificate for), else verification by ``pin`` — empty until a person confirms one, when every connection is refused
    and ``seen`` says what answered."""
    ctx = Context(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.name = name or None
    if verify or name:
        ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # (the pin is the verification: checked in wrap_socket)
        ctx.pinning = True
        ctx.pin = (pin or "").lower()
    return ctx


def short(fp: str) -> str:
    """A fingerprint as the pages say it (and identicon() draws it): its first three groups of four."""
    fp = (fp or "").upper()
    return "-".join(fp[i:i + 4] for i in (0, 4, 8)) if fp else ""


def changed(err: BaseException | None) -> bool:
    """Whether an error (however wrapped: URLError's ``reason``, a cause) is the pin's refusal."""
    seen = 0
    while err is not None and seen < 8:
        if isinstance(err, CertChanged):
            return True
        err = getattr(err, "reason", None) or err.__cause__ or err.__context__
        seen += 1
    return False


def fingerprint(url: str, timeout: float = 5) -> str:
    """The SHA-256 (hex) of the certificate serving ``url``, read with no secret sent: what Trust New Certificate pins,
    and the identity fallback for TrueNAS (truenas.cert_fingerprint). "" when nothing answers, or the URL isn't https."""
    import socket
    import urllib.parse
    u = urllib.parse.urlsplit(url)
    if not u.hostname or u.scheme != "https":
        return ""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((u.hostname, u.port or 443), timeout=timeout) as s, ctx.wrap_socket(s, server_hostname=u.hostname) as t:
            der = t.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError, ValueError):
        return ""
    return hashlib.sha256(der).hexdigest() if der else ""
