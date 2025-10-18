import os
import tempfile
import logging
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    # Video parameters
    VIDEO_WIDTH = 1080
    VIDEO_HEIGHT = 1920
    VIDEO_FPS = 30
    
    # Effects and transitions
    EFFECTS_ENABLED = True
    TRANSITION_DURATION = 0.3
    
    # Directories
    CACHE_DIR = os.getenv("CACHE_DIR", "cache")
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
    TEMP_DIR = os.getenv("TEMP_DIR", tempfile.gettempdir())
    
    # Image Generation API (optional)
    REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "flux-schnell")
    IMAGE_STYLE_PRESET = os.getenv("IMAGE_STYLE_PRESET", "cinematic")
    
    # Whisper-TikTok service URL
    WHISPER_TIKTOK_URL = os.getenv("WHISPER_TIKTOK_URL", "http://whisper-tiktok:8001")
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

cfg = Config()

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)