"""Hardlink guard shared by the extensions.

A hardlink inside the vault to a file outside it is a real directory entry: there is no
link to follow, so path containment cannot see it and the file reads as an ordinary note.
Refusing st_nlink > 1 is the only cheap test that catches it, at the cost of not
supporting legitimate in-vault hardlinks (upstream issue #53).

Upstream #79 applies the same rule to the host's read paths. The extensions carry their
own copy because they read files directly or enumerate them with rglob, which the host
guard does not reach, and because a host older than #79 has no guard at all.
"""

from pathlib import Path


def has_extra_hard_links(path: Path) -> bool:
    """Return whether a file has more than one directory entry pointing at its inode."""
    try:
        return path.stat().st_nlink > 1
    except OSError:
        return False
