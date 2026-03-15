"""음성 인식 모듈

yt-dlp로 유튜브 영상의 오디오를 다운로드하고,
AWS S3에 업로드한 뒤 AWS Transcribe를 사용하여 텍스트로 변환한다.
자막이 없는 영상에 대한 폴백 처리를 담당한다.

동기 I/O 호출(yt-dlp, boto3)은 run_in_executor로 스레드풀에서 실행하여
이벤트 루프 블로킹을 방지한다.
"""

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from functools import partial

import boto3
import yt_dlp

from app.services.aws_client import get_aws_client

logger = logging.getLogger(__name__)

# AWS 설정 (환경변수에서 로드)
S3_BUCKET_NAME = os.environ.get("TRANSCRIBE_S3_BUCKET", "youtube-summary-audio")

# Transcribe 작업 폴링 간격 (초)
POLL_INTERVAL = int(os.environ.get("TRANSCRIBE_POLL_INTERVAL", "5"))
# Transcribe 작업 최대 대기 시간 (초)
MAX_WAIT_TIME = int(os.environ.get("TRANSCRIBE_MAX_WAIT_TIME", "600"))


def _download_audio(video_id: str, output_path: str) -> str:
    """yt-dlp를 사용하여 유튜브 영상의 오디오를 다운로드한다.

    Args:
        video_id: 유튜브 비디오 ID
        output_path: 오디오 파일 저장 경로 (확장자 제외)

    Returns:
        다운로드된 오디오 파일의 전체 경로

    Raises:
        RuntimeError: 오디오 다운로드 실패 시
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.error("비디오 %s: 오디오 다운로드 실패 - %s", video_id, e)
        raise RuntimeError(f"오디오 다운로드 실패: {e}") from e

    # yt-dlp 후처리로 mp3 확장자가 붙음
    audio_file = f"{output_path}.mp3"
    if not os.path.exists(audio_file):
        raise RuntimeError(f"오디오 파일을 찾을 수 없습니다: {audio_file}")

    return audio_file


def _upload_to_s3(file_path: str, s3_key: str) -> str:
    """오디오 파일을 S3 버킷에 업로드한다.

    Args:
        file_path: 로컬 오디오 파일 경로
        s3_key: S3 객체 키

    Returns:
        업로드된 S3 URI (s3://bucket/key)

    Raises:
        RuntimeError: S3 업로드 실패 시
    """
    try:
        s3_client = get_aws_client("s3")
        s3_client.upload_file(file_path, S3_BUCKET_NAME, s3_key)
        s3_uri = f"s3://{S3_BUCKET_NAME}/{s3_key}"
        logger.info("S3 업로드 완료: %s", s3_uri)
        return s3_uri
    except Exception as e:
        logger.error("S3 업로드 실패: %s", e)
        raise RuntimeError(f"S3 업로드 실패: {e}") from e


def _start_transcription_job(job_name: str, s3_uri: str) -> None:
    """AWS Transcribe 작업을 시작한다.

    Args:
        job_name: Transcribe 작업 이름 (고유해야 함)
        s3_uri: 오디오 파일의 S3 URI

    Raises:
        RuntimeError: Transcribe 작업 시작 실패 시
    """
    try:
        transcribe_client = get_aws_client("transcribe")
        transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": s3_uri},
            MediaFormat="mp3",
            IdentifyLanguage=True,
        )
        logger.info("Transcribe 작업 시작: %s", job_name)
    except Exception as e:
        logger.error("Transcribe 작업 시작 실패: %s", e)
        raise RuntimeError(f"Transcribe 작업 시작 실패: {e}") from e


def _wait_for_transcription(job_name: str) -> str:
    """Transcribe 작업 완료를 대기하고 결과 텍스트를 반환한다.

    폴링 방식으로 작업 상태를 확인하며, 완료 시 변환된 텍스트를 반환한다.

    Args:
        job_name: Transcribe 작업 이름

    Returns:
        변환된 텍스트

    Raises:
        RuntimeError: 작업 실패 또는 타임아웃 시
    """
    transcribe_client = get_aws_client("transcribe")
    elapsed = 0

    while elapsed < MAX_WAIT_TIME:
        response = transcribe_client.get_transcription_job(
            TranscriptionJobName=job_name
        )
        status = response["TranscriptionJob"]["TranscriptionJobStatus"]

        if status == "COMPLETED":
            # 결과 URI에서 텍스트 추출
            transcript_uri = response["TranscriptionJob"]["Transcript"][
                "TranscriptFileUri"
            ]
            return _fetch_transcript_text(transcript_uri)

        if status == "FAILED":
            failure_reason = response["TranscriptionJob"].get(
                "FailureReason", "알 수 없는 오류"
            )
            raise RuntimeError(f"Transcribe 작업 실패: {failure_reason}")

        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    raise RuntimeError(
        f"Transcribe 작업 타임아웃: {MAX_WAIT_TIME}초 초과"
    )


def _fetch_transcript_text(transcript_uri: str) -> str:
    """Transcribe 결과 URI에서 텍스트를 가져온다.

    AWS Transcribe는 결과를 JSON 파일로 S3에 저장한다.
    해당 JSON에서 변환된 텍스트를 추출하여 반환한다.

    Args:
        transcript_uri: Transcribe 결과 JSON의 URI

    Returns:
        변환된 텍스트

    Raises:
        RuntimeError: 결과 파싱 실패 시
    """
    try:
        # Transcribe 결과는 S3에 저장되므로 boto3로 가져옴
        # URI 형식: https://s3.{region}.amazonaws.com/{bucket}/{key}
        import urllib.request

        with urllib.request.urlopen(transcript_uri) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        transcripts = result.get("results", {}).get("transcripts", [])
        if not transcripts:
            raise RuntimeError("Transcribe 결과에 텍스트가 없습니다")

        text = " ".join(t["transcript"] for t in transcripts)
        return text
    except RuntimeError:
        raise
    except Exception as e:
        logger.error("Transcribe 결과 파싱 실패: %s", e)
        raise RuntimeError(f"Transcribe 결과 파싱 실패: {e}") from e


def _transcribe_audio_sync(video_id: str) -> str:
    """유튜브 영상의 오디오를 다운로드하고 음성 인식으로 텍스트를 추출한다 (동기 버전).

    스레드풀에서 실행되며, 전체 흐름을 동기적으로 처리한다.

    Args:
        video_id: 유튜브 비디오 ID

    Returns:
        변환된 텍스트
    """
    # 고유한 작업 이름 생성
    job_name = f"yt-{video_id}-{uuid.uuid4().hex[:8]}"
    s3_key = f"audio-summary/{job_name}.mp3"
    temp_dir = None
    audio_file = None

    try:
        # 1. 임시 디렉토리에 오디오 다운로드
        temp_dir = tempfile.mkdtemp()
        output_path = os.path.join(temp_dir, job_name)
        logger.info("비디오 %s: 오디오 다운로드 시작", video_id)
        audio_file = _download_audio(video_id, output_path)

        # 2. S3에 업로드
        logger.info("비디오 %s: S3 업로드 시작", video_id)
        s3_uri = _upload_to_s3(audio_file, s3_key)

        # 3. Transcribe 작업 시작
        logger.info("비디오 %s: Transcribe 작업 시작", video_id)
        _start_transcription_job(job_name, s3_uri)

        # 4. 작업 완료 대기 및 결과 반환
        logger.info("비디오 %s: Transcribe 작업 완료 대기 중", video_id)
        text = _wait_for_transcription(job_name)
        logger.info("비디오 %s: 음성 인식 완료", video_id)
        return text

    finally:
        # 5. 임시 파일 정리
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                logger.debug("임시 오디오 파일 삭제: %s", audio_file)
            except OSError:
                logger.warning("임시 오디오 파일 삭제 실패: %s", audio_file)
        if temp_dir and os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
                logger.debug("임시 디렉토리 삭제: %s", temp_dir)
            except OSError:
                logger.warning("임시 디렉토리 삭제 실패: %s", temp_dir)


async def transcribe_audio(video_id: str) -> str:
    """유튜브 영상의 오디오를 다운로드하고 음성 인식으로 텍스트를 추출한다.

    동기 I/O를 스레드풀에서 실행하여 이벤트 루프를 블로킹하지 않는다.

    Args:
        video_id: 유튜브 비디오 ID

    Returns:
        변환된 텍스트

    Raises:
        RuntimeError: 오디오 다운로드, S3 업로드, Transcribe 처리 실패 시
    """
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, partial(_transcribe_audio_sync, video_id)
        )
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(
            "비디오 %s: 음성 인식 중 예상치 못한 오류 - %s",
            video_id,
            e,
            exc_info=True,
        )
        raise RuntimeError(f"음성 인식 실패: {e}") from e
