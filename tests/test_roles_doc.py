"""docs/ROLES.md must say what permissions.py does."""
import re
from pathlib import Path

import permissions as P

DOC = (Path(__file__).resolve().parent.parent / "docs" / "ROLES.md").read_text()


def test_every_row_in_the_doc_matches_the_defaults():
    roles = list(P.ROLES)
    rows = re.findall(r"^\| .*\(`([a-z_.]+)`\) \| (.*) \|$", DOC, flags=re.M)
    assert {k for k, _ in rows} == set(P.PERMISSIONS)
    for key, cells in rows:
        cells = [c.strip() for c in cells.split("|")]
        assert len(cells) == len(roles), key
        for role, cell in zip(roles, cells):
            assert (cell == "yes") == (key in P.DEFAULTS[role]), f"{key} / {role}"
