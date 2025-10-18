import os
import time
import requests
from pathlib import Path
from typing import Optional
from PIL import Image
from io import BytesIO
from config import cfg, logger

class ImageHandler:
    """Handle image loading and generation via Replicate API"""

    # Replicate API endpoint
    REPLICATE_API_URL = "https://api.replicate.com/v1/predictions"
    
    # Модели (выбирайте нужную)
    MODELS = {
        "flux-schnell": "black-forest-labs/flux-schnell",  # Быстрая, дешевая ($0.003)
        "flux-dev": "black-forest-labs/flux-dev",  # Качественная ($0.025)
        "sdxl": "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b",  # SDXL
    }

    @staticmethod
    def load_image(path: str) -> Image.Image:
        """Load and prepare image"""
        logger.info(f"Loading: {path}")
        img = Image.open(path).convert("RGB")
        return ImageHandler.resize_to_vertical(img)

    @staticmethod
    def resize_to_vertical(img: Image.Image, width=cfg.VIDEO_WIDTH, height=cfg.VIDEO_HEIGHT) -> Image.Image:
        """Resize/crop to vertical format (1080x1920)"""
        img_ratio = img.width / img.height
        target_ratio = width / height

        if img_ratio > target_ratio:
            # Шире - обрезаем по ширине
            new_width = int(img.height * target_ratio)
            left = (img.width - new_width) // 2
            img = img.crop((left, 0, left + new_width, img.height))
        else:
            # Выше - обрезаем по высоте
            new_height = int(img.width / target_ratio)
            top = (img.height - new_height) // 2
            img = img.crop((0, top, img.width, top + new_height))

        return img.resize((width, height), Image.Resampling.LANCZOS)

    @staticmethod
    def generate_image(prompt: str, model: str = "flux-schnell", style_preset: Optional[str] = None) -> Optional[Image.Image]:
        """
        Generate image via Replicate API
        
        Args:
            prompt: Text prompt
            model: Model name (flux-schnell, flux-dev, sdxl)
            style_preset: Optional style (cinematic, anime, photographic, etc.)
        
        Returns:
            PIL Image or None if failed
        """
        api_key = os.getenv("REPLICATE_API_TOKEN")
        
        if not api_key:
            logger.warning("REPLICATE_API_TOKEN not set, using placeholder")
            return ImageHandler.generate_placeholder(prompt)
        
        try:
            model_version = ImageHandler.MODELS.get(model, ImageHandler.MODELS["flux-schnell"])
            logger.info(f"Generating image: {prompt[:50]}... (model: {model})")
            
            # Улучшаем промпт для вертикального формата
            enhanced_prompt = f"{prompt}, vertical composition, 9:16 aspect ratio, portrait orientation"
            if style_preset:
                enhanced_prompt = f"{style_preset} style, {enhanced_prompt}"
            
            # Создаем prediction
            headers = {
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "version": model_version.split(":")[-1] if ":" in model_version else None,
                "input": {
                    "prompt": enhanced_prompt,
                    "width": cfg.VIDEO_WIDTH,
                    "height": cfg.VIDEO_HEIGHT,
                    "num_outputs": 1
                }
            }
            
            # Если используется модель без версии (flux-schnell, flux-dev)
            if ":" not in model_version:
                payload = {
                    "model": model_version,
                    "input": payload["input"]
                }
            
            # Запускаем генерацию
            response = requests.post(ImageHandler.REPLICATE_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            
            prediction = response.json()
            prediction_id = prediction.get("id")
            
            if not prediction_id:
                raise ValueError("No prediction ID returned")
            
            # Ждем завершения (polling)
            max_wait = 120  # 2 минуты
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                status_response = requests.get(
                    f"{ImageHandler.REPLICATE_API_URL}/{prediction_id}",
                    headers=headers
                )
                status_response.raise_for_status()
                result = status_response.json()
                
                status = result.get("status")
                
                if status == "succeeded":
                    output = result.get("output")
                    if isinstance(output, list) and len(output) > 0:
                        image_url = output[0]
                    elif isinstance(output, str):
                        image_url = output
                    else:
                        raise ValueError("Unexpected output format")
                    
                    # Скачиваем изображение
                    img_response = requests.get(image_url)
                    img_response.raise_for_status()
                    
                    img = Image.open(BytesIO(img_response.content)).convert("RGB")
                    img = ImageHandler.resize_to_vertical(img)
                    
                    logger.info(f"✓ Image generated successfully")
                    return img
                
                elif status == "failed":
                    error = result.get("error", "Unknown error")
                    raise ValueError(f"Generation failed: {error}")
                
                elif status in ["starting", "processing"]:
                    time.sleep(2)
                    continue
                
                else:
                    logger.warning(f"Unknown status: {status}")
                    time.sleep(2)
            
            raise TimeoutError("Image generation timed out")
            
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            logger.warning("Falling back to placeholder")
            return ImageHandler.generate_placeholder(prompt)

    @staticmethod
    def generate_placeholder(prompt: str = None) -> Image.Image:
        """Generate gradient placeholder"""
        logger.info(f"Creating placeholder: {prompt[:30] if prompt else 'default'}...")
        
        img = Image.new('RGB', (cfg.VIDEO_WIDTH, cfg.VIDEO_HEIGHT))
        pixels = img.load()
        
        # Генерируем градиент на основе промпта (для уникальности)
        seed = sum(ord(c) for c in (prompt or "default"))
        r_val = (seed * 137) % 200 + 55
        g_val = (seed * 191) % 200 + 55
        b_val = (seed * 223) % 200 + 55
        
        for y in range(cfg.VIDEO_HEIGHT):
            factor = y / cfg.VIDEO_HEIGHT
            for x in range(cfg.VIDEO_WIDTH):
                x_factor = x / cfg.VIDEO_WIDTH
                pixels[x, y] = (
                    int(r_val * factor),
                    int(g_val * (1 - x_factor + factor) / 2),
                    int(b_val * x_factor)
                )
        
        return img