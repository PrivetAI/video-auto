import random
from config import cfg, logger

class EffectsEngine:
    """Ken Burns effects and transitions - improved version"""
    
    TRANSITIONS = [
        'fade', 'wipeleft', 'wiperight', 'slideleft', 'slideright', 
        'circleopen', 'circleclose', 'dissolve'
    ]

    @staticmethod
    def get_random_ken_burns(duration: float, width=cfg.VIDEO_WIDTH, height=cfg.VIDEO_HEIGHT) -> str:
        """
        Generate Ken Burns effect with smooth, natural motion
        Based on ShortGPT approach
        """
        # Выбираем эффект с весами (чаще используем мягкие)
        effects = [
            ('zoom_in_center', 0.25),
            ('zoom_out_center', 0.25),
            ('pan_right', 0.15),
            ('pan_left', 0.15),
            ('zoom_pan_diagonal', 0.20)
        ]
        
        effect = random.choices([e[0] for e in effects], weights=[e[1] for e in effects])[0]
        logger.info(f"Ken Burns: {effect} ({duration:.2f}s)")
        
        fps = cfg.VIDEO_FPS
        total_frames = int(duration * fps)
        
        # Безопасные параметры (меньше тряски)
        zoom_speed = 0.0005  # Медленнее
        max_zoom = 1.2  # Меньше увеличение
        
        if effect == 'zoom_in_center':
            # Плавный зум к центру
            return (
                f"zoompan=z='min(zoom+{zoom_speed},1.0+0.2*sin(on*2*PI/{total_frames}))':"
                f"d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={width}x{height}:fps={fps}"
            )
        
        elif effect == 'zoom_out_center':
            # Плавный зум от центра
            return (
                f"zoompan=z='max(1.3-on*{zoom_speed},1.0)':"
                f"d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={width}x{height}:fps={fps}"
            )
        
        elif effect == 'pan_right':
            # Медленное движение вправо с легким зумом
            return (
                f"zoompan=z='min(1.0+on*{zoom_speed},{max_zoom})':"
                f"d={total_frames}:"
                f"x='iw/2-(iw/zoom/2)-({width}/2)*on/{total_frames}':"
                f"y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
            )
        
        elif effect == 'pan_left':
            # Медленное движение влево с легким зумом
            return (
                f"zoompan=z='min(1.0+on*{zoom_speed},{max_zoom})':"
                f"d={total_frames}:"
                f"x='iw/2-(iw/zoom/2)+({width}/2)*on/{total_frames}':"
                f"y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
            )
        
        else:  # zoom_pan_diagonal
            # Диагональное движение
            return (
                f"zoompan=z='min(1.0+on*{zoom_speed},{max_zoom})':"
                f"d={total_frames}:"
                f"x='iw/2-(iw/zoom/2)-({width}/4)*on/{total_frames}':"
                f"y='ih/2-(ih/zoom/2)-({height}/4)*on/{total_frames}':"
                f"s={width}x{height}:fps={fps}"
            )

    @staticmethod
    def get_random_transition() -> str:
        """Get random transition (weighted)"""
        # Чаще используем мягкие переходы
        transitions = [
            ('fade', 0.3),
            ('dissolve', 0.3),
            ('wipeleft', 0.1),
            ('wiperight', 0.1),
            ('slideleft', 0.1),
            ('slideright', 0.1),
        ]
        
        return random.choices([t[0] for t in transitions], weights=[t[1] for t in transitions])[0]