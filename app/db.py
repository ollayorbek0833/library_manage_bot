from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path.as_posix())
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")

        schema_path = Path(__file__).with_name("models.sql")
        schema_sql = schema_path.read_text(encoding="utf-8")
        await self._conn.executescript(schema_sql)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not initialized")
        return self._conn

    async def ensure_default_settings(self, defaults: dict[str, str]) -> None:
        for key, value in defaults.items():
            await self.conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )
        await self.conn.commit()

    async def get_setting(self, key: str) -> str | None:
        async with self.conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return str(row["value"])

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        await self.conn.commit()

    async def get_settings(
        self,
        keys: Iterable[str] | None = None,
    ) -> dict[str, str]:
        if keys is None:
            query = "SELECT key, value FROM settings"
            params: tuple[Any, ...] = ()
        else:
            key_list = list(keys)
            if not key_list:
                return {}
            placeholders = ",".join("?" for _ in key_list)
            query = f"SELECT key, value FROM settings WHERE key IN ({placeholders})"
            params = tuple(key_list)

        async with self.conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def create_book(
        self,
        title: str,
        author: str,
        total_pages: int,
        start_page: int,
        start_date: str,
    ) -> int:
        created_at = utc_now_iso()
        last_read_page = start_page - 1
        cursor = await self.conn.execute(
            """
            INSERT INTO books(
                title,
                author,
                total_pages,
                start_page,
                start_date,
                status,
                header_message_id,
                last_read_page,
                last_read_date,
                created_at,
                finished_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', NULL, ?, NULL, ?, NULL)
            """,
            (
                title,
                author,
                total_pages,
                start_page,
                start_date,
                last_read_page,
                created_at,
            ),
        )
        await self.conn.commit()
        return int(cursor.lastrowid)

    async def delete_book(self, book_id: int) -> None:
        await self.conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        await self.conn.commit()

    async def set_book_header_message(self, book_id: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE books SET header_message_id = ? WHERE id = ?",
            (message_id, book_id),
        )
        await self.conn.commit()

    async def get_book(self, book_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM books WHERE id = ?",
            (book_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row_to_dict(row)

    async def list_books(
        self,
        statuses: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query = f"""
                SELECT * FROM books
                WHERE status IN ({placeholders})
                ORDER BY id DESC
            """
            params: tuple[Any, ...] = tuple(statuses)
        else:
            query = "SELECT * FROM books ORDER BY id DESC"
            params = ()

        async with self.conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_active_books(self) -> list[dict[str, Any]]:
        return await self.list_books(statuses=("active",))

    async def set_book_status(self, book_id: int, status: str) -> None:
        await self.conn.execute(
            "UPDATE books SET status = ? WHERE id = ?",
            (status, book_id),
        )
        await self.conn.commit()

    async def update_book_progress(
        self,
        book_id: int,
        last_read_page: int,
        last_read_date: str,
    ) -> None:
        await self.conn.execute(
            """
            UPDATE books
            SET last_read_page = ?, last_read_date = ?
            WHERE id = ?
            """,
            (last_read_page, last_read_date, book_id),
        )
        await self.conn.commit()

    async def finish_book(
        self,
        book_id: int,
        finish_date: str,
        last_read_page: int | None = None,
    ) -> bool:
        if last_read_page is None:
            cursor = await self.conn.execute(
                """
                UPDATE books
                SET status = 'finished',
                    finished_at = ?,
                    last_read_date = ?
                WHERE id = ? AND status != 'finished'
                """,
                (finish_date, finish_date, book_id),
            )
        else:
            cursor = await self.conn.execute(
                """
                UPDATE books
                SET status = 'finished',
                    finished_at = ?,
                    last_read_date = ?,
                    last_read_page = ?
                WHERE id = ? AND status != 'finished'
                """,
                (finish_date, finish_date, last_read_page, book_id),
            )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def get_reminder(self, reminder_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM reminders WHERE id = ?",
            (reminder_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row_to_dict(row)

    async def get_reminder_by_book_and_date(
        self,
        book_id: int,
        reminder_date: str,
    ) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM reminders WHERE book_id = ? AND date = ?",
            (book_id, reminder_date),
        ) as cursor:
            row = await cursor.fetchone()
        return row_to_dict(row)

    async def get_latest_reminder(self, book_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            """
            SELECT * FROM reminders
            WHERE book_id = ?
            ORDER BY date DESC, id DESC
            LIMIT 1
            """,
            (book_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row_to_dict(row)

    async def create_or_get_reminder(
        self,
        book_id: int,
        reminder_date: str,
        from_page: int,
        to_page: int,
        pages_planned: int,
    ) -> tuple[dict[str, Any], bool]:
        existing = await self.get_reminder_by_book_and_date(book_id, reminder_date)
        if existing is not None:
            return existing, False

        created_at = utc_now_iso()
        cursor = await self.conn.execute(
            """
            INSERT INTO reminders(
                book_id,
                date,
                from_page,
                to_page,
                pages_planned,
                status,
                channel_message_id,
                created_at,
                done_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)
            """,
            (book_id, reminder_date, from_page, to_page, pages_planned, created_at),
        )
        await self.conn.commit()

        reminder_id = int(cursor.lastrowid)
        reminder = await self.get_reminder(reminder_id)
        if reminder is None:
            raise RuntimeError("Failed to fetch reminder after insert")
        return reminder, True

    async def set_reminder_channel_message(
        self,
        reminder_id: int,
        message_id: int,
    ) -> None:
        await self.conn.execute(
            "UPDATE reminders SET channel_message_id = ? WHERE id = ?",
            (message_id, reminder_id),
        )
        await self.conn.commit()

    async def mark_reminder_done(
        self,
        reminder_id: int,
        done_at: str,
        from_page: int | None = None,
        to_page: int | None = None,
    ) -> None:
        if from_page is None or to_page is None:
            await self.conn.execute(
                """
                UPDATE reminders
                SET status = 'done', done_at = ?
                WHERE id = ?
                """,
                (done_at, reminder_id),
            )
        else:
            await self.conn.execute(
                """
                UPDATE reminders
                SET status = 'done',
                    done_at = ?,
                    from_page = ?,
                    to_page = ?
                WHERE id = ?
                """,
                (done_at, from_page, to_page, reminder_id),
            )
        await self.conn.commit()

