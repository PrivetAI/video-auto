import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config import cfg, logger

class WhisperTikTokClient:
    """Client for Whisper-TikTok API service"""
    
    def __init__(self):
        self.base_url = cfg.WHISPER_TIKTOK_URL
        self.timeout = httpx.Timeout(300.0, connect=30.0)
    
    async def add_audio_and_subtitles(
        self,
        video_path: str,
        texts: List[str],
        voice: str,
        language: str,
        output_path: str
    ) -> Optional[str]:
        """
        Send silent video to Whisper-TikTok API
        Returns path to final video with audio and subtitles
        """
        try:
            logger.info(f"Connecting to Whisper-TikTok: {self.base_url}")
            
            with open(video_path, 'rb') as video_file:
                files = {
                    'video': (Path(video_path).name, video_file, 'video/mp4')
                }
                
                data = {
                    'texts': json.dumps(texts),
                    'voice': voice,
                    'language': language,
                    'font': 'Lexend Bold',
                    'font_color': '#fff000',
                    'font_size': 21,
                    'sub_position': 5,
                    'max_words': 2
                }
                
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/api/generate",
                        files=files,
                        data=data
                    )
                    
                    response.raise_for_status()
                    
                    with open(output_path, 'wb') as out_file:
                        out_file.write(response.content)
                    
                    logger.info(f"✓ Video with audio/subs: {output_path}")
                    return output_path
        
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code}: {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Failed: {e}", exc_info=True)
            return None

    async def list_voices(self, language: str = "all") -> List[Dict[str, Any]]:
        """Fetch available voices from Whisper-TikTok service."""
        params = {"language": language} if language else {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/api/voices",
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
                voices = payload.get("voices", [])
                logger.info("Fetched %d voices from Whisper-TikTok", len(voices))
                return voices
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text if exc.response else str(exc)
            logger.error("Voice list HTTP %s: %s", exc.response.status_code if exc.response else "error", detail)
            raise
        except Exception as exc:
            logger.error("Unable to fetch voices: %s", exc, exc_info=True)
            raise
