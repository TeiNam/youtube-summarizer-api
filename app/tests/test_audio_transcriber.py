"""음성 인식기 테스트 모듈

오디오 다운로드 실패, Transcribe 실패, 전체 성공 시나리오를 모킹하여 테스트한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.audio_transcriber import (
    _download_audio,
    _reject_live_or_too_long,
    _start_transcription_job,
    _upload_to_s3,
    _wait_for_transcription,
    transcribe_audio,
)


# =============================================================================
# 오디오 다운로드 실패 시나리오
# =============================================================================


class TestAudioDownloadFailure:
    """yt-dlp 오디오 다운로드 실패 테스트"""

    def test_download_raises_runtime_error_on_yt_dlp_failure(self) -> None:
        """yt-dlp 다운로드 중 예외 발생 시 RuntimeError를 발생시켜야 한다."""
        with patch("app.services.audio_transcriber.yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl.download.side_effect = Exception("네트워크 오류")
            mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
            mock_ydl.__exit__ = MagicMock(return_value=False)
            mock_ydl_cls.return_value = mock_ydl

            with pytest.raises(RuntimeError, match="오디오 다운로드 실패"):
                _download_audio("test_vid", "/tmp/test_output")

    def test_download_raises_when_file_not_found(self, tmp_path: object) -> None:
        """다운로드 후 파일이 존재하지 않으면 RuntimeError를 발생시켜야 한다."""
        output_path = str(tmp_path) + "/nonexistent"

        with patch("app.services.audio_transcriber.yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl.download.return_value = None  # 다운로드 성공한 척
            mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
            mock_ydl.__exit__ = MagicMock(return_value=False)
            mock_ydl_cls.return_value = mock_ydl

            with pytest.raises(RuntimeError, match="오디오 파일을 찾을 수 없습니다"):
                _download_audio("test_vid", output_path)

    @pytest.mark.asyncio
    async def test_transcribe_audio_raises_on_download_failure(self) -> None:
        """transcribe_audio에서 다운로드 실패 시 RuntimeError가 전파되어야 한다."""
        with patch(
            "app.services.audio_transcriber._download_audio",
            side_effect=RuntimeError("오디오 다운로드 실패: 네트워크 오류"),
        ):
            with pytest.raises(RuntimeError, match="오디오 다운로드 실패"):
                await transcribe_audio("test_vid")


# =============================================================================
# Transcribe 실패 시나리오
# =============================================================================


class TestTranscribeFailure:
    """AWS Transcribe 작업 실패 테스트"""

    def test_start_transcription_job_raises_on_boto3_error(self) -> None:
        """Transcribe 작업 시작 시 boto3 오류가 발생하면 RuntimeError를 발생시켜야 한다."""
        with patch("app.services.audio_transcriber.get_aws_client") as mock_boto:
            mock_client = MagicMock()
            mock_client.start_transcription_job.side_effect = Exception(
                "AWS 인증 오류"
            )
            mock_boto.return_value = mock_client

            with pytest.raises(RuntimeError, match="Transcribe 작업 시작 실패"):
                _start_transcription_job(
                    "test-job", "s3://bucket/audio/test.mp3"
                )

    @pytest.mark.asyncio
    async def test_wait_for_transcription_raises_on_failed_status(self) -> None:
        """Transcribe 작업 상태가 FAILED이면 RuntimeError를 발생시켜야 한다."""
        with patch("app.services.audio_transcriber.get_aws_client") as mock_boto:
            mock_client = MagicMock()
            mock_client.get_transcription_job.return_value = {
                "TranscriptionJob": {
                    "TranscriptionJobStatus": "FAILED",
                    "FailureReason": "잘못된 오디오 형식",
                }
            }
            mock_boto.return_value = mock_client

            with pytest.raises(RuntimeError, match="Transcribe 작업 실패"):
                await _wait_for_transcription("test-job")

    @pytest.mark.asyncio
    async def test_wait_for_transcription_raises_on_timeout(self) -> None:
        """Transcribe 작업이 타임아웃되면 RuntimeError를 발생시켜야 한다."""
        with (
            patch("app.services.audio_transcriber.get_aws_client") as mock_boto,
            patch("app.services.audio_transcriber.MAX_WAIT_TIME", 0.05),
            patch("app.services.audio_transcriber.POLL_INTERVAL", 0.01),
        ):
            mock_client = MagicMock()
            # 계속 IN_PROGRESS 상태를 반환하여 타임아웃 유도
            mock_client.get_transcription_job.return_value = {
                "TranscriptionJob": {
                    "TranscriptionJobStatus": "IN_PROGRESS",
                }
            }
            mock_boto.return_value = mock_client

            with pytest.raises(RuntimeError, match="Transcribe 작업 타임아웃"):
                await _wait_for_transcription("test-job")

    @pytest.mark.asyncio
    async def test_wait_for_transcription_honors_wall_clock_deadline(self) -> None:
        """AWS 호출이 느려도 MAX_WAIT_TIME 을 실제 상한으로 지켜야 한다.

        경과 시간을 POLL_INTERVAL 누적으로 세면 AWS 호출에 걸린 시간이 빠져
        상한을 한참 넘겨 대기한다(수정 전 동작). 벽시계 기준이면 넘지 않는다.
        """
        import time as _time

        with (
            patch("app.services.audio_transcriber.get_aws_client") as mock_boto,
            patch("app.services.audio_transcriber.MAX_WAIT_TIME", 0.2),
            patch("app.services.audio_transcriber.POLL_INTERVAL", 0.01),
        ):
            mock_client = MagicMock()

            def slow_poll(**_kwargs):
                # 폴링 간격(0.01s)보다 훨씬 오래 걸리는 AWS 호출을 흉내낸다
                _time.sleep(0.05)
                return {"TranscriptionJob": {"TranscriptionJobStatus": "IN_PROGRESS"}}

            mock_client.get_transcription_job.side_effect = slow_poll
            mock_boto.return_value = mock_client

            started = _time.monotonic()
            with pytest.raises(RuntimeError, match="Transcribe 작업 타임아웃"):
                await _wait_for_transcription("test-job")
            elapsed = _time.monotonic() - started

        # 상한 0.2s 를 크게 넘기지 않아야 한다 (마지막 폴링 1회분 여유만 허용)
        assert elapsed < 0.6, f"상한을 넘겨 대기함: {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_transcribe_audio_raises_on_transcribe_failure(self) -> None:
        """transcribe_audio에서 Transcribe 실패 시 RuntimeError가 전파되어야 한다."""
        with (
            patch(
                "app.services.audio_transcriber._download_and_upload_sync",
                return_value="s3://bucket/audio/test.mp3",
            ),
            patch(
                "app.services.audio_transcriber._start_transcription_job",
                side_effect=RuntimeError("Transcribe 작업 시작 실패: AWS 오류"),
            ),
            patch("app.services.audio_transcriber._delete_from_s3"),
        ):
            with pytest.raises(RuntimeError, match="Transcribe 작업 시작 실패"):
                await transcribe_audio("test_vid")


# =============================================================================
# 전체 성공 시나리오
# =============================================================================


def _fake_prepare(video_id, job_name, s3_key, uploaded_keys):
    """_download_and_upload_sync 대역 — 업로드 키를 실제 구현처럼 기록한다.

    호출자는 반환값이 아니라 uploaded_keys 를 보고 S3 정리를 결정한다
    (취소 시에도 삭제가 누락되지 않게 하기 위한 구조).
    """
    uploaded_keys.add(s3_key)
    return f"s3://bucket/{s3_key}"


class TestTranscribeAudioSuccess:
    """전체 음성 인식 파이프라인 성공 테스트 (모킹 활용)"""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self) -> None:
        """전체 파이프라인이 성공하면 변환된 텍스트를 반환해야 한다."""
        expected_text = "안녕하세요 이것은 테스트 음성입니다"

        with (
            patch(
                "app.services.audio_transcriber._download_and_upload_sync",
                side_effect=_fake_prepare,
            ) as mock_prepare,
            patch(
                "app.services.audio_transcriber._start_transcription_job",
            ) as mock_start,
            patch(
                "app.services.audio_transcriber._wait_for_transcription",
                new_callable=AsyncMock,
                return_value=expected_text,
            ) as mock_wait,
            patch("app.services.audio_transcriber._delete_from_s3") as mock_delete,
        ):
            result = await transcribe_audio("test_vid_id")

        assert result == expected_text
        mock_prepare.assert_called_once()
        mock_start.assert_called_once()
        mock_wait.assert_called_once()
        # 업로드한 오디오는 성공 후에도 삭제되어야 한다 (S3 무한 누적 방지)
        mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_uploaded_audio_deleted_on_failure(self) -> None:
        """Transcribe 실패 시에도 업로드한 S3 오디오를 삭제해야 한다."""
        with (
            patch(
                "app.services.audio_transcriber._download_and_upload_sync",
                side_effect=_fake_prepare,
            ),
            patch(
                "app.services.audio_transcriber._start_transcription_job",
                side_effect=RuntimeError("Transcribe 작업 시작 실패: AWS 오류"),
            ),
            patch("app.services.audio_transcriber._delete_from_s3") as mock_delete,
        ):
            with pytest.raises(RuntimeError, match="Transcribe 작업 시작 실패"):
                await transcribe_audio("test_vid")

        mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_s3_delete_when_upload_never_happened(self) -> None:
        """다운로드 단계에서 실패하면 S3 삭제를 시도하지 않아야 한다."""
        with (
            patch(
                "app.services.audio_transcriber._download_and_upload_sync",
                side_effect=RuntimeError("오디오 다운로드 실패: 네트워크 오류"),
            ),
            patch("app.services.audio_transcriber._delete_from_s3") as mock_delete,
        ):
            with pytest.raises(RuntimeError, match="오디오 다운로드 실패"):
                await transcribe_audio("test_vid")

        mock_delete.assert_not_called()

    def test_upload_to_s3_success(self) -> None:
        """S3 업로드 성공 시 S3 URI를 반환해야 한다."""
        with patch("app.services.audio_transcriber.get_aws_client") as mock_boto, \
             patch("app.services.audio_transcriber.S3_BUCKET_NAME", "youtube-summary-audio"):
            mock_client = MagicMock()
            mock_boto.return_value = mock_client

            result = _upload_to_s3("/tmp/test.mp3", "audio/test.mp3")

        assert result == "s3://youtube-summary-audio/audio/test.mp3"
        mock_client.upload_file.assert_called_once_with(
            "/tmp/test.mp3", "youtube-summary-audio", "audio/test.mp3"
        )

    def test_upload_to_s3_failure(self) -> None:
        """S3 업로드 실패 시 RuntimeError를 발생시켜야 한다."""
        with patch("app.services.audio_transcriber.get_aws_client") as mock_boto:
            mock_client = MagicMock()
            mock_client.upload_file.side_effect = Exception("S3 접근 거부")
            mock_boto.return_value = mock_client

            with pytest.raises(RuntimeError, match="S3 업로드 실패"):
                _upload_to_s3("/tmp/test.mp3", "audio/test.mp3")

    @pytest.mark.asyncio
    async def test_wait_for_transcription_success(self) -> None:
        """Transcribe 작업 완료 시 텍스트를 반환해야 한다."""
        transcript_text = "테스트 음성 인식 결과"

        with (
            patch("app.services.audio_transcriber.get_aws_client") as mock_boto,
            patch(
                "app.services.audio_transcriber._fetch_transcript_text",
                return_value=transcript_text,
            ),
        ):
            mock_client = MagicMock()
            mock_client.get_transcription_job.return_value = {
                "TranscriptionJob": {
                    "TranscriptionJobStatus": "COMPLETED",
                    "Transcript": {
                        "TranscriptFileUri": "https://s3.amazonaws.com/bucket/result.json"
                    },
                }
            }
            mock_boto.return_value = mock_client

            result = await _wait_for_transcription("test-job")

        assert result == transcript_text


# =============================================================================
# 다운로드 가드 (라이브·길이 상한)
# =============================================================================


class TestDownloadGuards:
    """yt-dlp match_filter 가 라이브·과도한 길이를 거르는지 검증"""

    def test_live_stream_rejected(self) -> None:
        """라이브 스트림은 거부되어야 한다 (다운로드가 끝나지 않는다)."""
        assert _reject_live_or_too_long({"is_live": True}) is not None
        assert _reject_live_or_too_long({"live_status": "is_live"}) is not None

    def test_too_long_video_rejected(self) -> None:
        """길이 상한을 넘는 영상은 거부되어야 한다."""
        with patch("app.services.audio_transcriber.MAX_AUDIO_DURATION", 600):
            assert _reject_live_or_too_long({"duration": 601}) is not None

    def test_normal_video_passes(self) -> None:
        """상한 이내의 일반 영상은 통과해야 한다."""
        with patch("app.services.audio_transcriber.MAX_AUDIO_DURATION", 600):
            assert _reject_live_or_too_long({"duration": 300}) is None
        # duration 을 알 수 없어도 통과시킨다 (yt-dlp 가 못 읽는 경우)
        assert _reject_live_or_too_long({}) is None
