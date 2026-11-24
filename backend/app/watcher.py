"""Watched inbox folder: new PNG/WebP/JPG drops (e.g. Inkarnate exports) are animated + imported."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

log = logging.getLogger("vtt.inbox")

IMAGE_EXTS = {".png", ".webp", ".jpg", ".jpeg"}


def _is_candidate(path: Path) -> bool:
    return (
        path.suffix.lower() in IMAGE_EXTS
        and not path.name.startswith(".")
        and not path.name.endswith((".part", ".tmp", ".crdownload"))
    )


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "InboxWatcher"):
        self.watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.watcher.notify(Path(str(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.watcher.notify(Path(str(event.dest_path)))


class InboxWatcher:
    def __init__(self, inbox: Path, on_file: Callable[[Path], Awaitable[None]], polling: bool = False):
        self.inbox = inbox
        self.on_file = on_file
        self.polling = polling
        self._observer = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: set[Path] = set()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.inbox.mkdir(parents=True, exist_ok=True)
        observer = PollingObserver(timeout=2) if self.polling else Observer()
        observer.schedule(_Handler(self), str(self.inbox), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer
        log.info("Watching %s for new maps (%s)", self.inbox, "polling" if self.polling else "native events")
        # Pick up anything dropped while the server was down.
        for p in sorted(self.inbox.iterdir()):
            if p.is_file():
                self.notify(p)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=3)

    def notify(self, path: Path) -> None:
        """Called from the watchdog thread."""
        if not _is_candidate(path) or path in self._pending or self._loop is None:
            return
        self._pending.add(path)
        asyncio.run_coroutine_threadsafe(self._process(path), self._loop)

    async def _process(self, path: Path) -> None:
        try:
            if not await self._wait_stable(path):
                return
            await self.on_file(path)
        except Exception:  # noqa: BLE001
            log.exception("Inbox processing failed for %s", path)
        finally:
            self._pending.discard(path)

    @staticmethod
    async def _wait_stable(path: Path, timeout: float = 120) -> bool:
        """Wait until the file stops growing (large 4K exports copy slowly over SMB)."""
        start = time.monotonic()
        last = -1
        stable = 0
        while time.monotonic() - start < timeout:
            try:
                size = os.path.getsize(path)
            except OSError:
                return False
            if size > 0 and size == last:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
            last = size
            await asyncio.sleep(0.75)
        return False
