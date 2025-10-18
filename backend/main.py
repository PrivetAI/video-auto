import json
import asyncio
from pathlib import Path
from typing import Dict, Any, List
from image_handler import ImageHandler
from video_composer import VideoComposer
from whisper_tiktok_client import WhisperTikTokClient
from config import cfg, logger

class SlideVideoGenerator:
    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self._setup_dirs()
        self.whisper_client = WhisperTikTokClient()
    
    def _load_config(self, path: str) -> Dict[str, Any]:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _setup_dirs(self):
        for d in [cfg.CACHE_DIR, cfg.OUTPUT_DIR, cfg.TEMP_DIR]:
            Path(d).mkdir(exist_ok=True, parents=True)
    
    async def generate(self, output_filename: str = "output.mp4") -> bool:
        try:
            logger.info("=" * 60)
            logger.info("🎬 Starting video generation")
            logger.info("=" * 60)
            
            slides_data = self.config['slides']
            
            # 1. Load/Generate images only
            logger.info(f"📸 Processing {len(slides_data)} slides...")
            images = []
            for idx, slide in enumerate(slides_data, 1):
                logger.info(f"Slide {idx}/{len(slides_data)}")
                
                source = slide.get('source', 'api')
                image_path = slide.get('imagePath') or slide.get('image')
                
                if source == 'upload' and image_path:
                    img = ImageHandler.load_image(image_path)
                else:
                    prompt = slide.get('prompt') or slide.get('text', '')[:100]
                    if cfg.REPLICATE_API_TOKEN:
                        img = ImageHandler.generate_image(prompt)
                    else:
                        img = ImageHandler.generate_placeholder(prompt)
                
                images.append(img)
            
            # 2. Create silent video from slides
            logger.info("🎥 Creating silent video...")
            silent_video = Path(cfg.TEMP_DIR) / f"silent_{output_filename}"
            
            success = VideoComposer.create_silent_video(
                images,
                str(silent_video),
                duration_per_slide=5.0  # Default duration
            )
            
            if not success:
                raise Exception("Failed to create silent video")
            
            # 3. Send to Whisper-TikTok for audio + subtitles
            logger.info("🎤 Sending to Whisper-TikTok service...")
            final_video = await self.whisper_client.add_audio_and_subtitles(
                video_path=str(silent_video),
                texts=[slide['text'] for slide in slides_data],
                voice=self.config.get('voice', 'en-US-ChristopherNeural'),
                language=self.config.get('language', 'en'),
                output_path=str(Path(cfg.OUTPUT_DIR) / output_filename)
            )
            
            if final_video:
                logger.info("=" * 60)
                logger.info(f"✅ Video created: {final_video}")
                logger.info(f"📸 Slides: {len(slides_data)}")
                logger.info("=" * 60)
                return True
            else:
                raise Exception("Whisper-TikTok service failed")
            
        except Exception as e:
            logger.error(f"❌ Generation failed: {e}", exc_info=True)
            return False

async def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python main.py --input slides.json [--output video.mp4]")
        return
    
    input_file = output_file = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--input" and i + 1 < len(sys.argv[1:]):
            input_file = sys.argv[i + 2]
        elif arg == "--output" and i + 1 < len(sys.argv[1:]):
            output_file = sys.argv[i + 2]
    
    if not input_file:
        print("Error: --input required")
        return
    
    generator = SlideVideoGenerator(input_file)
    success = await generator.generate(output_file or "output.mp4")
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())