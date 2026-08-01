"""작업 관리자 모듈

인메모리 딕셔너리를 사용하여 비동기 요약 작업의 상태를 관리한다.
작업 생성, 조회, 상태 업데이트 기능을 제공한다.

TTL 과 개수 상한을 둔다 — 완료된 작업이 전체 번역문을 들고 영구히 남으면
프로세스 메모리가 요청 수만큼 증가해 결국 OOM 으로 죽는다.

ponytail: 단일 컨테이너 배포라 인메모리로 충분하다. 프로세스가 죽으면 진행 중인
작업은 유실되고, 멀티 워커에서는 워커별로 상태가 갈린다(그래서 --workers 1 이다).
영속성·워커 확장이 필요해지면 Redis 로 옮긴다 — 인터페이스는 그대로 두면 된다.
"""

import logging
import os
import threading
import time
import uuid
from typing import Optional

from app.models.responses import TaskStatus

logger = logging.getLogger(__name__)

# 완료·실패 작업을 보관하는 시간(초). 조회는 그 안에 하면 된다.
TASK_TTL_SECONDS = int(os.environ.get("TASK_TTL_SECONDS", "3600"))
# 작업 총 개수 상한. 넘으면 가장 오래된 종료 작업부터 버린다.
MAX_TASKS = int(os.environ.get("TASK_MAX_ENTRIES", "1000"))

# 더 이상 진행되지 않는 상태 — TTL·상한 정리 대상
_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})


class TaskRejectedError(RuntimeError):
    """상한이 가득 차 새 작업을 받을 수 없을 때 발생한다.

    종료 작업을 정리해도 자리가 없다는 뜻이다 — 즉 진행 중·대기 중 작업이 상한만큼
    쌓여 있다. 접수를 거절해야 한다. 여기서 막지 않으면 202 를 계속 내주면서
    대기 큐와 메모리가 무제한 늘어난다(요청 속도 > 처리 속도인 동안).
    """


class TaskManager:
    """인메모리 작업 상태 관리자

    작업을 생성하고, 상태를 업데이트하며, 작업 정보를 조회하는 기능을 제공한다.
    TTL 이 지난 종료 작업과 상한을 넘긴 오래된 작업은 자동으로 제거된다.

    파이프라인은 스레드풀에서 도는 코드와 이벤트 루프 양쪽에서 이 객체를 만지므로
    모든 접근을 락으로 감싼다.
    """

    def __init__(self) -> None:
        """작업 저장소 초기화"""
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create_task(self, url: str, target_language: str) -> str:
        """새로운 요약 작업을 생성한다.

        Args:
            url: 유튜브 영상 URL
            target_language: 번역 대상 언어 코드

        Returns:
            생성된 작업의 고유 ID (UUID)

        Raises:
            TaskRejectedError: 상한이 가득 차 받을 수 없는 경우
        """
        task_id = str(uuid.uuid4())
        with self._lock:
            # 새 작업 자리를 만들 만큼 정리한 뒤(MAX_TASKS-1 까지) 자리가 있는지 본다.
            # 자리가 없으면 접수를 거절한다 — 작업 생성은 세마포어 획득보다 먼저
            # 일어나므로, 여기서 막지 않으면 요청 속도가 처리 속도를 넘는 동안
            # PENDING 이 무제한 쌓인다(실측: 상한 5 인데 200건 누적).
            self._evict_locked(target=MAX_TASKS - 1)
            if len(self._tasks) >= MAX_TASKS:
                raise TaskRejectedError(
                    f"처리 대기 작업이 상한({MAX_TASKS})에 도달했습니다"
                )

            self._tasks[task_id] = {
                "task_id": task_id,
                "url": url,
                "target_language": target_language,
                "status": TaskStatus.PENDING,
                "result": None,
                "error": None,
                "created_at": time.monotonic(),
                "finished_at": None,
            }
        return task_id

    def get_task(self, task_id: str) -> Optional[dict]:
        """작업 ID로 작업 정보를 조회한다.

        Args:
            task_id: 조회할 작업의 고유 ID

        Returns:
            작업 정보 딕셔너리의 사본 (task_id, status, result, error 포함).
            등록되지 않았거나 TTL 이 지나 정리된 작업 ID면 None.
        """
        with self._lock:
            self._evict_locked()
            task = self._tasks.get(task_id)
            # 사본을 준다 — 호출자가 받은 dict 를 파이프라인이 동시에 고치면 안 된다
            return dict(task) if task is not None else None

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        """작업의 상태를 업데이트한다.

        Args:
            task_id: 업데이트할 작업의 고유 ID
            status: 새로운 작업 상태
            result: 완료 시 요약 결과 (선택)
            error: 실패 시 오류 메시지 (선택)
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task["status"] = status
            if result is not None:
                task["result"] = result
            if error is not None:
                task["error"] = error
            # 종료 상태가 되는 시점을 기록한다 — TTL 기준점
            if status in _TERMINAL_STATUSES and task["finished_at"] is None:
                task["finished_at"] = time.monotonic()

    def _evict_locked(self, target: Optional[int] = None) -> None:
        """TTL 이 지난 종료 작업과 상한 초과분을 제거한다 (락 보유 상태에서 호출).

        진행 중인 작업은 TTL 로도 상한으로도 지우지 않는다 — Transcribe 폴백은 10분
        이상 걸릴 수 있고, 지우면 파이프라인은 계속 도는데 update_status 가 무시되고
        조회는 영구 404 가 된다(202 를 받은 사용자가 결과를 영원히 못 받는다).
        상한이 가득 차면 여기서 지우는 대신 create_task 가 접수를 거절한다.

        Args:
            target: 이 개수 이하로 줄인다. 생략하면 MAX_TASKS 를 쓴다.
                    create_task 는 새 작업 자리를 만들려고 MAX_TASKS-1 을 넘긴다.
        """
        limit = MAX_TASKS if target is None else target
        now = time.monotonic()

        expired = [
            tid
            for tid, t in self._tasks.items()
            if t["finished_at"] is not None and now - t["finished_at"] > TASK_TTL_SECONDS
        ]
        for tid in expired:
            del self._tasks[tid]

        if len(self._tasks) <= limit:
            return

        # 상한 초과 — 종료된 작업만 오래된 순으로 버린다.
        finished = sorted(
            (t for t in self._tasks.values() if t["finished_at"] is not None),
            key=lambda t: t["finished_at"],
        )
        for task in finished:
            if len(self._tasks) <= limit:
                return
            del self._tasks[task["task_id"]]

        logger.warning(
            "종료되지 않은 작업이 많아 상한(%d)에 도달했습니다: 현재 %d건",
            MAX_TASKS,
            len(self._tasks),
        )
