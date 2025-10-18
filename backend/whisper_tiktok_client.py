import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config import cfg, logger


class WhisperTikTokClient:
    """Async client for interacting with the Whisper-TikTok microservice."""

    def __init__(self) -> None:
        self.timeout = httpx.Timeout(300.0, connect=30.0)
        self._raw_base_url = cfg.WHISPER_TIKTOK_URL.rstrip("/")
        self.api_base = self._resolve_api_base(self._raw_base_url)

        self.shared_media_dir = Path(
            os.getenv("WHISPER_TIKTOK_SHARED_MEDIA", "/shared/media")
        )
        self.shared_background_dir = Path(
            os.getenv("WHISPER_TIKTOK_SHARED_BACKGROUNDS", "/shared/backgrounds")
        )
        for shared_dir in (self.shared_media_dir, self.shared_background_dir):
            try:
                shared_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                # Ignore when running outside of the container environment
                pass
        self._local_cache = Path(cfg.TEMP_DIR) / "whisper_tiktok"
        self._local_cache.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------------- #
    # Public API helpers matching the documented endpoints
    # --------------------------------------------------------------------- #

    async def download_video(self, url: str) -> Dict[str, Any]:
        """Proxy `/download-video`."""
        response = await self._request("GET", "download-video", params={"url": url})
        return response.json()

    async def available_backgrounds(self) -> Dict[str, Any]:
        """Proxy `/available-backgrounds`."""
        response = await self._request("GET", "available-backgrounds")
        return response.json()

    async def generate_tts(
        self,
        text: str,
        *,
        outfile: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Proxy `/generate_tts`."""
        params: Dict[str, Any] = {}
        if outfile:
            params["outfile"] = outfile
        if voice:
            params["voice"] = voice

        # FastAPI endpoint expects a JSON encoded raw string body
        response = await self._request(
            "POST",
            "generate_tts",
            params=params,
            json=text,
            headers={"Content-Type": "application/json"},
        )
        payload = response.json()
        local_path = self._project_media_path(payload.get("filename"))
        payload["local_path"] = str(local_path) if local_path else None
        return payload

    async def get_tts(self, filename: str) -> bytes:
        """Proxy `/get_tts/{filename}`."""
        response = await self._request("GET", f"get_tts/{filename}")
        return response.content

    async def get_subtitles(
        self,
        *,
        filename: str,
        model: str = "base",
        non_english: bool = False,
        uuid_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Proxy `/get_subtitles`."""
        params = {
            "filename": filename,
            "model": model,
            "non_english": str(non_english).lower(),
            "uuid": uuid_value or Path(filename).stem,
        }
        response = await self._request("GET", "get_subtitles", params=params)

        vtt_path = self._local_cache / f"{params['uuid']}.vtt"
        vtt_path.write_bytes(response.content)

        ass_candidate = self._project_media_path(f"{params['uuid']}.ass")
        remote_ass = f"/app/media/{params['uuid']}.ass"
        remote_vtt = f"/app/media/{params['uuid']}.vtt"
        return {
            "uuid": params["uuid"],
            "vtt_path": str(vtt_path),
            "ass_path": str(ass_candidate) if ass_candidate.exists() else None,
            "remote_ass_path": remote_ass,
            "remote_vtt_path": remote_vtt,
        }

    async def create_video(
        self, *, background_file: str, audio_file: str, subtitles_file: str
    ) -> Dict[str, Any]:
        """Proxy `/create_video`. Returns JSON payload when possible."""
        params = {
            "background_file": background_file,
            "audio_file": audio_file,
            "subtitles_file": subtitles_file,
        }
        response = await self._request("POST", "create_video", params=params)

        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return {"message": "Video processing started", "path": None}

    async def get_video(self, filename: str) -> bytes:
        """Proxy `/get_video/{filename}`."""
        response = await self._request("GET", f"get_video/{filename}")
        return response.content

    async def get_background(
        self, filename: str, range_header: Optional[str] = None
    ) -> httpx.Response:
        """Proxy `/background/{filename}` with optional range support."""
        headers = {"Range": range_header} if range_header else None
        response = await self._request(
            "GET", f"background/{filename}", headers=headers
        )
        return response

    # --------------------------------------------------------------------- #
    # High-level orchestration used by SlideVideoGenerator
    # --------------------------------------------------------------------- #

    async def add_audio_and_subtitles(
        self,
        video_path: str,
        texts: List[str],
        voice: str,
        language: str,
        output_path: str,
    ) -> Optional[str]:
        """
        Generate audio + subtitles using the Whisper-TikTok API and compose
        the final video locally with FFmpeg.
        """
        if not texts:
            logger.error("No narration texts provided for Whisper-TikTok")
            return None

        script = "\n\n".join([t.strip() for t in texts if t.strip()])
        if not script:
            logger.error("Narration texts contain only whitespace")
            return None

        job_id = uuid.uuid4().hex[:12]
        audio_filename = f"{job_id}.mp3"
        subtitles_uuid = job_id

        try:
            logger.info("🎙️ Generating TTS via Whisper-TikTok")
            tts_payload = await self.generate_tts(
                script, outfile=audio_filename, voice=voice
            )
            remote_audio_path = tts_payload.get("filename")
            audio_file = await self._ensure_media_file(
                remote_audio_path or audio_filename
            )

            logger.info("📝 Generating subtitles via Whisper-TikTok")
            subtitles_payload = await self.get_subtitles(
                filename=Path(audio_file).name,
                model="small",
                non_english=language.lower() not in {"en", "en-us", "english"},
                uuid_value=subtitles_uuid,
            )

            subtitles_file = await self._resolve_ass_file(
                subtitles_payload["uuid"],
                fallback_vtt=subtitles_payload.get("vtt_path"),
            )
            if subtitles_file is None:
                logger.error("Failed to obtain subtitles file (.ass)")
                return None

            composed = self._compose_video(
                video_path=Path(video_path),
                audio_path=audio_file,
                subtitles_path=subtitles_file,
                output_path=Path(output_path),
            )
            return str(composed) if composed else None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response else "error"
            detail = exc.response.text if exc.response else str(exc)
            logger.error("Whisper-TikTok HTTP %s: %s", status, detail)
            return None
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to build narrated video: %s", exc, exc_info=True)
            return None

    async def list_voices(self, language: str = "all") -> List[Dict[str, Any]]:
        """Fetch available voices from Whisper-TikTok service, if exposed."""
        params = {"language": language} if language else {}
        try:
            response = await self._request("GET", "voices", params=params)
            payload = response.json()
            voices = payload.get("voices", [])
            logger.info("Fetched %d voices from Whisper-TikTok", len(voices))
            return voices
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text if exc.response else str(exc)
            status_code = exc.response.status_code if exc.response else "error"
            logger.warning(
                "Voice list HTTP %s: %s (falling back to defaults)", status_code, detail
            )
            return []
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Unable to fetch voices: %s", exc)
            return []

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #

    async def _request(
        self, method: str, endpoint: str, **kwargs: Any
    ) -> httpx.Response:
        url = self._build_url(endpoint)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def _resolve_api_base(self, base: str) -> str:
        if base.endswith("/api/py"):
            return base
        if base.endswith("/api"):
            return f"{base}/py"
        if base.endswith("/py"):
            return base
        return f"{base}/api/py"

    def _build_url(self, endpoint: str) -> str:
        endpoint = endpoint.lstrip("/")
        return f"{self.api_base}/{endpoint}"

    def _project_media_path(self, remote_path: Optional[str]) -> Optional[Path]:
        if not remote_path:
            return None
        filename = Path(remote_path).name
        return self.shared_media_dir / filename

    async def _ensure_media_file(self, remote_path: str) -> Path:
        filename = Path(remote_path).name
        candidate = self._project_media_path(filename)
        if candidate.exists():
            return candidate

        target = self._local_cache / filename
        response = await self._request("GET", f"get_tts/{filename}")
        target.write_bytes(response.content)
        return target

    async def _resolve_ass_file(
        self, uuid_value: str, *, fallback_vtt: Optional[str] = None
    ) -> Optional[Path]:
        ass_candidate = self._project_media_path(f"{uuid_value}.ass")
        if ass_candidate.exists():
            return ass_candidate

        if not fallback_vtt:
            return None

        vtt_path = Path(fallback_vtt)
        if not vtt_path.exists():
            return None

        ass_output = self._local_cache / f"{uuid_value}.ass"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(vtt_path),
            str(ass_output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("FFmpeg failed converting VTT->ASS: %s", result.stderr)
            return None
        return ass_output

    def _compose_video(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        subtitles_path: Path,
        output_path: Path,
    ) -> Optional[Path]:
        """Compose silent video with generated audio + subtitles via ffmpeg."""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        filter_chain = f"[0:v]ass={subtitles_path}[v]"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            filter_chain,
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("FFmpeg composition failed: %s", result.stderr)
            return None

        logger.info("✅ Video composed with audio & subtitles: %s", output_path)
        return output_path

    # Public helpers for other modules

    def shared_media_path(self, filename: str) -> Path:
        return self.shared_media_dir / filename

    def shared_background_path(self, filename: str) -> Path:
        return self.shared_background_dir / filename

    @property
    def cache_dir(self) -> Path:
        return self._local_cache
