"""Get bundles to the FHIR server without losing or duplicating any.

Bundles go into a SQLite outbox first and are deleted only after the server
accepts them. A crash, a network drop or a server restart costs a delay, not
data. Because every resource in a bundle is a conditional create, resending
one the server already stored is harmless.

Failures are sorted into two kinds:
* retryable (network error, 408, 429, 5xx): stop, keep order, try later;
* rejected (other 4xx): the bundle itself is wrong and will never succeed.
  It moves to a dead-letter table with the server's OperationOutcome, so one
  bad bundle cannot block everything queued behind it.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
from dataclasses import dataclass

import httpx

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class Rejected(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"server rejected bundle with HTTP {status}")
        self.status, self.body = status, body


class Unavailable(Exception):
    pass


class FhirClient:
    # @implements SRS-014
    def __init__(
        self, base_url: str, token: str | None = None, timeout: float = 15.0, transport: httpx.BaseTransport | None = None
    ):
        headers = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # transactions go to the base URL itself, exactly as given (no trailing slash added)
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(headers=headers, timeout=timeout, transport=transport)

    # @implements SRS-011, SRS-013
    def transaction(self, bundle: dict, attempts: int = 4, base_delay: float = 0.5, sleep=time.sleep) -> dict:
        """POST a transaction bundle. Retries transient failures with backoff."""
        for attempt in range(attempts):
            try:
                r = self._http.post(self._base, content=json.dumps(bundle))
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if r.status_code < 300:
                    return r.json()
                if r.status_code not in RETRYABLE_STATUS:
                    raise Rejected(r.status_code, r.text)
                last = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep(min(int(retry_after), 60))
                    continue
            if attempt < attempts - 1:
                sleep(base_delay * 2**attempt * (1 + random.random() * 0.25))
        raise Unavailable(last)

    def close(self) -> None:
        self._http.close()


@dataclass
class FlushReport:
    sent: int = 0
    rejected: int = 0
    pending: int = 0
    stopped_on: str | None = None


# @implements SRS-012
class Outbox:
    def __init__(self, path: str = "outbox.sqlite3"):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bundle TEXT NOT NULL,
                created REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS dead_letter (
                id INTEGER PRIMARY KEY,
                bundle TEXT NOT NULL,
                status INTEGER NOT NULL,
                response TEXT,
                moved REAL NOT NULL
            );
        """)

    def put(self, bundle: dict) -> None:
        with self.db:
            self.db.execute("INSERT INTO outbox (bundle, created) VALUES (?, ?)", (json.dumps(bundle), time.time()))

    def pending(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def dead(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM dead_letter").fetchone()[0]

    # @implements SRS-012, SRS-013
    def flush(self, client: FhirClient, **retry) -> FlushReport:
        """Send queued bundles oldest first; stop at the first retryable failure."""
        report = FlushReport()
        rows = self.db.execute("SELECT id, bundle FROM outbox ORDER BY id").fetchall()
        for row_id, text in rows:
            try:
                client.transaction(json.loads(text), **retry)
            except Unavailable as e:
                with self.db:
                    self.db.execute("UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?", (str(e), row_id))
                report.stopped_on = str(e)
                break
            except Rejected as e:
                with self.db:
                    self.db.execute(
                        "INSERT INTO dead_letter (id, bundle, status, response, moved) VALUES (?, ?, ?, ?, ?)",
                        (row_id, text, e.status, e.body[:20000], time.time()),
                    )
                    self.db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
                report.rejected += 1
            else:
                with self.db:
                    self.db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
                report.sent += 1
        report.pending = self.pending()
        return report

    def close(self) -> None:
        self.db.close()
