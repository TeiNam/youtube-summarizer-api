"""음성 인식 모듈

yt-dlp로 유튜브 영상의 오디오를 다운로드하고,
AWS S3에 업로드한 뒤 AWS Transcribe를 사용하여 텍스트로 변환한다.
자막이 없는 영상에 대한 폴백 처리를 담당한다.

동기 I/O 호출(yt-dlp, boto3)은 run_in_executor로 스레드풀에서 실행하여
이벤트 루프 블로킹을 방지한다. 단 폴링 대기는 스레드를 잡지 않는다 —
아래 transcribe_audio 의 주석 참고.
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import yt_dlp

from app.services.aws_client import get_aws_client

logger = logging.getLogger(__name__)

# AWS 설정 (환경변수에서 로드)
S3_BUCKET_NAME = os.environ.get("TRANSCRIBE_S3_BUCKET", "youtube-summary-audio")

# Transcribe 작업 폴링 간격 (초)
POLL_INTERVAL = int(os.environ.get("TRANSCRIBE_POLL_INTERVAL", "5"))
# Transcribe 작업 최대 대기 시간 (초)
MAX_WAIT_TIME = int(os.environ.get("TRANSCRIBE_MAX_WAIT_TIME", "600"))

# 다운로드할 영상 길이 상한(초). 넘으면 받지 않는다 — 3시간 영상 하나가
# 디스크·ffmpeg CPU·Transcribe 비용을 모두 끌어올린다.
MAX_AUDIO_DURATION = int(os.environ.get("TRANSCRIBE_MAX_DURATION", "7200"))

# Transcribe 결과 JSON 다운로드 타임아웃(초)과 크기 상한(바이트).
# timeout 이 없으면 소켓이 응답을 안 줄 때 스레드가 무기한 점유된다.
TRANSCRIPT_FETCH_TIMEOUT = 30
TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024

# 무거운 블로킹 작업(yt-dlp 다운로드 + ffmpeg 변환 + S3 업로드) 전용 스레드풀.
# 기본 executor 를 쓰면 자막 추출·Bedrock 호출과 스레드를 다투다가
# 폴백 몇 건으로 API 전체가 정체된다(실측: 0.5s 작업이 4.2s).
_DOWNLOAD_WORKERS = int(os.environ.get("TRANSCRIBE_DOWNLOAD_WORKERS", "2"))
_download_executor = ThreadPoolExecutor(
    max_workers=_DOWNLOAD_WORKERS, thread_name_prefix="yts-audio"
)


def _reject_live_or_too_long(info: dict, *, incomplete: bool = False):
    """yt-dlp match_filter — 라이브 스트림과 과도하게 긴 영상을 거른다.

    None 을 반환하면 통과, 문자열을 반환하면 그 이유로 다운로드가 거부된다.
    라이브는 끝이 없어 다운로드가 종료되지 않고, 긴 영상 하나가 디스크·ffmpeg
    CPU·Transcribe 비용을 함께 끌어올린다.
    """
    if info.get("is_live") or info.get("live_status") in ("is_live", "post_live"):
        return "라이브 스트림은 음성 인식 대상이 아닙니다"
    duration = info.get("duration")
    if duration and duration > MAX_AUDIO_DURATION:
        return f"영상이 너무 깁니다({duration}초 > 상한 {MAX_AUDIO_DURATION}초)"
    return None


def _download_audio(video_id: str, output_path: str) -> str:
    """yt-dlp를 사용하여 유튜브 영상의 오디오를 다운로드한다.

    MAX_AUDIO_DURATION 을 넘는 영상과 라이브 스트림은 받지 않는다.

    Args:
        video_id: 유튜브 비디오 ID
        output_path: 오디오 파일 저장 경로 (확장자 제외)

    Returns:
        다운로드된 오디오 파일의 전체 경로

    Raises:
        RuntimeError: 오디오 다운로드 실패 또는 길이·라이브 제한에 걸린 경우
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
        "match_filter": _reject_live_or_too_long,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.error("비디오 %s: 오디오 다운로드 실패 - %s", video_id, e)
        raise RuntimeError(f"오디오 다운로드 실패: {e}") from e

    # yt-dlp 후처리로 mp3 확장자가 붙음.
    # match_filter 로 걸러진 경우 yt-dlp 는 예외 없이 종료하므로 여기서 잡힌다.
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


