"""Guard: no colon-prefixed bind names inside SQL `--` comments.

SQLAlchemy's text() binds every `:name` it sees, including ones inside a
`--` comment, but SQL Server ignores the comment. The driver then
receives one parameter more than there are markers and the query fails
("The SQL contains 5 parameter markers, but 6 parameters were
supplied"). That silently zeroed the Order Assistant performance strip
from 2026-11-06 until 2026-11-09.
"""
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
# `-- ... :name` where :name is not part of `::` (e.g. a URL or a cast).
COMMENT_PARAM = re.compile(r"--[^\n]*(?<!:):[A-Za-z_]\w*")


def test_no_bind_names_in_sql_comments():
    offenders = []
    for path in list(BACKEND.glob("routers/*.py")) + list(BACKEND.glob("services/*.py")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            # Only SQL comments: Python comments start with '#', and SQL
            # lives inside strings, where a comment starts with '--'.
            if stripped.startswith("--") and COMMENT_PARAM.search(stripped):
                offenders.append(f"{path.relative_to(BACKEND)}:{no}: {stripped}")
    assert not offenders, "Colon param names inside SQL comments:\n" + "\n".join(offenders)
