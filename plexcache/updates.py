"""Is there a newer version? Asks GitHub every few hours (can be switched off in System), and when
you press Check Now — and a few seconds after the app starts.

Follows the channel this build is from (plexcache.CHANNEL), and only ever points at a build that
exists: GitHub is asked for the newest finished build of the branch (its workflow runs, the last
successful one), and the version number is read from plexcache/__init__.py at that very commit —
so a push that is still building, or failed, isn't offered. A dev build also compares the commit,
the number being the same between releases. Nothing is sent but ordinary downloads.
"""

import base64
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

from . import CHANNEL, COMMIT, REPO_URL, __version__
from .i18n import M

log = logging.getLogger("plexcache")
RAW = REPO_URL.replace("https://github.com/", "https://raw.githubusercontent.com/")
API = REPO_URL.replace("https://github.com/", "https://api.github.com/repos/")
BRANCH = {"stable": "main", "dev": "dev"}
SOURCE = f"{RAW}/main/plexcache/__init__.py"
EVERY = 6 * 3600
RETRY = 3600
# The image Update Now has TrueNAS pull, and the tag each channel's install runs (compose.yaml; dev/compose.dev.yaml).
OWNER = REPO_URL.split("/")[3].lower()
IMAGE = f"{OWNER}/plex-cache"
IMAGE_TAG = {"stable": "latest", "dev": "dev"}
REGISTRY = "https://ghcr.io"
# Update Now's checked digest, kept until the restarted app compares it with the image TrueNAS holds for the tag (F-2026-050:
# TrueNAS pulls by tag, so the tag could have moved between the check and the pull)
PULLED = "update-pulled.json"
PULLED_FOR = 24 * 3600
PROVENANCE = "https://slsa.dev/provenance/v1"
MANIFEST_TYPES = ", ".join(("application/vnd.oci.image.index.v1+json", "application/vnd.oci.image.manifest.v1+json",
                            "application/vnd.docker.distribution.manifest.list.v2+json", "application/vnd.docker.distribution.manifest.v2+json"))


class NotOurBuild(ValueError):
    """The image a tag points at isn't one this project's own GitHub build made (or that couldn't be checked)."""


def source_for(channel: str, sha: str | None = None) -> str:
    """The version file, on the branch — or at one commit (the newest built one)."""
    return f"{RAW}/{sha or BRANCH.get(channel, 'main')}/plexcache/__init__.py"


def runs_source_for(channel: str) -> str:
    """Where the branch's newest finished build is read from: its last successful workflow run."""
    return f"{API}/actions/runs?branch={BRANCH.get(channel, 'main')}&status=success&event=push&per_page=1"


def parse_version(text: str) -> str | None:
    m = re.search(r'^__version__\s*=\s*["\']([0-9][0-9A-Za-z.+-]*)["\']', text, re.M)
    return m.group(1) if m else None


def newer(a: str, b: str) -> bool:
    """Is version a newer than b? (0.14.0 > 0.13.2; a pre-release comes before its release:
    0.16.0-beta.1 < 0.16.0-beta.2 < 0.16.0; anything unparseable isn't newer)."""
    def key(v):
        core, _, pre = v.split("+")[0].partition("-")
        nums = [int(x) for x in re.findall(r"\d+", core)[:4]]
        nums += [0] * (4 - len(nums))
        # a release sorts after every pre-release of it
        return (tuple(nums), (1,) if not pre else (0, *[int(x) for x in re.findall(r"\d+", pre)]))
    try:
        return key(a) > key(b)
    except ValueError:
        return False