def _delete_from_s3(s3_key: str) -> None:
    """업로드한 오디오 객체를 S3에서 삭제한다 (실패해도 예외를 던지지 않는다).

    Transcribe 가 읽고 나면 원본 오디오는 쓸 데가 없다. 지우지 않으면
    요청마다 mp3 가 버킷에 영구 누적된다.
    """
    try:
        get_aws_client("s3").delete_object(Bucket=S3_BUCKET_NAME, Key=s3_key)
        logger.debug("S3 오디오 삭제: s3://%s/%s", S3_BUCKET_NAME, s3_key)
    except Exception as e:
        # 삭제 실패로 요약을 실패시킬 이유는 없다 — 경고만 남긴다
        logger.warning("S3 오디오 삭제 실패 (s3://%s/%s): %s", S3_BUCKET_NAME, s3_key, e)


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


def _poll_transcription_once(job_name: str) -> tuple[str, str | None]:
    """Transcribe 작업 상태를 한 번 조회한다.

    Returns:
        (상태, 완료 시 결과 URI). 미완료면 URI 는 None.

    Raises:
        RuntimeError: 작업이 FAILED 상태일 때
    """
    response = get_aws_client("transcribe").get_transcription_job(
        TranscriptionJobName=job_name
    )
    job = response["TranscriptionJob"]
    status = job["TranscriptionJobStatus"]

    if status == "COMPLETED":
        return status, job["Transcript"]["TranscriptFileUri"]

    if status == "FAILED":
        failure_reason = job.get("FailureReason", "알 수 없는 오류")
        raise RuntimeError(f"Transcribe 작업 실패: {failure_reason}")

    return status, None


async def _wait_for_transcription(job_name: str) -> str:
    """Transcribe 작업 완료를 대기하고 결과 텍스트를 반환한다.

    대기 구간을 asyncio.sleep 으로 처리해 폴링 중에는 스레드를 점유하지 않는다.
    AWS 호출과 결과 다운로드만 짧게 executor 를 쓴다. 이게 없으면 폴백 한 건이
    최대 MAX_WAIT_TIME 동안 스레드 하나를 붙잡아 API 전체가 정체된다(실측).

    경과 시간은 벽시계로 잰다 — POLL_INTERVAL 만 누적하면 AWS 호출에 걸린
    시간이 빠져 MAX_WAIT_TIME 이 실제 상한이 되지 않는다.

    Args:
        job_name: Transcribe 작업 이름

    Returns:
        변환된 텍스트

    Raises:
        RuntimeError: 작업 실패 또는 타임아웃 시
    """
    loop = asyncio.get_running_loop()
    deadline = time.monotonic() + MAX_WAIT_TIME

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"Transcribe 작업 타임아웃: {MAX_WAIT_TIME}초 초과")

        # 폴링 호출 자체에도 남은 시간을 씌운다 — executor 대기나 느린 AWS 응답으로
        # 호출 안에서 상한을 넘겨 버리면 벽시계 deadline 이 무의미해진다.
        try:
            _status, transcript_uri = await asyncio.wait_for(
                loop.run_in_executor(None, partial(_poll_transcription_once, job_name)),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Transcribe 작업 타임아웃: {MAX_WAIT_TIME}초 초과"
            ) from None

        if transcript_uri is not None:
            # 결과 다운로드는 자체 타임아웃(TRANSCRIPT_FETCH_TIMEOUT)이 있고, 여기까지
            # 왔으면 작업은 이미 끝났으므로 남은 시간으로 자르지 않는다.
            return await loop.run_in_executor(
                None, partial(_fetch_transcript_text, transcript_uri)
            )

        # 남은 시간보다 길게 자지 않는다
        await asyncio.sleep(min(POLL_INTERVAL, max(0.0, deadline - time.monotonic())))


