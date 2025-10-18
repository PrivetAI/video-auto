import json
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from main import SlideVideoGenerator
from config import cfg, logger
from whisper_tiktok_client import WhisperTikTokClient

app = FastAPI(title="Slide-to-Video Generator API", version="2.0.0")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SlideData(BaseModel):
    text: str
    source: str = "upload"
    voice: Optional[str] = None
    image: Optional[str] = None
    imagePath: Optional[str] = None
    prompt: Optional[str] = None

class GenerateRequest(BaseModel):
    slides: List[SlideData]
    language: str = "en"
    voice: str = "en-US-ChristopherNeural"
    output_filename: str = "output.mp4"

class TaskStatus(BaseModel):
    task_id: str
    status: str
    progress: int = 0
    current_step: str = ""
    message: str = ""
    output_path: Optional[str] = None

tasks: Dict[str, TaskStatus] = {}
voice_client = WhisperTikTokClient()

def update_task_status(task_id: str, status: str, progress: int = 0, 
                       current_step: str = "", message: str = ""):
    if task_id in tasks:
        tasks[task_id].status = status
        tasks[task_id].progress = progress
        tasks[task_id].current_step = current_step
        tasks[task_id].message = message

async def generate_video_task(task_id: str, config: Dict[str, Any], output_filename: str):
    try:
        update_task_status(task_id, "processing", 10, "Initializing...")
        
        config_path = Path(cfg.TEMP_DIR) / f"{task_id}.json"
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        update_task_status(task_id, "processing", 30, "Loading images...")
        generator = SlideVideoGenerator(str(config_path))
        
        update_task_status(task_id, "processing", 60, "Creating video...")
        success = await generator.generate(output_filename)
        
        if success:
            output_path = Path(cfg.OUTPUT_DIR) / output_filename
            update_task_status(task_id, "complete", 100, "Done!", f"Video: {output_path}")
            tasks[task_id].output_path = str(output_path)
        else:
            update_task_status(task_id, "error", 0, "Failed", "Generation error")
        
        config_path.unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}", exc_info=True)
        update_task_status(task_id, "error", 0, "Error", str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/api/voices")
async def get_voices(language: str = "all"):
    try:
        voices = await voice_client.list_voices(language)
        return {"voices": voices}
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        logger.error("Voice list error %s: %s", status_code, detail)
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as exc:
        logger.error("Failed to fetch voices: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Unable to load voices")

@app.get("/")
async def root():
    if FRONTEND_INDEX.exists():
        return FileResponse(str(FRONTEND_INDEX), media_type="text/html")
    return {"message": "Slide-to-Video Generator API", "docs": "/docs"}

@app.post("/api/generate")
async def generate_video(request: GenerateRequest, background_tasks: BackgroundTasks):
    try:
        task_id = str(uuid.uuid4())
        tasks[task_id] = TaskStatus(task_id=task_id, status="queued", current_step="Preparing...")
        
        config = {
            "slides": [],
            "language": request.language,
            "voice": request.voice,
            "output_filename": request.output_filename
        }
        
        for slide in request.slides:
            slide_config = {
                "text": slide.text,
                "source": slide.source,
                "voice": slide.voice or request.voice,
            }
            
            if slide.source == "upload":
                if slide.imagePath:
                    slide_config["imagePath"] = slide.imagePath
                elif slide.image:
                    slide_config["imagePath"] = str(Path(cfg.CACHE_DIR) / "uploads" / slide.image)
            else:
                slide_config["prompt"] = slide.prompt or slide.text[:100]
            
            config["slides"].append(slide_config)
        
        logger.info(f"Task {task_id} config: {json.dumps(config, indent=2)}")
        
        background_tasks.add_task(generate_video_task, task_id, config, request.output_filename)
        return {"task_id": task_id, "status": "queued", "message": "Generation started"}
    except Exception as e:
        logger.error(f"Failed to start: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/generate/{task_id}")
async def get_task_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]

@app.post("/api/generate/{task_id}/cancel")
async def cancel_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    update_task_status(task_id, "cancelled", 0, "Cancelled")
    return {"status": "cancelled"}

@app.get("/api/download/{task_id}")
async def download_video(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    if task.status != "complete":
        raise HTTPException(status_code=400, detail="Video not ready")
    
    if not task.output_path or not Path(task.output_path).exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(task.output_path, media_type="video/mp4", 
                        filename=Path(task.output_path).name)

@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    try:
        images_dir = Path(cfg.CACHE_DIR) / "uploads"
        images_dir.mkdir(exist_ok=True, parents=True)
        
        safe_filename = f"{uuid.uuid4().hex[:8]}_{file.filename}"
        file_path = images_dir / safe_filename
        
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"✓ Uploaded: {safe_filename} ({len(content)} bytes)")
        
        return {
            "filename": safe_filename,
            "path": str(file_path),
            "size": len(content)
        }
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

output_dir = Path(cfg.OUTPUT_DIR)
if output_dir.exists():
    app.mount("/output", StaticFiles(directory=str(output_dir)), name="output")

@app.on_event("startup")
async def startup_event():
    logger.info("Starting Slide-to-Video Generator API v2.0")
    for dir_path in [cfg.CACHE_DIR, cfg.OUTPUT_DIR, cfg.TEMP_DIR, Path(cfg.CACHE_DIR) / "uploads"]:
        Path(dir_path).mkdir(exist_ok=True, parents=True)
    logger.info("API: http://0.0.0.0:8000")
    logger.info("Docs: http://0.0.0.0:8000/docs")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down API")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
