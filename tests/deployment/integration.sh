#!/usr/bin/env bash
set -euo pipefail

# Intent: an application upgrade within current schema reuses the existing database.
# Setup: deploy the real PR base revision, without modifying its schema declaration.
# Expected: candidate takes over while seeded data and allocator survive.
# Regression: accidental database replacement, schema migration or allocator reset.
git worktree add --detach /tmp/ocpp-csms-base "$BASE_SHA"
# Resolve the schema versions from source without importing either application.
read_schema() {
  python3 - "$1" <<'PY'
import ast, sys
from pathlib import Path
for node in ast.parse(Path(sys.argv[1]).read_text()).body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CURRENT_SCHEMA_VERSION" for t in node.targets):
        value = ast.literal_eval(node.value)
        if not isinstance(value, int) or value < 1:
            raise SystemExit("Invalid schema version")
        print(value)
        break
else:
    raise SystemExit("Missing schema version")
PY
}
TEST_SCHEMA="$(read_schema src/ocpp_csms/schema.py)"
BASE_SCHEMA="$(read_schema /tmp/ocpp-csms-base/src/ocpp_csms/schema.py)"
export TEST_SCHEMA
if (( TEST_SCHEMA < BASE_SCHEMA )); then
  echo "::error::Candidate schema is older than base; no automatic downgrade is supported"
  exit 1
fi
if (( TEST_SCHEMA != BASE_SCHEMA )); then
  echo "::error::Application upgrade fixture requires matching schemas ($BASE_SCHEMA vs $TEST_SCHEMA); schema advancement needs a separate new-database scenario"
  exit 1
fi
(cd /tmp/ocpp-csms-base && ./deploy.sh)
sudo systemctl is-active --quiet ocpp-csms.service
sudo systemctl is-enabled --quiet ocpp-csms.service
/usr/local/bin/ocpp-csms --help >/dev/null
db="$HOME/ocpp-csms-data/schemas/$TEST_SCHEMA/ocpp-csms.sqlite3"
test -f "$db"
# Fresh-install contract: the base deploy must initialize current schema.
# This is checked before seeding upgrade fixtures to distinguish installation
# failures from upgrade persistence regressions.
python3 - "$db" <<'PY'
import sqlite3, sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as conn:
    assert conn.execute("PRAGMA user_version").fetchone()[0] == int(__import__("os").environ["TEST_SCHEMA"])
    assert conn.execute("SELECT last_id FROM transaction_allocator").fetchone() == (0,)
PY
# Post-deployment socket check; not continuous listener sampling.
python3 - <<'PY'
import socket
with socket.create_connection(("127.0.0.1", 9000), timeout=5):
    pass
PY
/usr/local/bin/ocpp-csms status
old_release="$(readlink -f "$HOME/.local/share/ocpp-csms/current")"
old_inode="$(stat -c '%d:%i' "$db")"
python3 - "$db" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("PRAGMA user_version").fetchone()[0] == int(__import__("os").environ["TEST_SCHEMA"])
    db.execute("UPDATE transaction_allocator SET last_id=314159")
    db.execute("""INSERT INTO runtime_events
        (occurred_at, event, charger_id, details_json)
        VALUES ('2026-01-01T00:00:00Z', 'ci-upgrade-probe', 'CI001', '{}')""")
    db.execute("""INSERT INTO transactions
        (transaction_id, charger_id, connector_id, id_tag, state, last_activity_at)
        VALUES (314159, 'CI001', 1, 'CI-TAG', 'stopped', '2026-01-01T00:00:00Z')""")
PY
./deploy.sh
test "$(stat -c '%d:%i' "$db")" = "$old_inode"
new_release="$(readlink -f "$HOME/.local/share/ocpp-csms/current")"
test "$new_release" != "$old_release" || { echo "::error::candidate release did not replace base"; exit 1; }
python3 - "$db" <<'PY'
import sqlite3, sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    assert db.execute("PRAGMA user_version").fetchone()[0] == int(__import__("os").environ["TEST_SCHEMA"])
    assert db.execute("SELECT last_id FROM transaction_allocator").fetchone() == (314159,)
    assert db.execute("SELECT charger_id FROM transactions WHERE transaction_id=314159").fetchone() == ('CI001',)
    assert db.execute("SELECT event FROM runtime_events WHERE event='ci-upgrade-probe'").fetchone() == ('ci-upgrade-probe',)
PY
sudo systemctl is-active --quiet ocpp-csms.service
sudo systemctl is-enabled --quiet ocpp-csms.service
/usr/local/bin/ocpp-csms --help >/dev/null
/usr/local/bin/ocpp-csms status
# Intent: idempotent redeployment must not alter an existing same-schema store.
# Setup: candidate already active with seeded transaction and allocator state.
# Expected: no release switch, service restart or database replacement.
# Regression: destructive same-revision deployment or unsafe staging.
bash tests/deployment/acceptance.sh

