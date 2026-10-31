VERSION = 1
# A dedicated SQLite schema; no references to users/subscriptions/payments tables.
STATEMENTS = [
    """CREATE TABLE tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL CHECK(owner>0),
        category TEXT NOT NULL, question TEXT, description TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('new','working','waiting','resolved','closed')),
        assignee INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
    "CREATE UNIQUE INDEX one_open_ticket ON tickets(owner) WHERE status IN ('new','working','waiting')",
    """CREATE TABLE history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticket INTEGER NOT NULL REFERENCES tickets(id),
        actor INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
        attachment TEXT, source TEXT NOT NULL UNIQUE, created_at REAL NOT NULL)""",
    "CREATE INDEX history_ticket ON history(ticket,id)",
    "CREATE TABLE processed_inputs (source TEXT PRIMARY KEY, ticket INTEGER NOT NULL REFERENCES tickets(id))",
    """CREATE TABLE sessions (
        owner INTEGER PRIMARY KEY CHECK(owner>0), category TEXT, question TEXT,
        target INTEGER REFERENCES tickets(id), nav_id INTEGER, nav_key TEXT NOT NULL DEFAULT 'home')""",
    """CREATE TABLE rates (
        actor INTEGER NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY(actor,bucket))""",
    """CREATE TABLE deliveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event INTEGER NOT NULL REFERENCES history(id),
        ticket INTEGER NOT NULL REFERENCES tickets(id), chat INTEGER NOT NULL,
        audience TEXT NOT NULL CHECK(audience IN ('user','operator')), payload TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','sent','blocked')),
        attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, lease REAL,
        telegram_id INTEGER, error_code TEXT, UNIQUE(event,chat))""",
    "CREATE INDEX delivery_due ON deliveries(state,due)",
    """CREATE TABLE operator_messages (
        chat INTEGER NOT NULL, message INTEGER NOT NULL, ticket INTEGER NOT NULL REFERENCES tickets(id),
        PRIMARY KEY(chat,message))""",
    "CREATE TABLE bot_binding (id INTEGER PRIMARY KEY CHECK(id=1), bot_id INTEGER NOT NULL, admin_chat INTEGER NOT NULL)",
    "CREATE TABLE polling_cursor (id INTEGER PRIMARY KEY CHECK(id=1), offset INTEGER NOT NULL)",
    """CREATE TABLE worker_health (id INTEGER PRIMARY KEY CHECK(id=1), checked_at REAL NOT NULL,
        pending INTEGER NOT NULL, blocked INTEGER NOT NULL)""",
]