class UpdateChecker:
    def __init__(self, app, source: str | None = None, channel: str | None = None, runs_source: str | None = None, commit: str | None = None):
        self.app = app
        self.channel = channel or CHANNEL
        self.source = source or os.environ.get("PLEXCACHE_UPDATE_SOURCE") or source_for(self.channel)
        # the commit this build is from, and where the branch's newest finished build is read: by default
        # from GitHub, unless the source was given by hand (tests) without a runs source
        self.commit = COMMIT if commit is None else commit
        self.runs_source = runs_source if runs_source is not None else (runs_source_for(self.channel) if source is None and not os.environ.get("PLEXCACHE_UPDATE_SOURCE") else None)
        self.latest: str | None = None  # newest version seen, if newer than this one; else this one
        self.latest_commit: str | None = None  # the branch's newest commit (dev builds)
        self.checked: float | None = None
        self.error: str | None = None
        self.checking = False
        self.updating: float | None = None  # when Update Now was asked (TrueNAS pulls and restarts: this process ends)
        self.wake = threading.Event()
        self.first_delay = 5  # a check soon after every start, once startup has settled
        self.started = time.time()  # (pulled_check compares only in a process started after Update Now's ask)

    def start(self):
        threading.Thread(target=self._loop, name="updates", daemon=True).start()

    def _loop(self):
        self.app.stopping.wait(self.first_delay)
        try:
            self.pulled_check()
        except Exception:  # only a warning's chance lost: the update itself is done
            log.exception("comparing the pulled image")
        while not self.app.stopping.is_set():
            wait = EVERY
            if self.app.settings.section("general").get("update_check", True):
                wait = EVERY if self.check() else RETRY
            self.wake.wait(wait)
            self.wake.clear()

    def check_async(self) -> bool:
        """Check Now: a check in the background (one at a time)."""
        if self.checking:
            return False
        self.checking = True

        def work():
            try:
                self.check()
            finally:
                self.checking = False
        threading.Thread(target=work, name="updates-now", daemon=True).start()
        return True

    def _fetch(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": f"plex-cache/{self.app.version}", "Accept": "application/vnd.github+json, text/plain;q=0.9, */*;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read(256 * 1024)
        except urllib.error.HTTPError as e:  # (its response closed: an error left open warns when it's dropped)
            e.close()
            raise

    def check(self) -> bool:
        try:
            head, source = None, self.source
            if self.runs_source:  # the newest finished build: the version is read at that commit, so what's offered exists
                data = json.loads(self._fetch(self.runs_source).decode("utf-8", "replace"))
                runs = (data or {}).get("workflow_runs") or []
                head = str((runs[0].get("head_sha") if runs else "") or "")[:40] or None
                if head and source == source_for(self.channel):
                    source = source_for(self.channel, head)
            found = parse_version(self._fetch(source).decode("utf-8", "replace"))
            if not found:
                raise ValueError("no version number in the file")
        except (OSError, ValueError) as e:
            self.error = str(getattr(e, "reason", None) or e)
            log.info("update check failed: %s", self.error)
            return False
        before = (self.latest, self.latest_commit)
        mine = __version__
        self.latest = found if newer(found, mine) else mine
        self.latest_commit = head
        self.checked, self.error = time.time(), None
        if self.available() and before != (self.latest, self.latest_commit):
            if self.latest != mine:
                self.app.activity.event("update", M("Dionysus {latest} is available (this is {current})", latest=self.latest, current=self.app.version))
            else:
                self.app.activity.event("update", M("A newer dev build is available: dev is at {head} (this is {mine})", head=(head or "?")[:7], mine=(self.commit or "?")[:7]))
        if getattr(self.app, "ha", None):
            self.app.ha.wake.set()
        return True

    def _get(self, url: str, headers: dict | None = None, method: str = "GET", limit: int = 4 * 1024 * 1024):
        """(the answer's headers, its body): a GET or HEAD with its own headers, the body read up to ``limit``."""
        req = urllib.request.Request(url, method=method, headers={"User-Agent": f"plex-cache/{self.app.version}", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.headers, (resp.read(limit) if method != "HEAD" else b"")
        except urllib.error.HTTPError as e:
            e.close()
            raise

    def image_digest(self, tag: str) -> str:
        """The digest ("sha256:…") the registry serves for the image at ``tag`` now: an anonymous pull token, then the
        manifest's own digest (a HEAD: nothing pulled)."""
        _, body = self._get(f"{REGISTRY}/token?scope=repository:{IMAGE}:pull&service=ghcr.io")
        token = (json.loads(body or b"{}") or {}).get("token") or ""
        if not token:
            raise NotOurBuild(M("GitHub’s registry gave no pull token for {image}.", image=IMAGE))
        headers, _ = self._get(f"{REGISTRY}/v2/{IMAGE}/manifests/{tag}", {"Authorization": f"Bearer {token}", "Accept": MANIFEST_TYPES}, "HEAD")
        digest = str(headers.get("Docker-Content-Digest") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise NotOurBuild(M("GitHub’s registry didn’t say which image {image}:{tag} is.", image=IMAGE, tag=tag))
        return digest

    def attested(self, digest: str) -> bool:
        """Whether this project's own GitHub build attested the image ``digest``: the repository's attestations (only its
        workflows, with the attestations permission, can add one) list a build provenance whose subject is that digest and
        whose workflow is this repository's. Read from GitHub's API, over https with its certificate verified."""
        _, body = self._get(f"{API}/attestations/{digest}", {"Accept": "application/vnd.github+json"})
        for att in (json.loads(body or b"{}") or {}).get("attestations") or []:
            env = ((att or {}).get("bundle") or {}).get("dsseEnvelope") or {}
            try:
                stmt = json.loads(base64.b64decode(env.get("payload") or ""))
            except (ValueError, TypeError):
                continue
            if stmt.get("predicateType") != PROVENANCE:
                continue
            subjects = {"sha256:" + str((s.get("digest") or {}).get("sha256") or "") for s in stmt.get("subject") or [] if isinstance(s, dict)}
            workflow = (((stmt.get("predicate") or {}).get("buildDefinition") or {}).get("externalParameters") or {}).get("workflow") or {}
            if digest in subjects and str(workflow.get("repository") or "").lower() == REPO_URL.lower():
                return True
        return False

    def verify_image(self) -> str:
        """Before Update Now has TrueNAS pull: the image this channel's tag points at now is one this project's GitHub build
        made and attested (F-2026-043) — else NotOurBuild, and nothing is pulled. The digest checked."""
        tag = IMAGE_TAG.get(self.channel, "latest")
        try:
            digest = self.image_digest(tag)
            ok = self.attested(digest)
        except NotOurBuild:
            raise
        except (OSError, ValueError) as e:
            raise NotOurBuild(M("The image couldn’t be checked against GitHub’s build, so it isn’t pulled: {why}",
                                why=str(getattr(e, "reason", None) or e))) from None
        if not ok:
            raise NotOurBuild(M("The image at {image}:{tag} ({digest}) isn’t one this project’s GitHub build made: not pulled.",
                                image=IMAGE, tag=tag, digest=digest[:19]))
        return digest

    def pulled(self, digest: str) -> None:
        """Update Now asked TrueNAS to pull this channel's tag after checking ``digest``: kept for pulled_check."""
        self.app._save_json(PULLED, {"digest": digest, "tag": IMAGE_TAG.get(self.channel, "latest"), "at": time.time()})

    def pulled_check(self) -> bool | None:
        """After the restart Update Now brought: whether the image TrueNAS holds for the tag (app.image.query's repo_digests)
        is the one checked before the pull (F-2026-050). True, or False with a warning in the activity log, and the note
        dropped; None when there's nothing to compare yet (no update asked, this process from before it, TrueNAS not
        reached), the note kept a day at most."""
        want = self.app._load_json(PULLED)
        if not isinstance(want, dict) or not isinstance(want.get("digest"), str):
            return None
        at = want.get("at") if isinstance(want.get("at"), (int, float)) else 0
        if time.time() - at > PULLED_FOR:
            self._drop_pulled()
            return None
        if at >= self.started:  # (the process that asked: TrueNAS hasn't pulled yet, the tag still names the old image)
            return None
        tag = f"ghcr.io/{IMAGE}:{want.get('tag') or 'latest'}"
        try:
            with self.app.truenas() as tn:
                images = tn.call("app.image.query", []) or []
        except Exception:  # noqa: BLE001 — not reached, or not allowed: compared on a later start
            return None
        held = [i for i in images if isinstance(i, dict) and tag in (i.get("repo_tags") or [])]
        if not held:
            return None
        digests = {str(d).rpartition("@")[2] for i in held for d in i.get("repo_digests") or []}
        self._drop_pulled()
        if want["digest"] in digests:
            self.app.activity.event("update", M("The image TrueNAS pulled is the one checked against GitHub’s build ({digest})", digest=want["digest"][:19]))
            return True
        self.app.activity.event("update", M("The image TrueNAS pulled for {tag} isn’t the one checked against GitHub’s build ({digest}): the tag moved between "
                                            "the check and the pull. Check the app’s image in TrueNAS.", tag=tag, digest=want["digest"][:19]), "warn")
        return False

    def _drop_pulled(self) -> None:
        try:
            os.remove(os.path.join(self.app.config_dir, PULLED))
        except OSError:
            pass

    def available(self) -> bool:
        """A newer version — or, on the dev channel, a newer commit of dev than this build's."""
        if self.latest and self.latest != __version__:
            return True
        return bool(self.latest_commit and self.commit and not self.latest_commit.startswith(self.commit) and not self.commit.startswith(self.latest_commit))

    def build_url(self) -> str:
        """This build's page on GitHub: a stable build's release, a dev build's commit — else the repository."""
        if self.channel == "dev":
            return f"{REPO_URL}/commit/{self.commit}" if self.commit else f"{REPO_URL}/tree/dev"
        return f"{REPO_URL}/releases/tag/v{__version__}"

    def status(self) -> dict:
        return {"latest": self.latest, "available": self.available(), "checked": self.checked, "error": self.error, "repo": REPO_URL, "url": self.build_url(),
                "channel": self.channel, "branch": BRANCH.get(self.channel, "main"), "commit": (self.commit or "")[:7], "latest_commit": (self.latest_commit or "")[:7],
                "checking": self.checking, "updating": self.updating, "version": self.app.version}
