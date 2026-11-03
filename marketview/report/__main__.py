"""python -m marketview.report [YYYY-MM-DD]  — 서버 없이 리포트를 만든다 (기본: 오늘)."""

import json
import sys

from .. import db
from . import run

conn = db.connect()
db.migrate(conn)
try:
    print(json.dumps(run.generate(conn, sys.argv[1] if len(sys.argv) > 1 else None), ensure_ascii=False, indent=2))
finally:
    conn.close()
