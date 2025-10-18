import subprocess
from pathlib import Path
from typing import List
from PIL import Image
from config import cfg, logger
from effects_engine import EffectsEngine

class VideoComposer:
    """Compose silent video with effects only"""

    @staticmethod
    def create_silent_video(
        slides: List[Image.Image], 
        output_path: str, 
        duration_per_slide: float = 5.0
    ) -> bool:
        """Create silent video from slides with Ken Burns effects"""
        logger.info(f"Creating silent video: {output_path}")
        temp_dir = Path(cfg.TEMP_DIR) / "video_temp"
        temp_dir.mkdir(exist_ok=True)

        try:
            segments = []
            
            # Create segment for each slide
            for idx, img in enumerate(slides):
                slide_path = temp_dir / f"slide_{idx}.png"
                img.save(slide_path, format='PNG')

                segment_path = temp_dir / f"seg_{idx}.mp4"
                
                # Apply Ken Burns effect
                if cfg.EFFECTS_ENABLED:
                    effect = EffectsEngine.get_random_ken_burns(duration_per_slide)
                    cmd = [
                        'ffmpeg', '-y', '-loop', '1', '-i', str(slide_path),
                        '-vf', effect,
                        '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                        '-t', str(duration_per_slide), '-r', str(cfg.VIDEO_FPS),
                        '-pix_fmt', 'yuv420p', '-an',  # No audio
                        str(segment_path)
                    ]
                else:
                    cmd = [
                        'ffmpeg', '-y', '-loop', '1', '-i', str(slide_path),
                        '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                        '-t', str(duration_per_slide), '-r', str(cfg.VIDEO_FPS),
                        '-pix_fmt', 'yuv420p', '-an',
                        str(segment_path)
                    ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"Segment {idx} failed: {result.stderr}")
                    raise subprocess.CalledProcessError(result.returncode, cmd)
                
                segments.append(str(segment_path))
                logger.info(f"✓ Segment {idx+1}/{len(slides)} created")

            # Merge with transitions
            if len(segments) == 1:
                import shutil
                shutil.copy(segments[0], output_path)
            else:
                VideoComposer._merge_with_transitions(
                    segments, 
                    [duration_per_slide] * len(segments),
                    output_path
                )

            logger.info(f"✓ Silent video created")
            return True
            
        except Exception as e:
            logger.error(f"Video creation failed: {e}", exc_info=True)
            return False

    @staticmethod
    def _merge_with_transitions(segments: List[str], durations: List[float], output_path: str):
        """Merge segments with xfade transitions"""
        logger.info(f"Merging {len(segments)} segments...")
        
        inputs = []
        filters = []
        
        # Input streams
        for idx, seg in enumerate(segments):
            inputs.extend(['-i', seg])
            filters.append(f"[{idx}:v]format=yuv420p[v{idx}]")

        # Transition chain
        current_label = "[v0]"
        
        for i in range(1, len(segments)):
            transition = EffectsEngine.get_random_transition()
            base_duration = durations[i - 1]
            overlap = min(cfg.TRANSITION_DURATION, base_duration * 0.3, 0.5)
            overlap = max(0.1, overlap)
            transition_offset = max(0.1, base_duration - overlap)
            
            out_label = "[vout]" if i == len(segments) - 1 else f"[xf{i}]"
            
            filters.append(
                f"{current_label}[v{i}]xfade=transition={transition}:"
                f"duration={overlap:.3f}:offset={transition_offset:.3f}{out_label}"
            )
            
            current_label = out_label

        cmd = ['ffmpeg', '-y'] + inputs + [
            '-filter_complex', ';'.join(filters),
            '-map', current_label,
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
            '-movflags', '+faststart',
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"FFmpeg stderr: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd)
        
        logger.info("✓ Segments merged")