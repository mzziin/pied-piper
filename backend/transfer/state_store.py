"""SQLite persistent transfer state store for Phase 10.

Maintains durable records across process restarts for transfers, files, and chunk hashes
using standard library sqlite3 (async-wrapped via thread executor, protocol_spec.md §6.1).
"""

import asyncio
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# SQL Schema Definitions (§6.1)
# -----------------------------------------------------------------------------

CREATE_TRANSFERS_TABLE = """
CREATE TABLE IF NOT EXISTS transfers (
    transfer_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

CREATE_FILES_TABLE = """
CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    transfer_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL,
    chunk_size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    highest_verified_chunk INTEGER NOT NULL DEFAULT -1,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY (transfer_id) REFERENCES transfers(transfer_id) ON DELETE CASCADE
);
"""

CREATE_CHUNK_HASHES_TABLE = """
CREATE TABLE IF NOT EXISTS chunk_hashes (
    file_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_id, chunk_index),
    FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
);
"""

CREATE_INDICES = """
CREATE INDEX IF NOT EXISTS idx_files_transfer_id ON files(transfer_id);
CREATE INDEX IF NOT EXISTS idx_chunk_hashes_file_id ON chunk_hashes(file_id);
"""


class TransferStateStore:
    """Thread-safe, async SQLite store for durable transfer state and checkpoint recovery."""

    def __init__(self, db_path: Union[str, Path] = "./pied_piper.db") -> None:
        self.db_path: Path = Path(db_path)
        self._initialized: bool = False
        self._lock = asyncio.Lock()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a sqlite3 connection configured with WAL and foreign keys."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _sync_init_db(self) -> None:
        """Synchronously execute idempotent table and index creation."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute(CREATE_TRANSFERS_TABLE)
            conn.execute(CREATE_FILES_TABLE)
            conn.execute(CREATE_CHUNK_HASHES_TABLE)
            conn.executescript(CREATE_INDICES)
            conn.commit()
        self._initialized = True

    async def initialize(self) -> None:
        """Ensure database schema is created and verified asynchronously."""
        async with self._lock:
            if not self._initialized:
                await asyncio.to_thread(self._sync_init_db)

    # -------------------------------------------------------------------------
    # Transfers Table Operations
    # -------------------------------------------------------------------------

    def _sync_record_transfer(self, transfer_id: str, role: str, status: str) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO transfers (transfer_id, role, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(transfer_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at;
                """,
                (transfer_id, role, status, now, now),
            )
            conn.commit()

    async def record_transfer(self, transfer_id: str, role: str, status: str = "in_progress") -> None:
        """Record or update a transfer session row."""
        await self.initialize()
        await asyncio.to_thread(self._sync_record_transfer, transfer_id, role, status)

    def _sync_update_transfer_status(self, transfer_id: str, status: str) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE transfers SET status = ?, updated_at = ? WHERE transfer_id = ?;",
                (status, now, transfer_id),
            )
            conn.commit()

    async def update_transfer_status(self, transfer_id: str, status: str) -> None:
        """Update transfer lifecycle status (e.g. 'completed', 'failed', 'paused')."""
        await self.initialize()
        await asyncio.to_thread(self._sync_update_transfer_status, transfer_id, status)

    def _sync_get_transfer(self, transfer_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM transfers WHERE transfer_id = ?;", (transfer_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    async def get_transfer(self, transfer_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve transfer session record by transfer_id."""
        await self.initialize()
        return await asyncio.to_thread(self._sync_get_transfer, transfer_id)

    # -------------------------------------------------------------------------
    # Files Table Operations
    # -------------------------------------------------------------------------

    def _sync_record_file(
        self,
        file_id: str,
        transfer_id: str,
        filename: str,
        file_path: str,
        size_bytes: int,
        total_chunks: int,
        chunk_size_bytes: int,
        sha256: str,
        highest_verified_chunk: int = -1,
        status: str = "in_progress",
    ) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO files (
                    file_id, transfer_id, filename, file_path, size_bytes,
                    total_chunks, chunk_size_bytes, sha256, highest_verified_chunk,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    highest_verified_chunk = excluded.highest_verified_chunk,
                    status = excluded.status,
                    updated_at = excluded.updated_at;
                """,
                (
                    file_id,
                    transfer_id,
                    filename,
                    str(file_path),
                    size_bytes,
                    total_chunks,
                    chunk_size_bytes,
                    sha256,
                    highest_verified_chunk,
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()

    async def record_file(
        self,
        file_id: str,
        transfer_id: str,
        filename: str,
        file_path: Union[str, Path],
        size_bytes: int,
        total_chunks: int,
        chunk_size_bytes: int,
        sha256: str,
        highest_verified_chunk: int = -1,
        status: str = "in_progress",
    ) -> None:
        """Record or update a file record belonging to a transfer."""
        await self.initialize()
        await asyncio.to_thread(
            self._sync_record_file,
            file_id,
            transfer_id,
            filename,
            str(file_path),
            size_bytes,
            total_chunks,
            chunk_size_bytes,
            sha256,
            highest_verified_chunk,
            status,
        )

    def _sync_update_file_checkpoint(self, file_id: str, highest_verified_chunk: int) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE files SET highest_verified_chunk = ?, updated_at = ? WHERE file_id = ?;",
                (highest_verified_chunk, now, file_id),
            )
            conn.commit()

    async def update_file_checkpoint(self, file_id: str, highest_verified_chunk: int) -> None:
        """Advance the durable highest_verified_chunk pointer for a file."""
        await self.initialize()
        await asyncio.to_thread(self._sync_update_file_checkpoint, file_id, highest_verified_chunk)

    def _sync_update_file_status(self, file_id: str, status: str) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE files SET status = ?, updated_at = ? WHERE file_id = ?;",
                (status, now, file_id),
            )
            conn.commit()

    async def update_file_status(self, file_id: str, status: str) -> None:
        """Update file status (e.g. 'completed', 'failed')."""
        await self.initialize()
        await asyncio.to_thread(self._sync_update_file_status, file_id, status)

    def _sync_get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM files WHERE file_id = ?;", (file_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    async def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve file record by file_id."""
        await self.initialize()
        return await asyncio.to_thread(self._sync_get_file, file_id)

    def _sync_get_file_by_transfer(self, transfer_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM files WHERE transfer_id = ? LIMIT 1;", (transfer_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    async def get_file_by_transfer(self, transfer_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve first file record associated with transfer_id."""
        await self.initialize()
        return await asyncio.to_thread(self._sync_get_file_by_transfer, transfer_id)

    # -------------------------------------------------------------------------
    # Chunk Hashes Operations
    # -------------------------------------------------------------------------

    def _sync_record_chunk_hash(self, file_id: str, chunk_index: int, sha256: str, verified: int) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO chunk_hashes (file_id, chunk_index, sha256, verified)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(file_id, chunk_index) DO UPDATE SET
                    sha256 = excluded.sha256,
                    verified = excluded.verified;
                """,
                (file_id, chunk_index, sha256, verified),
            )
            conn.commit()

    async def record_chunk_hash(self, file_id: str, chunk_index: int, sha256: str, verified: int = 0) -> None:
        """Insert or update chunk hash and verification status (0=read, 1=verified)."""
        await self.initialize()
        await asyncio.to_thread(self._sync_record_chunk_hash, file_id, chunk_index, sha256, verified)

    def _sync_record_chunk_hashes_batch(self, file_id: str, hashes: List[Tuple[int, str, int]]) -> None:
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO chunk_hashes (file_id, chunk_index, sha256, verified)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(file_id, chunk_index) DO UPDATE SET
                    sha256 = excluded.sha256,
                    verified = excluded.verified;
                """,
                [(file_id, idx, digest, v) for idx, digest, v in hashes],
            )
            conn.commit()

    async def record_chunk_hashes_batch(self, file_id: str, hashes: List[Tuple[int, str, int]]) -> None:
        """Batch-insert chunk hashes (e.g. from offer manifest or file scan)."""
        await self.initialize()
        await asyncio.to_thread(self._sync_record_chunk_hashes_batch, file_id, hashes)

    def _sync_get_chunk_hashes(self, file_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM chunk_hashes WHERE file_id = ? ORDER BY chunk_index ASC;",
                (file_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    async def get_chunk_hashes(self, file_id: str) -> List[Dict[str, Any]]:
        """Retrieve all chunk hashes for a file ordered by chunk_index."""
        await self.initialize()
        return await asyncio.to_thread(self._sync_get_chunk_hashes, file_id)

    def _sync_list_transfers(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM transfers ORDER BY created_at DESC;")
            return [dict(r) for r in cur.fetchall()]

    async def list_transfers(self) -> List[Dict[str, Any]]:
        """List all transfer sessions."""
        await self.initialize()
        return await asyncio.to_thread(self._sync_list_transfers)


def main() -> None:
    """CLI helper to inspect persisted transfer state in the local SQLite database."""
    import argparse
    from backend.config import get_settings

    parser = argparse.ArgumentParser(description="Inspect Pied Piper SQLite transfer state.")
    parser.add_argument(
        "--db",
        type=str,
        default=str(get_settings().sqlite_path),
        help="Path to SQLite database file",
    )
    args = parser.parse_args()

    store = TransferStateStore(db_path=args.db)
    asyncio.run(store.initialize())

    transfers = asyncio.run(store.list_transfers())
    print("\n" + "=" * 60)
    print(f"       Pied Piper SQLite State Store: {args.db}")
    print("=" * 60)
    print(f"Total Transfers: {len(transfers)}\n")

    for t in transfers:
        f = asyncio.run(store.get_file_by_transfer(t["transfer_id"]))
        print(f"Transfer ID: {t['transfer_id']} [{t['role'].upper()}] - Status: {t['status']}")
        if f:
            print(f"  File: {f['filename']} ({f['size_bytes']:,} bytes, {f['total_chunks']} chunks)")
            print(f"  Highest Verified Chunk: {f['highest_verified_chunk']} / {f['total_chunks'] - 1}")
            print(f"  Path: {f['file_path']}")
        print("-" * 60)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
