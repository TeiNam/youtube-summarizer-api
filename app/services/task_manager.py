"""작업 관리자 모듈

인메모리 딕셔너리를 사용하여 비동기 요약 작업의 상태를 관리한다.
작업 생성, 조회, 상태 업데이트 기능을 제공한다.
"""

import uuid
from typing import Optional

from app.models.responses import TaskStatus


class TaskManager:
    """인메모리 작업 상태 관리자

    작업을 생성하고, 상태를 업데이트하며, 작업 정보를 조회하는 기능을 제공한다.
    모든 작업 데이터는 인메모리 딕셔너리에 저장된다.
    """

    def __init__(self) -> None:
        """작업 저장소 초기화"""
        self._tasks: dict[str, dict] = {}

    def create_task(self, url: str, target_language: str) -> str:
        """새로운 요약 작업을 생성한다.

        Args:
            url: 유튜브 영상 URL
            target_language: 번역 대상 언어 코드

        Returns:
            생성된 작업의 고유 ID (UUID)
        """
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {
            "task_id": task_id,
            "url": url,
            "target_language": target_language,
            "status": TaskStatus.PENDING,
            "result": None,
            "error": None,
        }
        return task_id

    def get_task(self, task_id: str) -> Optional[dict]:
        """작업 ID로 작업 정보를 조회한다.

        Args:
            task_id: 조회할 작업의 고유 ID

        Returns:
            작업 정보 딕셔너리 (task_id, status, result, error 포함).
            등록되지 않은 작업 ID인 경우 None 반환.
        """
        return self._tasks.get(task_id)

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
        task = self._tasks.get(task_id)
        if task is not None:
            task["status"] = status
            if result is not None:
                task["result"] = result
            if error is not None:
                task["error"] = error
