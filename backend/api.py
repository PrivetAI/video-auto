import json
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
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

class DownloadVideoRequest(BaseModel):
    url: str

class GenerateTTSRequest(BaseModel):
    text: str
    outfile: Optional[str] = None
    voice: Optional[str] = None

class CreateVideoRequest(BaseModel):
    background_file: str
    audio_file: str
    subtitles_file: str

tasks: Dict[str, TaskStatus] = {}
whisper_client = WhisperTikTokClient()

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
        voices = await whisper_client.list_voices(language)
        return {"voices": voices}
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        logger.error("Voice list error %s: %s", status_code, detail)
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as exc:
        logger.error("Failed to fetch voices: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail="Unable to load voices")

@app.post("/api/whisper/backgrounds/download")
async def whisper_download_background(request: DownloadVideoRequest):
    try:
        return await whisper_client.download_video(request.url)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        raise HTTPException(status_code=code, detail=detail)

@app.get("/api/whisper/backgrounds")
async def whisper_available_backgrounds():
    try:
        payload = await whisper_client.available_backgrounds()
        files = payload.get("backgrounds", [])
        enriched = [
            {
                "name": name,
                "local_path": str(whisper_client.shared_background_path(name)),
                "exists": whisper_client.shared_background_path(name).exists(),
            }
            for name in files
        ]
        payload["items"] = enriched
        return payload
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        raise HTTPException(status_code=code, detail=detail)

@app.get("/api/whisper/backgrounds/{filename}")
async def whisper_get_background(filename: str):
    local_path = whisper_client.shared_background_path(filename)
    if local_path.exists():
        return FileResponse(str(local_path), media_type="video/mp4", filename=filename)
    try:
        response = await whisper_client.get_background(filename)
        return Response(content=response.content, media_type="video/mp4")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response else 404
        detail = exc.response.text if exc.response else str(exc)
        raise HTTPException(status_code=code, detail=detail)

@app.post("/api/whisper/tts")
async def whisper_generate_tts(request: GenerateTTSRequest):
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Text is required for TTS")
    try:
        payload = await whisper_client.generate_tts(
            request.text,
            outfile=request.outfile,
            voice=request.voice,
        )
        return payload
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        raise HTTPException(status_code=code, detail=detail)

@app.get("/api/whisper/tts/{filename}")
async def whisper_get_tts(filename: str):
    local_path = whisper_client.shared_media_path(filename)
    if local_path.exists():
        return FileResponse(str(local_path), media_type="audio/mpeg", filename=filename)
    try:
        content = await whisper_client.get_tts(filename)
        cache_path = whisper_client.cache_dir / filename
        cache_path.write_bytes(content)
        return FileResponse(str(cache_path), media_type="audio/mpeg", filename=filename)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response else 404
        detail = exc.response.text if exc.response else str(exc)
        raise HTTPException(status_code=code, detail=detail)

@app.get("/api/whisper/subtitles")
async def whisper_get_subtitles(
    filename: str,
    model: str = "base",
    non_english: bool = False,
    uuid_value: Optional[str] = None,
):
    try:
        payload = await whisper_client.get_subtitles(
            filename=filename,
            model=model,
            non_english=non_english,
            uuid_value=uuid_value,
        )
        return payload
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        raise HTTPException(status_code=code, detail=detail)

@app.get("/api/whisper/subtitles/file/{uuid_value}")
async def whisper_get_subtitles_file(uuid_value: str, kind: str = "ass"):
    if kind not in {"ass", "vtt"}:
        raise HTTPException(status_code=400, detail="Unsupported subtitle format")
    if kind == "ass":
        path = whisper_client.shared_media_path(f"{uuid_value}.ass")
        media_type = "text/plain"
    else:
        path = whisper_client.cache_dir / f"{uuid_value}.vtt"
        media_type = "text/vtt"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Subtitle file not found")
    return FileResponse(str(path), media_type=media_type, filename=path.name)

@app.post("/api/whisper/video")
async def whisper_create_video(request: CreateVideoRequest):
    try:
        payload = await whisper_client.create_video(
            background_file=request.background_file,
            audio_file=request.audio_file,
            subtitles_file=request.subtitles_file,
        )
        return payload
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response else 502
        detail = exc.response.text if exc.response else str(exc)
        raise HTTPException(status_code=code, detail=detail)

@app.get("/api/whisper/video/{filename}")
async def whisper_get_video(filename: str):
    local_path = whisper_client.shared_background_path(filename)
    if local_path.exists():
        return FileResponse(str(local_path), media_type="video/mp4", filename=filename)
    try:
        content = await whisper_client.get_video(filename)
        cache_path = whisper_client.cache_dir / filename
        cache_path.write_bytes(content)
        return FileResponse(str(cache_path), media_type="video/mp4", filename=filename)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code if exc.response else 404
        detail = exc.response.text if exc.response else str(exc)
        raise HTTPException(status_code=code, detail=detail)

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
