PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    total_pages INTEGER NOT NULL CHECK (total_pages > 0),
    start_page INTEGER NOT NULL DEFAULT 1 CHECK (start_page > 0),
    start_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'finished')),
    header_message_id INTEGER,
    last_read_page INTEGER NOT NULL CHECK (last_read_page >= 0),
    last_read_date TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    from_page INTEGER NOT NULL CHECK (from_page > 0),
    to_page INTEGER NOT NULL CHECK (to_page > 0),
    pages_planned INTEGER NOT NULL CHECK (pages_planned > 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'skipped')),
    channel_message_id INTEGER,
    created_at TEXT NOT NULL,
    done_at TEXT,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    UNIQUE(book_id, date)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_status ON books(status);
CREATE INDEX IF NOT EXISTS idx_reminders_book_date ON reminders(book_id, date);
CREATE INDEX IF NOT EXISTS idx_reminders_status_date ON reminders(status, date);

