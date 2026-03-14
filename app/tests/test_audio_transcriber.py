"""음성 인식기 테스트 모듈

오디오 다운로드 실패, Transcribe 실패, 전체 성공 시나리오를 모킹하여 테스트한다.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from app.services.audio_transcriber import (
    _download_audio,
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
        with patch("app.services.audio_transcriber.boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.start_transcription_job.side_effect = Exception(
                "AWS 인증 오류"
            )
            mock_boto.return_value = mock_client

            with pytest.raises(RuntimeError, match="Transcribe 작업 시작 실패"):
                _start_transcription_job(
                    "test-job", "s3://bucket/audio/test.mp3"
                )

    def test_wait_for_transcription_raises_on_failed_status(self) -> None:
        """Transcribe 작업 상태가 FAILED이면 RuntimeError를 발생시켜야 한다."""
        with patch("app.services.audio_transcriber.boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.get_transcription_job.return_value = {
                "TranscriptionJob": {
                    "TranscriptionJobStatus": "FAILED",
                    "FailureReason": "잘못된 오디오 형식",
                }
            }
            mock_boto.return_value = mock_client

            with pytest.raises(RuntimeError, match="Transcribe 작업 실패"):
                _wait_for_transcription("test-job")

    def test_wait_for_transcription_raises_on_timeout(self) -> None:
        """Transcribe 작업이 타임아웃되면 RuntimeError를 발생시켜야 한다."""
        with (
            patch("app.services.audio_transcriber.boto3.client") as mock_boto,
            patch("app.services.audio_transcriber.time.sleep"),
            patch("app.services.audio_transcriber.MAX_WAIT_TIME", 10),
            patch("app.services.audio_transcriber.POLL_INTERVAL", 5),
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
                _wait_for_transcription("test-job")

    @pytest.mark.asyncio
    async def test_transcribe_audio_raises_on_transcribe_failure(self) -> None:
        """transcribe_audio에서 Transcribe 실패 시 RuntimeError가 전파되어야 한다."""
        with (
            patch(
                "app.services.audio_transcriber._download_audio",
                return_value="/tmp/test.mp3",
            ),
            patch(
                "app.services.audio_transcriber._upload_to_s3",
                return_value="s3://bucket/audio/test.mp3",
            ),
            patch(
                "app.services.audio_transcriber._start_transcription_job",
                side_effect=RuntimeError("Transcribe 작업 시작 실패: AWS 오류"),
            ),
            patch("app.services.audio_transcriber.os.path.exists", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="Transcribe 작업 시작 실패"):
                await transcribe_audio("test_vid")


# =============================================================================
# 전체 성공 시나리오
# =============================================================================


class TestTranscribeAudioSuccess:
    """전체 음성 인식 파이프라인 성공 테스트 (모킹 활용)"""

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self) -> None:
        """전체 파이프라인이 성공하면 변환된 텍스트를 반환해야 한다."""
        expected_text = "안녕하세요 이것은 테스트 음성입니다"

        with (
            patch(
                "app.services.audio_transcriber._download_audio",
                return_value="/tmp/test-audio.mp3",
            ) as mock_download,
            patch(
                "app.services.audio_transcriber._upload_to_s3",
                return_value="s3://bucket/audio/test.mp3",
            ) as mock_upload,
            patch(
                "app.services.audio_transcriber._start_transcription_job",
            ) as mock_start,
            patch(
                "app.services.audio_transcriber._wait_for_transcription",
                return_value=expected_text,
            ) as mock_wait,
            patch("app.services.audio_transcriber.os.path.exists", return_value=False),
        ):
            result = await transcribe_audio("test_vid_id")

        assert result == expected_text
        mock_download.assert_called_once()
        mock_upload.assert_called_once()
        mock_start.assert_called_once()
        mock_wait.assert_called_once()

    def test_upload_to_s3_success(self) -> None:
        """S3 업로드 성공 시 S3 URI를 반환해야 한다."""
        with patch("app.services.audio_transcriber.boto3.client") as mock_boto, \
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
        with patch("app.services.audio_transcriber.boto3.client") as mock_boto:
            mock_client = MagicMock()
            mock_client.upload_file.side_effect = Exception("S3 접근 거부")
            mock_boto.return_value = mock_client

            with pytest.raises(RuntimeError, match="S3 업로드 실패"):
                _upload_to_s3("/tmp/test.mp3", "audio/test.mp3")

    def test_wait_for_transcription_success(self) -> None:
        """Transcribe 작업 완료 시 텍스트를 반환해야 한다."""
        transcript_text = "테스트 음성 인식 결과"

        with (
            patch("app.services.audio_transcriber.boto3.client") as mock_boto,
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

            result = _wait_for_transcription("test-job")

        assert result == transcript_text
