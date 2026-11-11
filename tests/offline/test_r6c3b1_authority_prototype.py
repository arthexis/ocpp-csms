"""R6C-3B1 disposable SQLite authority MODEL. Never imported by production CSMS.

Every database exists only in pytest tmp_path. Synthetic call-UID bindings
are ASSUMPTIONS, not proof of existing ocpp-go pre-send capabilities.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

import pytest


class FenceDenied(ValueError):
    """Requested session/epoch/identity lacks write or read authority."""


@dataclass(frozen=True)
class Scope:
    source_id: str
    database_incarnation: str
    tenant_id: str
    cp_id: str

    def key(self):
        parts = (self.source_id, self.database_incarnation, self.tenant_id, self.cp_id)
        if any(not x or "\x00" in x for x in parts):
            raise FenceDenied("incomplete or ambiguous identity")
        return "\x00".join(parts)


class OfflineAuthority:
    """Test-owned reference model, NOT an approved database migration."""

    def __init__(self, path):
        self.path = path
        # Reopen an existing disposable fixture without resetting its owner.
        if self.path.exists():
            return
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE owners(
                  scope TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                  database_incarnation TEXT NOT NULL, tenant_id TEXT NOT NULL,
                  cp_id TEXT NOT NULL, owner_session TEXT NOT NULL,
                  epoch INTEGER NOT NULL CHECK(epoch>0),
                  revision INTEGER NOT NULL CHECK(revision>0));
                CREATE TABLE attempts(
                  scope TEXT NOT NULL, attempt_id TEXT NOT NULL,
                  session_id TEXT NOT NULL, epoch INTEGER NOT NULL,
                  call_uid TEXT NOT NULL,
                  PRIMARY KEY(scope,attempt_id),
                  UNIQUE(scope,session_id,epoch,call_uid));
                CREATE TABLE allocator(singleton INTEGER PRIMARY KEY,last_id INTEGER NOT NULL);
                INSERT INTO allocator VALUES(1,0);
                CREATE TABLE transactions(
                  transaction_id INTEGER PRIMARY KEY, scope TEXT NOT NULL,
                  connector INTEGER NOT NULL, state TEXT NOT NULL,
                  start_event_id INTEGER NOT NULL, stop_event_id INTEGER);
                CREATE TABLE evidence(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT NOT NULL,
                  epoch INTEGER NOT NULL,session_id TEXT NOT NULL,
                  action TEXT NOT NULL,transaction_id INTEGER,
                  attempt_id TEXT,call_uid TEXT,orphan INTEGER NOT NULL DEFAULT 0);
            """)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        db.execute("PRAGMA busy_timeout=3000")
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _owner(db, scope):
        row = db.execute("SELECT * FROM owners WHERE scope=?", (scope.key(),)).fetchone()
        if row is None:
            raise FenceDenied("no durable owner for this identity")
        if tuple(row[k] for k in
                 ("source_id", "database_incarnation", "tenant_id", "cp_id")) != (
                     scope.source_id, scope.database_incarnation,
                     scope.tenant_id, scope.cp_id):
            raise FenceDenied("mismatched authority identity")
        return row

    @classmethod
    def _check(cls, db, scope, session, epoch):
        row = cls._owner(db, scope)
        if row["owner_session"] != session or row["epoch"] != epoch:
            raise FenceDenied("stale owner epoch or session")
        return row

    def claim(self, scope, session):
        if not session:
            raise FenceDenied("empty owner session")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT epoch,revision FROM owners WHERE scope=?",
                             (scope.key(),)).fetchone()
            if old is None:
                epoch, revision = 1, 1
                db.execute("INSERT INTO owners VALUES(?,?,?,?,?,?,?,?)", (
                    scope.key(), scope.source_id, scope.database_incarnation,
                    scope.tenant_id, scope.cp_id, session, epoch, revision))
            else:
                epoch, revision = old["epoch"]+1, old["revision"]+1
                db.execute("""UPDATE owners SET owner_session=?,epoch=?,revision=?
                           WHERE scope=?""", (session, epoch, revision, scope.key()))
            db.commit()
            return epoch, revision

    def bind_synthetic_uid(self, scope, session, epoch, attempt_id, uid):
        """Illustrative prior binding; real Go ability is NOT established."""
        if not attempt_id or not uid or len(uid) > 36:
            raise FenceDenied("missing or invalid pre-send correlation")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check(db, scope, session, epoch)
            try:
                db.execute("INSERT INTO attempts VALUES(?,?,?,?,?)", (
                    scope.key(), attempt_id, session, epoch, uid))
            except sqlite3.IntegrityError as exc:
                raise FenceDenied("duplicate attempt or OCPP call UID") from exc
            db.execute("UPDATE owners SET revision=revision+1 WHERE scope=?",
                       (scope.key(),))
            db.commit()

    def write(self, scope, session, epoch, action, *, transaction_id=None,
              connector=1, attempt_id=None, call_uid=None, before_commit=None):
        if action not in {"StartTransaction", "MeterValues", "StopTransaction"}:
            raise ValueError("unsupported prototype action")
        if connector < 1 or connector > 128:
            raise FenceDenied("connector outside supported scope")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check(db, scope, session, epoch)
            if action == "StartTransaction":
                found = db.execute("""SELECT 1 FROM attempts
                    WHERE scope=? AND session_id=? AND epoch=?
                    AND attempt_id=? AND call_uid=?""",
                    (scope.key(), session, epoch, attempt_id, call_uid)).fetchone()
                if found is None:
                    raise FenceDenied("no durable attempt+UID binding before Start")
                prior = db.execute("""SELECT 1 FROM evidence
                    WHERE scope=? AND attempt_id=? AND action='StartTransaction'""",
                    (scope.key(), attempt_id)).fetchone()
                if prior:
                    raise FenceDenied("start already committed; do not replay")
                transaction_id = db.execute(
                    "SELECT last_id+1 FROM allocator WHERE singleton=1").fetchone()[0]
                db.execute("UPDATE allocator SET last_id=? WHERE singleton=1",
                           (transaction_id,))
                orphan = 0
            else:
                row = db.execute("""SELECT state FROM transactions
                    WHERE scope=? AND transaction_id=?""",
                    (scope.key(), transaction_id)).fetchone()
                orphan = int(row is None)
            event = db.execute("""INSERT INTO evidence(
                scope,epoch,session_id,action,transaction_id,attempt_id,call_uid,orphan
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (scope.key(), epoch, session, action, transaction_id,
                 attempt_id, call_uid, orphan))
            eid = event.lastrowid
            if action == "StartTransaction":
                db.execute("INSERT INTO transactions VALUES(?,?,?,'open',?,NULL)",
                           (transaction_id, scope.key(), connector, eid))
            elif action == "StopTransaction" and not orphan:
                db.execute("""UPDATE transactions SET state='stopped',stop_event_id=?
                    WHERE scope=? AND transaction_id=? AND stop_event_id IS NULL""",
                    (eid, scope.key(), transaction_id))
            db.execute("UPDATE owners SET revision=revision+1 WHERE scope=?",
                       (scope.key(),))
            if before_commit:
                before_commit()
            db.commit()
            return transaction_id, eid, bool(orphan)

    def snapshot(self, scope):
        """Single read transaction, observational only, no CleanVerified."""
        with self._db() as db:
            db.execute("BEGIN")
            owner = self._owner(db, scope)
            rows = db.execute("""SELECT transaction_id,connector,state,
                       start_event_id,stop_event_id FROM transactions
                       WHERE scope=? ORDER BY transaction_id""",
                       (scope.key(),)).fetchall()
            evs = db.execute("""SELECT id,action,transaction_id,orphan
                       FROM evidence WHERE scope=? ORDER BY id""",
                       (scope.key(),)).fetchall()
            answer = {"epoch": owner["epoch"], "revision": owner["revision"],
                      "owner_session": owner["owner_session"],
                      "transactions": [dict(x) for x in rows],
                      "events": [dict(x) for x in evs], "quarantined": True}
            db.commit()
            return answer


@pytest.fixture
def fixture(tmp_path):
    return OfflineAuthority(tmp_path / "prototype.sqlite"), Scope(
        "source-a", "db-incarnation-a", "tenant-a", "CP-1")


def test_start_meter_stop_commit_and_revision(fixture):
    model, scope = fixture
    epoch, rev = model.claim(scope, "ws-1")
    model.bind_synthetic_uid(scope, "ws-1", epoch, "attempt-A", "uid-A")
    tid, first, orphan = model.write(scope, "ws-1", epoch, "StartTransaction",
                                      attempt_id="attempt-A", call_uid="uid-A")
    assert (tid, orphan) == (1, False)
    assert not model.write(scope, "ws-1", epoch, "MeterValues", transaction_id=tid)[2]
    assert not model.write(scope, "ws-1", epoch, "StopTransaction", transaction_id=tid)[2]
    snap = model.snapshot(scope)
    assert snap["revision"] == rev+4 and snap["epoch"] == epoch
    assert snap["transactions"] == [{
        "transaction_id": 1, "connector": 1, "state": "stopped",
        "start_event_id": first, "stop_event_id": 3}]
    assert snap["quarantined"]


@pytest.mark.parametrize("action", ["StartTransaction", "MeterValues", "StopTransaction"])
def test_stale_ws_start_meter_stop_never_commit(fixture, action):
    model, scope = fixture
    old, _ = model.claim(scope, "old-ws")
    model.bind_synthetic_uid(scope, "old-ws", old, "attempt", "uid")
    tid = model.write(scope, "old-ws", old, "StartTransaction",
                      attempt_id="attempt", call_uid="uid")[0]
    new, _ = model.claim(scope, "new-ws")
    before = model.snapshot(scope)
    with pytest.raises(FenceDenied, match="stale"):
        model.write(scope, "old-ws", old, action,
                    transaction_id=tid, attempt_id="attempt", call_uid="uid")
    assert new > old and model.snapshot(scope) == before


def test_inflight_old_commit_is_included_by_new_epoch(fixture):
    model, scope = fixture
    old, _ = model.claim(scope, "old-ws")
    model.bind_synthetic_uid(scope, "old-ws", old, "attempt", "uid")
    entered = threading.Event()
    release = threading.Event()
    result = {}

    def during_commit():
        entered.set()
        assert release.wait(3)

    def old_writer():
        try:
            result["written"] = model.write(
                scope, "old-ws", old, "StartTransaction",
                attempt_id="attempt", call_uid="uid", before_commit=during_commit)
        except Exception as exc:
            result["write_error"] = repr(exc)

    def new_claim():
        try:
            result["claimed"] = model.claim(scope, "new-ws")
        except Exception as exc:
            result["claim_error"] = repr(exc)

    writer = threading.Thread(target=old_writer)
    writer.start()
    try:
        assert entered.wait(3)
        claimant = threading.Thread(target=new_claim)
        claimant.start()
        release.set()
        claimant.join(4)
    finally:
        release.set()
        writer.join(4)
    assert not writer.is_alive() and not claimant.is_alive()
    assert "write_error" not in result and "claim_error" not in result
    assert result["claimed"][0] == old+1
    assert model.snapshot(scope)["transactions"][0]["start_event_id"] == result["written"][1]


def test_synthetic_uid_binding_does_not_justify_payload_correlation(fixture):
    model, scope = fixture
    epoch, _ = model.claim(scope, "ws-1")
    with pytest.raises(FenceDenied, match="binding"):
        model.write(scope, "ws-1", epoch, "StartTransaction",
                    attempt_id="A", call_uid="uid-A")
    model.bind_synthetic_uid(scope, "ws-1", epoch, "A", "uid-A")
    first = model.write(scope, "ws-1", epoch, "StartTransaction",
                        attempt_id="A", call_uid="uid-A")
    with pytest.raises(FenceDenied, match="replay"):
        model.write(scope, "ws-1", epoch, "StartTransaction",
                    attempt_id="A", call_uid="uid-A")
    model.bind_synthetic_uid(scope, "ws-1", epoch, "B", "uid-B")
    second = model.write(scope, "ws-1", epoch, "StartTransaction",
                         attempt_id="B", call_uid="uid-B")
    assert first[0] != second[0]
    with pytest.raises(FenceDenied, match="duplicate"):
        model.bind_synthetic_uid(scope, "ws-1", epoch, "C", "uid-B")
    assert model.snapshot(scope)["quarantined"]


def test_unknown_stop_is_orphan_not_closure(fixture):
    model, scope = fixture
    epoch, _ = model.claim(scope, "ws-1")
    _, event_id, orphan = model.write(scope, "ws-1", epoch,
                                       "StopTransaction", transaction_id=999)
    snap = model.snapshot(scope)
    assert orphan and not snap["transactions"]
    assert snap["events"] == [{"id": event_id, "action": "StopTransaction",
                               "transaction_id": 999, "orphan": 1}]
    assert snap["quarantined"]


def test_source_incarnation_tenant_cp_scope_isolated(fixture):
    model, scope = fixture
    epoch, _ = model.claim(scope, "session")
    variants = [
        Scope("foreign", scope.database_incarnation, scope.tenant_id, scope.cp_id),
        Scope(scope.source_id, "restored", scope.tenant_id, scope.cp_id),
        Scope(scope.source_id, scope.database_incarnation, "foreign", scope.cp_id),
        Scope(scope.source_id, scope.database_incarnation, scope.tenant_id, "CP-2"),
    ]
    for other in variants:
        with pytest.raises(FenceDenied, match="no durable owner"):
            model.snapshot(other)
        with pytest.raises(FenceDenied, match="no durable owner"):
            model.write(other, "session", epoch, "MeterValues", transaction_id=1)


def test_revision_and_socket_change_never_authorize_recovery(fixture):
    model, scope = fixture
    old, _ = model.claim(scope, "old-ws")
    before = model.snapshot(scope)
    model.claim(scope, "new-ws")
    after = model.snapshot(scope)
    assert after["revision"] > before["revision"]
    assert after["epoch"] > before["epoch"]
    assert before["quarantined"] and after["quarantined"]
    assert not hasattr(model, "activate") and not hasattr(model, "clean_verified")
    with pytest.raises(FenceDenied, match="stale"):
        model.bind_synthetic_uid(scope, "old-ws", old, "attempt", "uid")


def test_empty_identity_invalid_uid_and_stale_epoch_fail_closed(fixture):
    model, scope = fixture
    with pytest.raises(FenceDenied):
        model.claim(Scope("", "inc", "tenant", "CP"), "session")
    with pytest.raises(FenceDenied):
        model.claim(scope, "")
    epoch, _ = model.claim(scope, "ws-1")
    for uid in ("", "a"*37):
        with pytest.raises(FenceDenied):
            model.bind_synthetic_uid(scope, "ws-1", epoch, "attempt", uid)
    with pytest.raises(FenceDenied):
        model.bind_synthetic_uid(scope, "ws-1", epoch+1, "attempt", "uid")


def test_owner_epoch_and_revision_survive_disposable_db_reopen(fixture):
    model, scope = fixture
    old, revision = model.claim(scope, "old-ws")
    reopened = OfflineAuthority(model.path)
    assert reopened.snapshot(scope)["epoch"] == old
    assert reopened.snapshot(scope)["revision"] == revision
    new, _ = reopened.claim(scope, "new-ws")
    second_reopen = OfflineAuthority(model.path)
    assert second_reopen.snapshot(scope)["epoch"] == new
    with pytest.raises(FenceDenied, match="stale"):
        second_reopen.write(scope, "old-ws", old, "MeterValues", transaction_id=123)
