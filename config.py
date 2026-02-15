"""Configuration settings for the Roblox Macro"""
import json
import os
import sys

def get_app_path():
    """Get the application path for writable data"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(__file__)

CONFIG_FILE = os.path.join(get_app_path(), "macro_config.json")

DEFAULT_CONFIG = {
    "start_keybind": "f1",
    "stop_keybind": "f3",
    "mode": "Story",
    "location": "Leaf Village",
    "act": "Act 1",
    "nightmare": False,
    "ocr_tolerance": 0.6,  # OCR confidence threshold (0.0 - 1.0)
    "use_avg_tolerance": False,  # Use average tolerance for image matching
    "avg_tolerance": 0.65,  # Average image match confidence (0.0 - 1.0)
    "t_press_delay": 0.08,  # Delay between repeated 'T' presses (seconds)
    "placement_between_delay": 0.4,  # Delay between each unit placement (seconds)
    "placement_confirm_delay": 0.3,  # Delay between placement confirmation clicks (seconds)
    "zoom_duration_story": 0.3,  # Zoom duration for Story mode
    "zoom_duration_raids": 0.3,  # Zoom duration for Raids mode
    "zoom_duration_siege": 0.3,  # Zoom duration for Siege mode
    "zoom_duration_legend": 0.3,  # Zoom duration for Legend mode
    "zoom_duration_auto-challenges": 0.3,  # Zoom duration for Auto-Challenges mode
    "zoom_duration_custom": 0.3,  # Zoom duration for Custom mode
    "zoom_duration_portals": 0.3,  # Zoom duration for Portals mode
    "placement_area": None,  # Will store {"x": int, "y": int, "width": int, "height": int}
    "discord_webhook_url": "",  # Discord webhook URL for notifications
    "stats_wins": 0,  # Total wins tracked
    "stats_losses": 0,  # Total losses tracked
    "private_server_link": "",  # Roblox private server link for auto-reconnect
    "auto_challenges_enabled": False,  # Auto-challenges mode toggle
    "challenge_location": "Leaf Village",  # Challenge location selection
    "buy_rrs_enabled": False,  # Buy RRs side task toggle
    "rejoin_after_games_enabled": False,  # Auto-rejoin after X games toggle
    "rejoin_after_games_count": 5,  # Number of games before auto-rejoin
    "ui_theme": "orange",  # UI color theme: purple, orange, blue, green, red, pink
    "background_image": "",  # Custom background image path
    "background_opacity": 20,  # Background image opacity (0-100)
    "ui_opacity": 100,  # UI element opacity (10-100)
    "custom_color": "#f97316",  # Custom primary/accent color (hex)
    "text_size": 14,  # Text size in pixels
    "text_color": "#e0e6ff",  # Text color (hex)
    "secondary_color": "#8b5cf6",  # Secondary color (hex)
    "button_color": "#f97316",  # Button color (hex)
    "bg_color": "#050510",  # Background color (hex)
    "gradient_enabled": False,  # Enable gradient backgrounds
    "gradient_text_enabled": False,  # Enable gradient text
    "gradient_start": "#f97316",  # Gradient start color (hex)
    "gradient_end": "#ea580c",  # Gradient end color (hex)
    "gradient_angle": "135deg",  # Gradient angle/direction
    "text_brightness": 100,  # Text brightness (50-200%)
    "neon_enabled": False,  # Enable neon glow effect
    "neon_intensity": 8,  # Neon glow intensity (2-20px)
    "custom_themes": [],  # User-saved custom themes
    "slots": [
        {
            "name": "Slot 1",
            "placement_priority": 1,
            "upgrade_priority": 1,
            "placement_limit": 3,
            "enabled": True
        },
        {
            "name": "Slot 2",
            "placement_priority": 2,
            "upgrade_priority": 2,
            "placement_limit": 3,
            "enabled": True
        },
        {
            "name": "Slot 3",
            "placement_priority": 3,
            "upgrade_priority": 3,
            "placement_limit": 3,
            "enabled": False
        },
        {
            "name": "Slot 4",
            "placement_priority": 4,
            "upgrade_priority": 4,
            "placement_limit": 3,
            "enabled": False
        },
        {
            "name": "Slot 5",
            "placement_priority": 5,
            "upgrade_priority": 5,
            "placement_limit": 3,
            "enabled": False
        },
        {
            "name": "Slot 6",
            "placement_priority": 6,
            "upgrade_priority": 6,
            "placement_limit": 3,
            "enabled": False
        }
    ]
}

def load_config():
    """Load configuration from file or return defaults"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Merge with defaults for any missing keys
                for key in DEFAULT_CONFIG:
                    if key not in config:
                        config[key] = DEFAULT_CONFIG[key]
                return config
        except (json.JSONDecodeError, IOError):
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(config):
    """Save configuration to file"""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4)