def _fetch_transcript_text(transcript_uri: str) -> str:
    """Transcribe 결과 URI에서 텍스트를 가져온다.

    AWS Transcribe는 결과를 JSON 파일로 S3에 저장한다.
    해당 JSON에서 변환된 텍스트를 추출하여 반환한다.

    타임아웃과 크기 상한을 둔다 — 둘 다 없으면 응답 없는 소켓이나 거대한
    결과 파일에 스레드·메모리가 묶인다.

    Args:
        transcript_uri: Transcribe 결과 JSON의 URI

    Returns:
        변환된 텍스트

    Raises:
        RuntimeError: 결과 파싱 실패 시
    """
    try:
        with urllib.request.urlopen(
            transcript_uri, timeout=TRANSCRIPT_FETCH_TIMEOUT
        ) as resp:
            raw = resp.read(TRANSCRIPT_MAX_BYTES + 1)
        if len(raw) > TRANSCRIPT_MAX_BYTES:
            raise RuntimeError(
                f"Transcribe 결과가 너무 큽니다 (>{TRANSCRIPT_MAX_BYTES} 바이트)"
            )
        result = json.loads(raw.decode("utf-8"))

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


def _download_and_upload_sync(
    video_id: str, job_name: str, s3_key: str, uploaded_keys: set[str]
) -> str:
    """오디오를 내려받아 S3 에 올린다 (전용 스레드풀에서 실행).

    임시 디렉터리는 성공·실패 모두 정리한다. 업로드를 시도했으면 uploaded_keys 에
    키를 넣어 호출자가 정리할 수 있게 한다(취소되어도 삭제가 누락되지 않는다).

    Args:
        video_id: 유튜브 비디오 ID
        job_name: 파일명으로 쓸 작업 이름
        s3_key: 업로드할 S3 객체 키

    Returns:
        업로드된 S3 URI
    """
    temp_dir = tempfile.mkdtemp()
    try:
        output_path = os.path.join(temp_dir, job_name)
        logger.info("비디오 %s: 오디오 다운로드 시작", video_id)
        audio_file = _download_audio(video_id, output_path)

        logger.info("비디오 %s: S3 업로드 시작", video_id)
        # 업로드 직전에 표시한다 — 호출자가 await 를 취소해도 이 스레드는 계속
        # 돌아 업로드를 마치므로, 반환값에만 의존하면 취소 시 객체가 고아가 된다.
        uploaded_keys.add(s3_key)
        return _upload_to_s3(audio_file, s3_key)
    finally:
        # yt-dlp 가 .part·.webm 등 중간 파일을 남기므로 os.rmdir 로는 못 지운다
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.debug("임시 디렉터리 정리: %s", temp_dir)


async def transcribe_audio(video_id: str) -> str:
    """유튜브 영상의 오디오를 다운로드하고 음성 인식으로 텍스트를 추출한다.

    무거운 블로킹 구간(다운로드·ffmpeg·업로드)만 전용 스레드풀에서 돌리고,
    Transcribe 완료 대기는 asyncio 로 처리해 스레드를 점유하지 않는다.
    업로드한 오디오는 성공·실패 어느 쪽이든 S3 에서 삭제한다.

    Args:
        video_id: 유튜브 비디오 ID

    Returns:
        변환된 텍스트

    Raises:
        RuntimeError: 오디오 다운로드, S3 업로드, Transcribe 처리 실패 시
    """
    job_name = f"yt-{video_id}-{uuid.uuid4().hex[:8]}"
    s3_key = f"audio-summary/{job_name}.mp3"
    loop = asyncio.get_running_loop()
    # 업로드를 시도한 키를 워커 스레드가 여기에 넣는다. 반환값 대신 이걸 보는 이유는
    # await 가 취소돼도 스레드는 업로드를 마치기 때문이다(반환값은 버려진다).
    uploaded_keys: set[str] = set()

    try:
        s3_uri = await loop.run_in_executor(
            _download_executor,
            partial(
                _download_and_upload_sync, video_id, job_name, s3_key, uploaded_keys
            ),
        )

        logger.info("비디오 %s: Transcribe 작업 시작", video_id)
        await loop.run_in_executor(
            None, partial(_start_transcription_job, job_name, s3_uri)
        )

        logger.info("비디오 %s: Transcribe 작업 완료 대기 중", video_id)
        text = await _wait_for_transcription(job_name)
        logger.info("비디오 %s: 음성 인식 완료", video_id)
        return text

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
    finally:
        # 취소(CancelledError)로 빠져나갈 때도 여기를 지난다. shield 로 감싸 삭제
        # 자체가 취소되지 않게 한다 — 안 그러면 취소 시 오디오가 S3 에 남는다.
        for key in uploaded_keys:
            await asyncio.shield(
                loop.run_in_executor(None, partial(_delete_from_s3, key))
            )
