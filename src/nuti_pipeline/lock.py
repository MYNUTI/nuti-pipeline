"""파일 락 — collect · build 가 동시에 두 개 돌지 않게 (Java 의 @Scheduled 락 대신).
프로세스가 죽으면 OS 가 락을 풀어 주므로 오래된 락 파일 문제가 없다. Windows 로컬 개발도 지원."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def exclusive(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise AlreadyRunning(f"다른 파이프라인 작업이 실행 중입니다 ({path})") from e
        yield
    finally:
        f.close()
