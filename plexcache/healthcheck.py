"""The image's health check (``python -m plexcache.healthcheck``), for both of the app's services:
the web app answers on PLEXCACHE_PORT (8642), the optimizer on PLEXCACHE_OPTIMIZER_PORT (8643)."""

import argparse
import os
import sys
import urllib.error
import urllib.request

from . import version_label


def main() -> int:
    """The container's health check: whether the app and the optimizer answer."""
    ports = (os.environ.get("PLEXCACHE_PORT") or "8642", os.environ.get("PLEXCACHE_OPTIMIZER_PORT") or "8643")
    for port in dict.fromkeys(ports):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=4).close()
            return 0
        except urllib.error.HTTPError as e:  # (an error answer: closed, like a good one)
            e.close()
            continue
        except OSError:
            continue
    return 1


def cli(argv: list[str] | None = None) -> int:
    """The command line, ``python -m plexcache.healthcheck``: --help and --version answered, anything else refused
    (exit 2: the ports are the environment's), and with nothing asked, the check made."""
    ap = argparse.ArgumentParser(prog="python -m plexcache.healthcheck", description=(
        "The container's health check: exit 0 where the app (PLEXCACHE_PORT, 8642) or the optimizer "
        "(PLEXCACHE_OPTIMIZER_PORT, 8643) answers, 1 where neither does."))
    ap.add_argument("--version", action="version", version=f"plexcache.healthcheck {version_label()}")
    ap.parse_args(argv)
    return main()


if __name__ == "__main__":
    sys.exit(cli())
