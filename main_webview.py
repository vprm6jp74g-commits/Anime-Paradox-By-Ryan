"""
Roblox Macro with OCR - Main Application (PyWebview Version)
A macro for automating gameplay in Roblox games with OCR-based detection.
"""
import sys
import os

# Fix stdout/stderr encoding for frozen exe (prevents character map errors)
if getattr(sys, 'frozen', False):
    import io
    if sys.stdout is None or isinstance(sys.stdout, type(None)):
        sys.stdout = io.TextIOWrapper(open(os.devnull, 'w').detach(), encoding='utf-8')
    elif hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.stderr is None or isinstance(sys.stderr, type(None)):
        sys.stderr = io.TextIOWrapper(open(os.devnull, 'w').detach(), encoding='utf-8')
    elif hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import webview
import keyboard
import threading
import queue
import base64
import ctypes
import time
from ctypes import wintypes
from config import load_config, save_config
from macro_engine import MacroEngine
from version import VERSION
from coordinate_picker import CoordinatePicker

def is_admin():
    """Check if the script is running with administrator privileges"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    """Relaunch the script with administrator privileges"""
    try:
        if getattr(sys, 'frozen', False):
            # Running as compiled exe
            script = sys.executable
            params = ' '.join([f'"{arg}"' for arg in sys.argv[1:]])
        else:
            # Running as script
            script = sys.executable
            params = f'"{__file__}"' + (' ' + ' '.join([f'"{arg}"' for arg in sys.argv[1:]]) if len(sys.argv) > 1 else '')
        
        # ShellExecuteW with 'runas' to request elevation
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            script,
            params,
            None,
            1  # SW_SHOWNORMAL
        )
        sys.exit(0)
    except Exception as e:
        print(f"Failed to elevate privileges: {e}")
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Failed to run as administrator.\n\nError: {e}\n\nPlease run the program as administrator manually.",
            "Administrator Privileges Required",
            0x10  # MB_ICONERROR
        )
        sys.exit(1)

# Helper function to get the correct base path for resources
def get_base_path():
    """Get the base path for bundled resources (read-only assets like ui.html, coordinate_picker.py)"""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(__file__)

def get_app_path():
    """Get the application path for writable data (Settings, starting image, etc.)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(__file__)

# Windows API for window management
user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
GetWindowText = user32.GetWindowTextW
GetWindowTextLength = user32.GetWindowTextLengthW
SetParent = user32.SetParent
SetWindowPos = user32.SetWindowPos
GetWindowRect = user32.GetWindowRect
ShowWindow = user32.ShowWindow
IsWindow = user32.IsWindow

SWP_SHOWWINDOW = 0x0040
SW_SHOW = 5

class MacroAPI:
    def __init__(self):
        self.config = load_config()
        self.engine = None
        self._status_queue = queue.Queue()
        self._hotkeys_registered = False
        self._capturing_key = False
        self._captured_key = None
        self._roblox_hwnd = None
        self._original_parent = None
        self._window = None
        self._overlay_window = None
        
        # Cleanup: Ensure any embedded Roblox windows are detached on startup
        self._cleanup_embedded_roblox()
        
    def capture_keybind(self, key_type):
        """Capture a keybind from user input"""
        self._capturing_key = True
        self._captured_key = None
        
        def on_key(event):
            if self._capturing_key:
                self._captured_key = event.name
                self._capturing_key = False
                keyboard.unhook_all()
                return False
        
        keyboard.on_press(on_key)
        
        # Wait for key capture (with timeout)
        import time
        timeout = 10
        start = time.time()
        while self._capturing_key and time.time() - start < timeout:
            time.sleep(0.1)
        
        keyboard.unhook_all()
        return self._captured_key if self._captured_key else ('F1' if key_type == 'start' else 'F3')
    
    def apply_keybinds(self, start_key, stop_key):
        """Apply the keybinds"""
        if self._hotkeys_registered:
            keyboard.unhook_all_hotkeys()
            self._hotkeys_registered = False
        
        try:
            # Ensure keys are lowercase
            start_key = start_key.lower()
            stop_key = stop_key.lower()
            
            # Register hotkeys in a separate thread to avoid blocking
            def register_keys():
                try:
                    keyboard.add_hotkey(start_key, self._start_macro_callback, suppress=False)
                    keyboard.add_hotkey(stop_key, self._stop_macro_callback, suppress=False)
                    keyboard.add_hotkey('f4', self._take_screenshot_callback, suppress=False)
                    self._hotkeys_registered = True
                    print(f"Hotkeys registered: Start={start_key}, Stop={stop_key}, Screenshot=F4")
                except Exception as e:
                    print(f"Error in register_keys thread: {e}")
            
            # Run registration in thread
            threading.Thread(target=register_keys, daemon=True).start()
            time.sleep(0.5)  # Give time for registration
            
            self.config["start_keybind"] = start_key
            self.config["stop_keybind"] = stop_key
            save_config(self.config)
            return True
        except Exception as e:
            print(f"Error registering hotkeys: {e}")
            return False
    
    def _get_image_folder_path(self):
        """Get the image folder path based on current mode and location"""
        # Reload config to get latest values
        self.config = load_config()
        
        base_folder = os.path.join(get_app_path(), "starting image")
        mode = self.config.get("mode", "Story")
        location = self.config.get("location", "Leaf Village")
        
        print(f"DEBUG: mode={mode}, location={location}")
        
        # Build path based on mode and location
        # Legend mode uses the same images as Story mode
        if mode == "Story" or mode == "Legend":
            folder_name = self._get_location_key(location)
            print(f"DEBUG: folder_name={folder_name}")
            image_folder = os.path.join(base_folder, "Story", folder_name)
        elif mode == "Auto-Challenges":
            # Auto-Challenges uses challenge_location which maps to Story folders
            challenge_location = self.config.get("challenge_location", "Leaf Village")
            folder_name = self._get_location_key(challenge_location)
            print(f"DEBUG: challenge_location={challenge_location}, folder_name={folder_name}")
            image_folder = os.path.join(base_folder, "Story", folder_name)
        elif mode == "Portals":
            # Portals mode: starting image/Portals/{portal_selection}/
            portal_selection = self.config.get("portal_selection", "JJK Portal")
            # Map portal name to folder name
            portal_folder = portal_selection.replace(" ", " ")  # Keep spaces in folder name
            image_folder = os.path.join(base_folder, "Portals", portal_folder)
        elif mode == "Raid" or mode == "Raids":
            # Raid mode: starting image/Raid/{location}/
            image_folder = os.path.join(base_folder, "Raid", location)
        elif mode == "Siege":
            # Siege mode: starting image/Siege/{location}/
            image_folder = os.path.join(base_folder, "Siege", location)
        else:
            # For other modes, use base folder
            image_folder = base_folder
        
        # Create folder if it doesn't exist
        os.makedirs(image_folder, exist_ok=True)
        print(f"DEBUG: image_folder={image_folder}")
        return image_folder
    
    def _get_location_key(self, location):
        """Get the folder/config key for a location"""
        location_lower = location.lower()
        if "planet" in location_lower or "namak" in location_lower or "namek" in location_lower:
            return "Planet"
        elif "leaf" in location_lower or "village" in location_lower:
            return "Leaf"
        elif "hollow" in location_lower or "dark" in location_lower:
            return "Dark"
        elif "shibuya" in location_lower:
            return "Shibuya"
        else:
            return "Leaf"  # Default
    

    
    def _take_screenshot_callback(self):
        """Callback for F4 screenshot hotkey"""
        print("F4 screenshot hotkey pressed!")
        try:
            # Try to get Roblox region from attached window first, then from engine
            region = None
            
            # Check if Roblox is attached/embedded
            if self._roblox_hwnd and IsWindow(self._roblox_hwnd):
                rect = wintypes.RECT()
                GetWindowRect(self._roblox_hwnd, ctypes.byref(rect))
                region = (rect.left, rect.top, rect.right, rect.bottom)
                print(f"Using attached Roblox window region: {region}")
            # Fall back to engine detection
            elif self.engine and self.engine.roblox_region:
                region = self.engine.roblox_region
                print(f"Using engine Roblox region: {region}")
            
            if not region:
                msg = "Screenshot failed: No Roblox window detected. Please attach Roblox first."
                print(msg)
                self._status_callback(msg)
                return
            
            # Take screenshot of Roblox window
            import mss
            from PIL import Image
            import io
            
            with mss.mss() as sct:
                monitor = {
                    "left": region[0],
                    "top": region[1],
                    "width": region[2] - region[0],
                    "height": region[3] - region[1]
                }
                screenshot = sct.grab(monitor)
                img = Image.frombytes('RGB', (screenshot.width, screenshot.height), screenshot.rgb)
                
                # Get the correct folder path
                image_folder = self._get_image_folder_path()
                
                # Delete existing images in the folder
                for filename in os.listdir(image_folder):
                    if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                        os.remove(os.path.join(image_folder, filename))
                
                # Save new screenshot
                image_path = os.path.join(image_folder, "screenshot.png")
                img.save(image_path)
                
                mode = self.config.get("mode", "Story")
                location = self.config.get("location", "Leaf Village")
                self._status_callback(f"Screenshot saved to {image_folder} for {mode} - {location}")
                print(f"Screenshot saved: {image_path}")
        except Exception as e:
            print(f"Error taking screenshot: {e}")
            self._status_callback(f"Screenshot error: {str(e)}")
    
    def update_tolerance(self, tolerance):
        """Update OCR tolerance setting"""
        self.config["ocr_tolerance"] = tolerance
        save_config(self.config)
        print(f"OCR tolerance updated to: {tolerance}")
        return True

    def update_t_press_delay(self, delay):
        """Update delay between repeated T presses (from UI)"""
        try:
            val = float(delay)
        except Exception:
            print(f"Invalid t_press_delay value: {delay}")
            return False
        self.config["t_press_delay"] = val
        save_config(self.config)
        print(f"T-press delay updated to: {val}")
        return True
    
    def save_webhook_url(self, url):
        """Save Discord webhook URL"""
        self.config["discord_webhook_url"] = url
        save_config(self.config)
        print(f"Webhook URL saved: {'[set]' if url else '[cleared]'}")
        return True
    
    def test_webhook(self):
        """Send a test message to the Discord webhook"""
        import urllib.request
        import json as json_module
        
        url = self.config.get("discord_webhook_url", "")
        if not url:
            return False
        
        try:
            wins = self.config.get("stats_wins", 0)
            losses = self.config.get("stats_losses", 0)
            total = wins + losses
            win_rate = round((wins / total) * 100) if total > 0 else 0
            
            embed = {
                "content": None,
                "embeds": [{
                    "title": "🧪 Webhook Test",
                    "description": "Webhook is working correctly!",
                    "color": 0x8B5CF6,
                    "fields": [
                        {"name": "✅ Wins", "value": str(wins), "inline": True},
                        {"name": "❌ Losses", "value": str(losses), "inline": True},
                        {"name": "📊 Win Rate", "value": f"{win_rate}%", "inline": True}
                    ],
                    "footer": {"text": "AnimeParadoxMacro"}
                }]
            }
            
            data = json_module.dumps(embed).encode('utf-8')
            req = urllib.request.Request(
                url, 
                data=data, 
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'AnimeParadoxMacro/1.0'
                }
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 204 or response.status == 200:
                    return True
            return True
        except urllib.error.HTTPError as e:
            print(f"Webhook test HTTP error {e.code}: {e.reason}")
            return False
        except Exception as e:
            print(f"Webhook test error: {e}")
            return False
    
    def reset_stats(self):
        """Reset win/loss stats"""
        self.config["stats_wins"] = 0
        self.config["stats_losses"] = 0
        save_config(self.config)
        print("Stats reset")
        return True
    
    def save_private_server_link(self, link):
        """Save Roblox private server link for auto-reconnect"""
        self.config["private_server_link"] = link
        save_config(self.config)
        print(f"Private server link saved: {'[set]' if link else '[cleared]'}")
        return True
    
    def save_rejoin_after_games(self, enabled, count):
        """Save rejoin-after-games settings"""
        self.config["rejoin_after_games_enabled"] = bool(enabled)
        self.config["rejoin_after_games_count"] = int(count)
        save_config(self.config)
        print(f"Rejoin after games updated - enabled: {enabled}, count: {count}")
        return True
    
    def get_hourly_timer_data(self):
        """Get remaining time until next hourly side task"""
        import time
        CHALLENGE_INTERVAL = 60 * 60  # 60 minutes in seconds
        
        # Determine which tasks are enabled
        tasks = []
        if self.config.get("buy_rrs_enabled", False):
            tasks.append("Buy RRs")
        if self.config.get("auto_challenges_enabled", False):
            tasks.append("Challenge")
        
        # If no tasks enabled, don't show timer
        if len(tasks) == 0:
            return {"enabled": False, "remaining": 0, "tasks": []}
        
        # Get last challenge time from macro engine if it exists
        last_time = 0
        if hasattr(self, 'engine') and self.engine:
            last_time = getattr(self.engine, 'last_challenge_time', 0)
        
        # Calculate remaining time
        if last_time == 0:
            # No previous run, show full time
            remaining = CHALLENGE_INTERVAL
        else:
            elapsed = time.time() - last_time
            remaining = max(0, CHALLENGE_INTERVAL - elapsed)
        
        return {
            "enabled": True,  # Show timer whenever tasks are enabled
            "remaining": int(remaining),
            "tasks": tasks
        }
    
    def save_placement_timing(self, between_delay, confirm_delay):
        """Save placement timing delays"""
        self.config["placement_between_delay"] = float(between_delay)
        self.config["placement_confirm_delay"] = float(confirm_delay)
        save_config(self.config)
        print(f"Placement timing updated - between: {between_delay}s, confirm: {confirm_delay}s")
        return True
    
    def save_zoom_duration(self, story_zoom, legend_zoom, raids_zoom, siege_zoom, challenges_zoom, custom_zoom=0.3, portals_zoom=0.3):
        """Save zoom duration for each mode"""
        self.config["zoom_duration_story"] = float(story_zoom)
        self.config["zoom_duration_legend"] = float(legend_zoom)
        self.config["zoom_duration_raids"] = float(raids_zoom)
        self.config["zoom_duration_siege"] = float(siege_zoom)
        self.config["zoom_duration_auto-challenges"] = float(challenges_zoom)
        self.config["zoom_duration_custom"] = float(custom_zoom)
        self.config["zoom_duration_portals"] = float(portals_zoom)
        save_config(self.config)
        print(f"Zoom duration updated - Story: {story_zoom}s, Legend: {legend_zoom}s, Raids: {raids_zoom}s, Siege: {siege_zoom}s, Auto-Challenges: {challenges_zoom}s, Custom: {custom_zoom}s, Portals: {portals_zoom}s")
        return True
    
    def save_tolerance_settings(self, use_avg_tolerance, avg_tolerance):
        """Save image match tolerance settings"""
        self.config["use_avg_tolerance"] = bool(use_avg_tolerance)
        self.config["avg_tolerance"] = float(avg_tolerance)
        save_config(self.config)
        print(f"Tolerance settings updated - use_avg: {use_avg_tolerance}, avg_tolerance: {avg_tolerance}")
        return True
    
    def calculate_best_tolerance(self):
        """Calculate the best average tolerance by testing areas.png at multiple levels.
        Picks the highest tolerance that still finds a match (stricter = better)."""
        try:
            from mouse_controller import find_image_on_screen
            import time
            
            test_image = os.path.join(get_base_path(), "buttons", "Areas.png")
            if not os.path.exists(test_image):
                print(f"Cannot find test image: {test_image}")
                return None
            
            # Tolerance levels to test (from most lenient to most strict)
            test_tolerances = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0]
            
            region = None
            # Check if Roblox is attached/embedded
            if self._roblox_hwnd and IsWindow(self._roblox_hwnd):
                rect = wintypes.RECT()
                GetWindowRect(self._roblox_hwnd, ctypes.byref(rect))
                region = (rect.left, rect.top, rect.right, rect.bottom)
                print(f"Using attached Roblox window region: {region}")
            # Fall back to engine detection
            elif self.engine and hasattr(self.engine, 'roblox_region') and self.engine.roblox_region:
                region = self.engine.roblox_region
                print(f"Using engine Roblox region: {region}")
            
            if not region:
                print("Cannot calculate tolerance: No Roblox window detected. Please attach Roblox first.")
                return {"error": "No Roblox window detected. Please attach Roblox first."}
            
            results = {}
            
            print("Calculating best tolerance level using Areas.png...")
            print(f"Testing {len(test_tolerances)} tolerance levels")
            
            for tolerance in test_tolerances:
                try:
                    result = find_image_on_screen(test_image, confidence=tolerance, region=region)
                    found = result is not None
                    results[tolerance] = found
                    print(f"  Tolerance {tolerance}: {'✓ Found' if found else '✗ Not found'}")
                    time.sleep(0.05)
                except Exception as e:
                    print(f"  Tolerance {tolerance}: ✗ Error - {e}")
                    results[tolerance] = False
            
            # Find all tolerances that successfully matched
            matching_tolerances = [t for t, found in results.items() if found]
            
            if matching_tolerances:
                # Pick the highest tolerance among those that matched (strictest = best)
                best_tolerance = max(matching_tolerances)
                print(f"Found {len(matching_tolerances)} working tolerance(s): {sorted(matching_tolerances)}")
                print(f"Best tolerance determined: {best_tolerance} (highest/strictest)")
            else:
                # No matches found at any tolerance level - use conservative default
                best_tolerance = 0.65
                print(f"No matches found at any tolerance level!")
                print(f"Using default fallback: {best_tolerance}")
            
            return {
                "best_tolerance": best_tolerance,
                "test_count": len(test_tolerances),
                "results": {str(k): v for k, v in results.items()}
            }
        except Exception as e:
            print(f"Error calculating best tolerance: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def browse_background_image(self):
        """Open file browser to select background image (Free version - no GIF animation)"""
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            file_path = filedialog.askopenfilename(
                title="Select Background Image",
                filetypes=[
                    ("Image files", "*.png *.jpg *.jpeg *.bmp"),
                    ("All files", "*.*")
                ]
            )
            root.destroy()
            if file_path:
                # Check if it's a GIF and warn the user
                if file_path.lower().endswith('.gif'):
                    print("WARNING: Animated GIFs are only supported in the Premium version.")
                    print("The GIF will display as a static image in the free version.")
                return {"path": file_path, "data_uri": self._image_to_data_uri(file_path)}
            return {"path": None, "data_uri": None}
        except Exception as e:
            print(f"Error browsing for image: {e}")
            return {"path": None, "data_uri": None}
    
    def _image_to_data_uri(self, file_path):
        """Convert an image file to a base64 data URI"""
        import base64
        import os
        try:
            ext = os.path.splitext(file_path)[1].lower()
            mime_map = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.bmp': 'image/bmp',
                '.webp': 'image/webp'
            }
            mime = mime_map.get(ext, 'image/png')
            with open(file_path, 'rb') as f:
                data = base64.b64encode(f.read()).decode('utf-8')
            return f'data:{mime};base64,{data}'
        except Exception as e:
            print(f"Error converting image to data URI: {e}")
            return None
    
    def get_background_data_uri(self):
        """Get data URI for the currently configured background image"""
        bg_path = self.config.get("background_image", "")
        if bg_path:
            import os
            if os.path.exists(bg_path):
                return {"data_uri": self._image_to_data_uri(bg_path)}
        return {"data_uri": None}
    
    def save_appearance_settings(self, settings):
        """Save appearance customization settings"""
        try:
            self.config["background_image"] = settings.get("background_image", "")
            self.config["background_opacity"] = int(settings.get("background_opacity", 20))
            self.config["ui_opacity"] = int(settings.get("ui_opacity", 100))
            self.config["custom_color"] = settings.get("custom_color", "#f97316")
            self.config["text_size"] = int(settings.get("text_size", 14))
            self.config["text_color"] = settings.get("text_color", "#e0e6ff")
            self.config["secondary_color"] = settings.get("secondary_color", "#8b5cf6")
            self.config["button_color"] = settings.get("button_color", "#f97316")
            self.config["bg_color"] = settings.get("bg_color", "#050510")
            self.config["gradient_enabled"] = bool(settings.get("gradient_enabled", False))
            self.config["gradient_text_enabled"] = bool(settings.get("gradient_text_enabled", False))
            self.config["gradient_start"] = settings.get("gradient_start", "#f97316")
            self.config["gradient_end"] = settings.get("gradient_end", "#ea580c")
            self.config["gradient_angle"] = settings.get("gradient_angle", "135deg")
            self.config["text_brightness"] = int(settings.get("text_brightness", 100))
            self.config["neon_enabled"] = bool(settings.get("neon_enabled", False))
            self.config["neon_intensity"] = int(settings.get("neon_intensity", 8))
            save_config(self.config)
            print(f"Appearance settings saved - bg: {'[set]' if settings.get('background_image') else '[none]'}, opacity: {settings.get('background_opacity')}%, ui_opacity: {settings.get('ui_opacity')}%, color: {settings.get('custom_color')}, gradient: {settings.get('gradient_enabled')}")
            return True
        except Exception as e:
            print(f"Error saving appearance settings: {e}")
            return False
    
    def set_save_on_exit(self, enabled):
        """Toggle save-on-exit setting"""
        self.config["save_on_exit"] = bool(enabled)
        save_config(self.config)
        print(f"Save on exit: {'enabled' if enabled else 'disabled'}")
        return True
    
    def save_custom_theme(self, theme_data):
        """Save a custom theme with all appearance settings"""
        try:
            if "custom_themes" not in self.config:
                self.config["custom_themes"] = []
            
            # Remove existing theme with same id
            self.config["custom_themes"] = [
                t for t in self.config["custom_themes"] 
                if t.get("id") != theme_data.get("id")
            ]
            
            # Add new theme
            self.config["custom_themes"].append(theme_data)
            save_config(self.config)
            print(f"Custom theme saved: {theme_data.get('name')}")
            return True
        except Exception as e:
            print(f"Error saving custom theme: {e}")
            return False
    
    def load_custom_theme(self, theme_id):
        """Load a custom theme by ID"""
        try:
            themes = self.config.get("custom_themes", [])
            for theme in themes:
                if theme.get("id") == theme_id:
                    return theme
            return None
        except Exception as e:
            print(f"Error loading custom theme: {e}")
            return None
    
    def delete_custom_theme(self, theme_id):
        """Delete a custom theme by ID"""
        try:
            if "custom_themes" not in self.config:
                return False
            
            original_count = len(self.config["custom_themes"])
            self.config["custom_themes"] = [
                t for t in self.config["custom_themes"] 
                if t.get("id") != theme_id
            ]
            
            if len(self.config["custom_themes"]) < original_count:
                save_config(self.config)
                print(f"Custom theme deleted: {theme_id}")
                return True
            return False
        except Exception as e:
            print(f"Error deleting custom theme: {e}")
            return False
    
    def get_custom_themes(self):
        """Get all custom themes"""
        try:
            return self.config.get("custom_themes", [])
        except Exception as e:
            print(f"Error getting custom themes: {e}")
            return []
    
    def export_theme_to_file(self, theme_data):
        """Export theme data to a JSON file using file dialog"""
        try:
            import tkinter as tk
            from tkinter import filedialog
            import json
            import re
            
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            
            # Get theme name for default filename
            theme_name = theme_data.get('name', 'theme')
            # Replace spaces with dashes and remove non-alphanumeric characters
            default_filename = re.sub(r'[^a-zA-Z0-9-]', '', theme_name.replace(' ', '-')) + '.json'
            
            file_path = filedialog.asksaveasfilename(
                title="Export Theme",
                defaultextension=".json",
                initialfile=default_filename,
                filetypes=[
                    ("JSON files", "*.json"),
                    ("All files", "*.*")
                ]
            )
            root.destroy()
            
            if file_path:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(theme_data, f, indent=2)
                return {"success": True, "path": file_path}
            return {"success": False, "message": "Export cancelled"}
        except Exception as e:
            print(f"Error exporting theme: {e}")
            return {"success": False, "message": str(e)}
    
    def import_theme_from_file(self):
        """Import theme data from a JSON file using file dialog"""
        try:
            import tkinter as tk
            from tkinter import filedialog
            import json
            
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            
            file_path = filedialog.askopenfilename(
                title="Import Theme",
                filetypes=[
                    ("JSON files", "*.json"),
                    ("All files", "*.*")
                ]
            )
            root.destroy()
            
            if file_path:
                with open(file_path, 'r', encoding='utf-8') as f:
                    theme_data = json.load(f)
                return {"success": True, "data": theme_data}
            return {"success": False, "message": "Import cancelled"}
        except Exception as e:
            print(f"Error importing theme: {e}")
            return {"success": False, "message": str(e)}
    
    def open_private_server_link(self):
        """Open the private server link directly via Roblox protocol (no browser)"""
        import os
        import re
        link = self.config.get("private_server_link", "")
        if not link:
            print("No private server link configured")
            return False
        try:
            # Convert web URL to Roblox protocol URL
            # Example: https://www.roblox.com/games/15468878005?privateServerLinkCode=12345
            # Becomes: roblox://placeId=15468878005&linkCode=12345
            
            roblox_url = link
            
            # Check if it's already a roblox:// URL
            if not link.startswith("roblox://"):
                # Extract place ID and private server link code from URL
                place_match = re.search(r'/games/(\d+)', link)
                code_match = re.search(r'privateServerLinkCode=([a-zA-Z0-9_-]+)', link)
                
                if place_match:
                    place_id = place_match.group(1)
                    if code_match:
                        link_code = code_match.group(1)
                        roblox_url = f"roblox://placeId={place_id}&linkCode={link_code}"
                    else:
                        roblox_url = f"roblox://placeId={place_id}"
                else:
                    # Fallback to browser if we can't parse the URL
                    import webbrowser
                    webbrowser.open(link)
                    print(f"Could not parse URL, opening in browser: {link}")
                    return True
            
            # Launch directly via Windows shell
            os.startfile(roblox_url)
            print(f"Launching Roblox directly: {roblox_url}")
            return True
        except Exception as e:
            print(f"Error opening private server link: {e}")
            # Fallback to browser
            try:
                import webbrowser
                webbrowser.open(link)
                return True
            except:
                return False
    
    def update_stats(self, is_win):
        """Update win/loss stats and return new values"""
        if is_win:
            self.config["stats_wins"] = self.config.get("stats_wins", 0) + 1
        else:
            self.config["stats_losses"] = self.config.get("stats_losses", 0) + 1
        save_config(self.config)
        return {
            "wins": self.config.get("stats_wins", 0),
            "losses": self.config.get("stats_losses", 0)
        }
    
    def _start_macro_callback(self):
        """Callback for start hotkey"""
        print("Start hotkey pressed!")
        
        # Reload config to get latest mode
        self.config = load_config()
        current_mode = self.config.get("mode", "Story")
        
        # Prevent starting if Auto-Challenges is selected
        if current_mode == "Auto-Challenges":
            self._status_callback("Cannot start macro with Auto-Challenges mode. Auto-Challenges runs as a side task with other modes.")
            print("Cannot start macro - Auto-Challenges mode selected")
            return
        
        if not self.engine or not self.engine.running:
            # Auto-save unit config before starting
            try:
                if self._window:
                    self._window.evaluate_js('saveUnitConfig()')
                    print("Auto-saved unit config before macro start")
            except Exception as e:
                print(f"Could not auto-save unit config: {e}")
            self._status_callback("Macro started via hotkey!")
            self._start_macro_internal()
        else:
            self._status_callback("Macro already running")
            print("Macro already running")
    
    def _stop_macro_callback(self):
        """Callback for stop hotkey"""
        print("Stop hotkey pressed!")
        if self.engine and self.engine.running:
            self.engine.stop()
            self._status_callback("Macro stopped via hotkey")
        else:
            self._status_callback("Macro not running")
            print("Macro not running")
    

    

    

    
    def attach_roblox(self):
        """Find and attach Roblox window"""
        def find_roblox_window():
            result = []
            ROBLOX_CLASSES = {'windowsclient', 'robloxplayerbeta'}
            GetClassName = user32.GetClassNameW
            IsWindowVisible = user32.IsWindowVisible
            
            def enum_callback(hwnd, lParam):
                if not IsWindowVisible(hwnd):
                    return True
                
                # Check window class name first (most reliable)
                class_buff = ctypes.create_unicode_buffer(256)
                GetClassName(hwnd, class_buff, 256)
                class_name = class_buff.value.lower()
                
                if class_name in ROBLOX_CLASSES:
                    result.append(hwnd)
                    return True
                
                # Fallback: check window title
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buff, length + 1)
                    title = buff.value
                    if 'roblox' in title.lower():
                        result.append(hwnd)
                return True
            
            EnumWindows(EnumWindowsProc(enum_callback), 0)
            return result[0] if result else None
        
        hwnd = find_roblox_window()
        if not hwnd:
            return {"success": False, "message": "Roblox window not found. Make sure Roblox is running!"}
        
        # Get the pywebview window handle
        try:
            import time
            time.sleep(0.1)  # Give window time to render
            
            # Find our webview window
            webview_hwnd = None
            def find_webview():
                result = []
                def enum_cb(h, lp):
                    length = GetWindowTextLength(h)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(h, buff, length + 1)
                        if 'Anime Paradox Free by Ryan' in buff.value:
                            result.append(h)
                    return True
                EnumWindows(EnumWindowsProc(enum_cb), 0)
                return result[0] if result else None
            
            webview_hwnd = find_webview()
            if not webview_hwnd:
                return {"success": False, "message": "Could not find app window handle"}
            
            # Compute container offsets dynamically from ui.html to stay in sync with CSS
            ui_path = os.path.join(get_base_path(), 'ui.html')
            main_panel_width = 550
            body_padding = 15
            gap = 15
            border_adj = 5
            try:
                if os.path.exists(ui_path):
                    with open(ui_path, 'r', encoding='utf-8') as fh:
                        content = fh.read()
                    import re
                    m = re.search(r"\.main-panel\s*\{[^}]*width:\s*(\d+)px", content)
                    if m:
                        main_panel_width = int(m.group(1))
            except Exception:
                pass

            settings_width = main_panel_width + body_padding + gap + border_adj
            fixed_game_width = 960  # Fixed width for Roblox window (16:10 aspect)
            fixed_game_height = 600  # Fixed height for Roblox window
            header_height = 60  # Header for game container

            # Try to account for DPI scaling of the webview window so child positioning matches CSS pixels
            try:
                # Get DPI for webview window if available (Windows 10+)
                GetDpiForWindow = user32.GetDpiForWindow
                dpi = GetDpiForWindow(webview_hwnd)
            except Exception:
                try:
                    # Fallback: use desktop DPI
                    hdc = user32.GetDC(0)
                    gdi32 = ctypes.windll.gdi32
                    LOGPIXELSX = 88
                    dpi = gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
                    user32.ReleaseDC(0, hdc)
                except Exception:
                    dpi = 96

            scale = float(dpi) / 96.0 if dpi else 1.0
            # Scale CSS pixel measurements to device pixels for positioning only
            scaled_settings_width = int(settings_width * scale)
            scaled_header_height = int(header_height * scale)

            # Keep the actual Roblox game window at the original resolution so templates match
            unscaled_game_width = fixed_game_width
            unscaled_game_height = fixed_game_height

            total_width = scaled_settings_width + unscaled_game_width + 30  # Extra padding
            total_height = max(750, unscaled_game_height + scaled_header_height + 50)
            
            # Resize main window first
            if self._window:
                self._window.resize(total_width, total_height)
                time.sleep(0.3)  # Let window resize
            
            # Store original parent
            GetParent = user32.GetParent
            self._original_parent = GetParent(hwnd)
            
            # Remove Roblox window border/titlebar to make it fit exactly
            GWL_STYLE = -16
            WS_POPUP = 0x80000000
            WS_VISIBLE = 0x10000000
            WS_CLIPSIBLINGS = 0x04000000
            WS_CLIPCHILDREN = 0x02000000
            
            # Get current style and modify it
            GetWindowLong = user32.GetWindowLongW
            SetWindowLong = user32.SetWindowLongW
            original_style = GetWindowLong(hwnd, GWL_STYLE)
            
            # Set borderless style for exact fit
            new_style = WS_POPUP | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN
            SetWindowLong(hwnd, GWL_STYLE, new_style)
            
            # Set Roblox as child of our window
            SetParent(hwnd, webview_hwnd)

            # Position and FORCE RESIZE Roblox window to fit exactly in the container
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020  # Apply style changes
            # If the webview client area has an offset or DPI scaling, place the Roblox window
            # at the scaled settings offset but keep its size unscaled so templates remain valid.
            SetWindowPos(hwnd, 0, scaled_settings_width, scaled_header_height, unscaled_game_width, unscaled_game_height, 
                        SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)
            ShowWindow(hwnd, SW_SHOW)
            
            self._roblox_hwnd = hwnd
            self._original_style = original_style  # Store for restoration
            # Update engine with exact Roblox region so image detection uses correct coords
            try:
                # After positioning, read back the actual window rect and update engine region
                rect = wintypes.RECT()
                GetWindowRect(hwnd, ctypes.byref(rect))
                region = (rect.left, rect.top, rect.right, rect.bottom)
                print(f"DEBUG: Attached Roblox region set to: {region}")
                if self.engine:
                    self.engine.roblox_region = region
            except Exception as e:
                print(f"DEBUG: Could not set engine.roblox_region: {e}")
            return {"success": True, "message": "Roblox window attached!"}
            
        except Exception as e:
            return {"success": False, "message": f"Error embedding window: {str(e)}"}
    
    def detach_roblox(self):
        """Detach Roblox window"""
        if self._roblox_hwnd and IsWindow(self._roblox_hwnd):
            try:
                # Restore original parent
                if self._original_parent:
                    SetParent(self._roblox_hwnd, self._original_parent)
                else:
                    # Set to desktop if no original parent
                    SetParent(self._roblox_hwnd, 0)
                
                # Reset window position
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                SWP_SHOWWINDOW = 0x0040
                SWP_FRAMECHANGED = 0x0020
                
                # Restore original window style if we saved it
                if hasattr(self, '_original_style') and self._original_style:
                    GWL_STYLE = -16
                    SetWindowLong = user32.SetWindowLongW
                    SetWindowLong(self._roblox_hwnd, GWL_STYLE, self._original_style)
                
                SetWindowPos(self._roblox_hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW | SWP_FRAMECHANGED)
                
            except Exception as e:
                pass
        
        self._roblox_hwnd = None
        self._original_parent = None
        self._original_style = None
        return {"success": True}
    
    def _cleanup_embedded_roblox(self):
        """On startup, find and detach any Roblox windows that might be embedded"""
        try:
            ROBLOX_CLASSES = {'WindowsClient', 'RobloxPlayerBeta'}
            WS_OVERLAPPEDWINDOW = 0x00CF0000
            GWL_STYLE = -16
            
            def enum_callback(hwnd, lParam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                
                class_buff = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buff, 256)
                class_name = class_buff.value
                
                if class_name in ROBLOX_CLASSES:
                    # Check if it has a non-desktop parent (might be embedded)
                    parent = user32.GetParent(hwnd)
                    if parent != 0:
                        # Unparent it to desktop and restore normal window style
                        user32.SetParent(hwnd, 0)
                        user32.SetWindowLongW(hwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW)
                        user32.ShowWindow(hwnd, SW_SHOW)
                        print(f"Cleaned up embedded Roblox window: {hwnd}")
                    else:
                        # Also restore window style for desktop-parented windows
                        user32.SetWindowLongW(hwnd, GWL_STYLE, WS_OVERLAPPEDWINDOW)
                return True
            
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            user32.EnumWindows(EnumWindowsProc(enum_callback), 0)
        except Exception as e:
            print(f"Error during Roblox cleanup: {e}")
    
    def start_macro(self, config_update):
        """Start the macro with updated configuration"""
        # Auto-save unit config before starting
        try:
            if self._window:
                self._window.evaluate_js('saveUnitConfig()')
                print("Auto-saved unit config before macro start")
        except Exception as e:
            print(f"Could not auto-save unit config: {e}")
        
        # Reload config from file first
        self.config = load_config()
        
        # Update config with new values
        self.config["mode"] = config_update.get("mode", "Story")
        self.config["location"] = config_update.get("location", "Leaf Village")
        self.config["act"] = config_update.get("act", "Act 1")
        save_config(self.config)
        
        # Start macro
        self._start_macro_internal()
        return {"success": True}
    
    def _start_macro_internal(self):
        """Internal method to start macro"""
        if self.engine and self.engine.running:
            return

        self.engine = MacroEngine(self.config, self._status_callback, self.attach_roblox)
        # If we have an attached Roblox window, set engine.roblox_region before starting
        try:
            if self._roblox_hwnd and IsWindow(self._roblox_hwnd):
                rect = wintypes.RECT()
                GetWindowRect(self._roblox_hwnd, ctypes.byref(rect))
                region = (rect.left, rect.top, rect.right, rect.bottom)
                print(f"DEBUG: Setting engine.roblox_region from attached hwnd: {region}")
                self.engine.roblox_region = region
        except Exception as e:
            print(f"DEBUG: Could not set engine.roblox_region before start: {e}")

        self.engine.start()
    
    def stop_macro(self):
        """Stop the macro"""
        if self.engine:
            self.engine.stop()
    
    def _status_callback(self, message):
        """Callback for status updates from macro engine"""
        self._status_queue.put(message)
    
    def get_status_updates(self):
        """Get pending status updates"""
        updates = []
        while not self._status_queue.empty():
            try:
                updates.append(self._status_queue.get_nowait())
            except queue.Empty:
                break
        return updates
    

    

    
    def get_config(self):
        """Get full config for UI"""
        return self.config
    
    def update_story_config(self, mode, location, act, nightmare=False, auto_challenges_enabled=False, buy_rrs_enabled=False):
        """Update story mode configuration"""
        self.config["mode"] = mode
        self.config["location"] = location
        self.config["act"] = act
        self.config["nightmare"] = nightmare
        self.config["auto_challenges_enabled"] = auto_challenges_enabled
        self.config["buy_rrs_enabled"] = buy_rrs_enabled
        # Store challenge location separately for Auto-Challenges mode
        if mode == "Auto-Challenges":
            self.config["challenge_location"] = location
        # Store portal selection for Portals mode
        if mode == "Portals":
            self.config["portal_selection"] = location
        save_config(self.config)
        print(f"Config updated: mode={mode}, location={location}, act={act}, nightmare={nightmare}, auto_challenges={auto_challenges_enabled}, buy_rrs={buy_rrs_enabled}")
        return True
    
    def update_ui_theme(self, theme):
        """Update UI theme configuration"""
        self.config["ui_theme"] = theme
        save_config(self.config)
        print(f"UI theme updated: {theme}")
        return True
    
    def get_unit_config_template(self):
        """Get blank unit config template"""
        return {
            "Units": [
                {
                    "Index": i,
                    "Enabled": False,
                    "PlaceBeforeYes": False,
                    "AutoUpgrade": False,
                    "Action": "Place",
                    "Slot": "1",
                    "X": "",
                    "Y": "",
                    "Upgrade": "0",
                    "WaitSeconds": "0",
                    "Note": f"Unit {i}",
                    "Preset": ""
                }
                for i in range(1, 31)  # 30 unit slots
            ]
        }
    
    def get_unit_config_path(self, location, act, mode="Story"):
        """Get the path for unit config based on location, act, and mode"""
        # Map mode to folder structure
        if mode == "Raids":
            settings_folder = os.path.join(get_app_path(), "Settings", "Raid", location)
        elif mode == "Siege":
            settings_folder = os.path.join(get_app_path(), "Settings", "Siege", location)
        elif mode == "Portals":
            settings_folder = os.path.join(get_app_path(), "Settings", "Portals", location)
        elif mode == "Auto-Challenges":
            settings_folder = os.path.join(get_app_path(), "Settings", "Challenges", location)
        elif mode == "Legend":
            settings_folder = os.path.join(get_app_path(), "Settings", "Legend", location)
        elif mode == "Custom":
            settings_folder = os.path.join(get_app_path(), "Settings", "Custom", location)
        else:
            settings_folder = os.path.join(get_app_path(), "Settings", "Story", location)
        os.makedirs(settings_folder, exist_ok=True)
        return os.path.join(settings_folder, f"{act}.json")
    
    def load_unit_config(self, location, act, mode="Story"):
        """Load unit configuration for a location and act"""
        import json
        config_path = self.get_unit_config_path(location, act, mode)
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return self.get_unit_config_template()
        else:
            # Create blank config
            template = self.get_unit_config_template()
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(template, f, indent=4)
            return template
    
    def save_unit_config(self, location, act, config_data, mode="Story"):
        """Save unit configuration"""
        import json
        config_path = self.get_unit_config_path(location, act, mode)
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
        
        print(f"Unit config saved to: {config_path}")
        return True
    
    # === CUSTOM CONFIG MANAGEMENT ===
    
    def get_custom_configs(self):
        """Get list of custom config names"""
        custom_dir = os.path.join(get_app_path(), "Settings", "Custom")
        if not os.path.exists(custom_dir):
            return []
        configs = []
        for name in sorted(os.listdir(custom_dir)):
            folder_path = os.path.join(custom_dir, name)
            if os.path.isdir(folder_path):
                configs.append(name)
        return configs
    
    def create_custom_config(self, name):
        """Create a new custom config with a blank unit template"""
        import json
        custom_dir = os.path.join(get_app_path(), "Settings", "Custom", name)
        os.makedirs(custom_dir, exist_ok=True)
        config_path = os.path.join(custom_dir, "Act 1.json")
        if not os.path.exists(config_path):
            template = self.get_unit_config_template()
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(template, f, indent=4)
        print(f"Custom config created: {name}")
        return True
    
    def delete_custom_config(self, name):
        """Delete a custom config folder"""
        import shutil
        custom_dir = os.path.join(get_app_path(), "Settings", "Custom", name)
        if os.path.exists(custom_dir):
            shutil.rmtree(custom_dir)
            print(f"Custom config deleted: {name}")
            return True
        return False
    
    # === PRESET MANAGEMENT ===
    
    def _get_presets_folder(self):
        """Get the presets folder path, creating it if needed"""
        presets_dir = os.path.join(get_app_path(), "presets")
        os.makedirs(presets_dir, exist_ok=True)
        return presets_dir
    
    def get_preset_list(self):
        """Get list of available preset names"""
        presets_dir = self._get_presets_folder()
        presets = []
        for f in sorted(os.listdir(presets_dir)):
            if f.endswith('.json'):
                presets.append(f.replace('.json', ''))
        return presets
    
    def load_preset(self, name):
        """Load a preset by name"""
        import json
        preset_path = os.path.join(self._get_presets_folder(), f"{name}.json")
        if os.path.exists(preset_path):
            try:
                with open(preset_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading preset: {e}")
                return None
        return None
    
    def save_preset(self, name, preset_data):
        """Save a preset"""
        import json
        preset_path = os.path.join(self._get_presets_folder(), f"{name}.json")
        with open(preset_path, 'w', encoding='utf-8') as f:
            json.dump(preset_data, f, indent=4)
        return True
    
    def delete_preset(self, name):
        """Delete a preset"""
        preset_path = os.path.join(self._get_presets_folder(), f"{name}.json")
        if os.path.exists(preset_path):
            os.remove(preset_path)
            return True
        return False
    
    def get_preset_template(self):
        """Get a blank preset template"""
        return {
            "Name": "New Preset",
            "Description": "",
            "Actions": [
                {
                    "Index": i,
                    "Enabled": False,
                    "Type": "Left Click",
                    "Wait": 0.2,
                    "X": 0,
                    "Y": 0,
                    "Text": "",
                    "Key": "w",
                    "HoldDuration": 1.0,
                    "Note": ""
                }
                for i in range(1, 11)
            ]
        }
    
    def import_unit_config(self, location, act, mode="Story"):
        """Import unit configuration from another JSON file"""
        import json
        import tkinter as tk
        from tkinter import filedialog
        
        try:
            # Open file browser to select config file
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            file_path = filedialog.askopenfilename(
                title="Select Config File to Import",
                filetypes=[
                    ("JSON files", "*.json"),
                    ("All files", "*.*")
                ],
                initialdir=os.path.join(get_app_path(), "Settings")
            )
            root.destroy()
            
            if not file_path:
                return {"success": False, "cancelled": True}
            
            # Load the selected config file
            with open(file_path, 'r', encoding='utf-8') as f:
                imported_data = json.load(f)
            
            # Check if it's a valid unit config (has Units array)
            if not isinstance(imported_data, dict) or 'Units' not in imported_data:
                return {
                    "success": False,
                    "error": "Invalid config file format. Must contain 'Units' array."
                }
            
            # Get the unit count
            unit_count = len(imported_data.get('Units', []))
            
            # Save the imported config to the current location/act
            config_path = self.get_unit_config_path(location, act, mode)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(imported_data, f, indent=4)
            
            # Update the macro config to ensure it points to this location/act/mode
            self.config["mode"] = mode
            self.config["location"] = location
            self.config["act"] = act
            save_config(self.config)
            
            print(f"Imported {unit_count} units from {file_path} to {config_path}")
            
            return {
                "success": True,
                "unit_count": unit_count,
                "source_file": os.path.basename(file_path)
            }
            
        except Exception as e:
            print(f"Error importing unit config: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def get_map_preview_path(self, location, act, mode="Story"):
        """Get the map preview image as base64 data URL"""
        import base64
        
        # Determine the settings folder based on mode
        if mode == "Raids":
            settings_folder = os.path.join(get_app_path(), "Settings", "Raid", location)
            starting_folder = os.path.join(get_app_path(), "starting image", "Raid", location)
        elif mode == "Siege":
            settings_folder = os.path.join(get_app_path(), "Settings", "Siege", location)
            starting_folder = os.path.join(get_app_path(), "starting image", "Siege", location)
        elif mode == "Portals":
            # Portals uses portal_selection instead of location
            portal_selection = self.config.get("portal_selection", "JJK Portal")
            settings_folder = os.path.join(get_app_path(), "Settings", "Portals", portal_selection)
            starting_folder = os.path.join(get_app_path(), "starting image", "Portals", portal_selection)
        elif mode == "Auto-Challenges":
            settings_folder = os.path.join(get_app_path(), "Settings", "Challenges", location)
            folder_name = self._get_location_key(location)
            # Use Story folder for starting images (Auto-Challenges uses same maps as Story)
            starting_folder = os.path.join(get_app_path(), "starting image", "Story", folder_name)
        elif mode == "Legend":
            settings_folder = os.path.join(get_app_path(), "Settings", "Legend", location)
            folder_name = self._get_location_key(location)
            # Use Story folder for starting images (Legend uses same maps as Story)
            starting_folder = os.path.join(get_app_path(), "starting image", "Story", folder_name)
        else:
            settings_folder = os.path.join(get_app_path(), "Settings", "Story", location)
            folder_name = self._get_location_key(location)
            starting_folder = os.path.join(get_app_path(), "starting image", "Story", folder_name)
        
        # Check Settings folder first (where coordinate picker saves screenshots)
        if os.path.exists(settings_folder):
            for filename in os.listdir(settings_folder):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    image_path = os.path.join(settings_folder, filename)
                    try:
                        with open(image_path, 'rb') as f:
                            img_data = base64.b64encode(f.read()).decode('utf-8')
                        ext = filename.lower().split('.')[-1]
                        mime = 'image/jpeg' if ext in ['jpg', 'jpeg'] else 'image/png'
                        return {"success": True, "path": f"data:{mime};base64,{img_data}"}
                    except Exception as e:
                        print(f"Error reading image: {e}")
        
        # Fallback to starting image folder
        if os.path.exists(starting_folder):
            for filename in os.listdir(starting_folder):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    image_path = os.path.join(starting_folder, filename)
                    try:
                        with open(image_path, 'rb') as f:
                            img_data = base64.b64encode(f.read()).decode('utf-8')
                        ext = filename.lower().split('.')[-1]
                        mime = 'image/jpeg' if ext in ['jpg', 'jpeg'] else 'image/png'
                        return {"success": True, "path": f"data:{mime};base64,{img_data}"}
                    except Exception as e:
                        print(f"Error reading image: {e}")
        
        return {"success": False, "path": None}
    
    def get_roblox_window_info(self):
        """Get the current Roblox window position and size"""
        if self._roblox_hwnd and IsWindow(self._roblox_hwnd):
            rect = wintypes.RECT()
            GetWindowRect(self._roblox_hwnd, ctypes.byref(rect))
            return {
                "x": rect.left,
                "y": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top
            }
        return {"x": 0, "y": 0, "width": 960, "height": 600}
    
    def open_coordinate_picker(self, mode, location, act, unit_index):
        """Open coordinate picker for a specific unit"""
        import subprocess
        import sys
        import json
        
        # Map mode to settings folder
        mode_folder_map = {
            'Story': 'Story',
            'Legend': 'Legend',  # Legend has its own folder now
            'Raids': 'Raid',
            'Siege': 'Siege',
            'Auto-Challenges': 'Challenges'
        }
        settings_mode = mode_folder_map.get(mode, 'Story')
        
        print(f"DEBUG open_coordinate_picker: mode={mode}, settings_mode={settings_mode}, location={location}, act={act}")
        
        # Get image folder for the location
        image_folder = self._get_image_folder_path()
        image_path = None
        
        if os.path.exists(image_folder):
            for filename in os.listdir(image_folder):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    image_path = os.path.join(image_folder, filename)
                    break
        
        if not image_path:
            return {"success": False, "message": "No screenshot found. Take a screenshot first (F4)."}
        
        # Load existing unit coordinates to display in picker
        config_path = os.path.join(get_app_path(), "Settings", settings_mode, location, f"{act}.json")
        other_units = []
        
        print(f"DEBUG: Looking for unit config at: {config_path}")
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                for unit in config.get("Units", []):
                    # Show all units that have coordinates set (not just enabled ones)
                    if unit.get("X") and unit.get("Y"):
                        try:
                            other_units.append({
                                "index": unit["Index"],
                                "x": int(unit["X"]),
                                "y": int(unit["Y"]),
                                "note": unit.get("Note", f"Unit {unit['Index']}")
                            })
                        except (ValueError, KeyError):
                            pass
                print(f"DEBUG: Found {len(other_units)} units with coordinates")
            except Exception as e:
                print(f"Error loading unit config: {e}")
        else:
            print(f"DEBUG: Config file does not exist: {config_path}")
        
        # Launch coordinate picker
        # Handle PyInstaller bundled scenario
        script_path = os.path.join(get_base_path(), "coordinate_picker.py")
        
        # Get the actual position of the Roblox window on screen
        if self._roblox_hwnd and IsWindow(self._roblox_hwnd):
            rect = wintypes.RECT()
            GetWindowRect(self._roblox_hwnd, ctypes.byref(rect))
            roblox_x = rect.left
            roblox_y = rect.top
            roblox_width = rect.right - rect.left
            roblox_height = rect.bottom - rect.top
        else:
            # Fallback to default embedded position - compute offsets from ui.html
            ui_path = os.path.join(get_base_path(), 'ui.html')
            main_panel_width = 550
            body_padding = 15
            gap = 15
            border_adj = 5
            try:
                if os.path.exists(ui_path):
                    with open(ui_path, 'r', encoding='utf-8') as fh:
                        content = fh.read()
                    import re
                    m = re.search(r"\.main-panel\s*\{[^}]*width:\s*(\d+)px", content)
                    if m:
                        main_panel_width = int(m.group(1))
            except Exception:
                pass

            settings_width = main_panel_width + body_padding + gap + border_adj
            roblox_x = settings_width
            roblox_y = 60
            roblox_width = 960
            roblox_height = 600
        
        try:
            # Pass other units as JSON argument
            other_units_json = json.dumps(other_units)
            
            # When running as frozen exe, run coordinate picker in-process
            # because sys.executable points to the exe, not Python
            if getattr(sys, 'frozen', False):
                # Run coordinate picker directly using imported class
                import io
                import tkinter as tk
                import traceback
                
                old_stdout = sys.stdout
                old_stderr = sys.stderr
                sys.stdout = captured_output = io.StringIO()
                sys.stderr = captured_errors = io.StringIO()
                
                picker_error = None
                try:
                    # Force cleanup of ALL tkinter state before starting
                    try:
                        # Destroy any existing default root
                        if tk._default_root is not None:
                            try:
                                tk._default_root.destroy()
                            except:
                                pass
                        tk._default_root = None
                        tk._support_default_root = True
                    except:
                        pass
                    
                    print(f"DEBUG: Creating picker with image={image_path}")
                    print(f"DEBUG: other_units count={len(other_units)}")
                    
                    picker = CoordinatePicker(
                        image_path, settings_mode, location, act, unit_index,
                        roblox_x, roblox_y, roblox_width, roblox_height, other_units
                    )
                    print("DEBUG: Picker created, calling run()")
                    picker.run()
                    print("DEBUG: Picker run() completed")
                    output = captured_output.getvalue()
                except Exception as e:
                    picker_error = f"Picker error: {e}\n{traceback.format_exc()}"
                    output = ""
                finally:
                    sys.stdout = old_stdout
                    sys.stderr = old_stderr
                    
                    # Log any captured output/errors
                    captured_out = captured_output.getvalue()
                    captured_err = captured_errors.getvalue()
                    if captured_out:
                        print(f"DEBUG captured stdout: {captured_out}")
                    if captured_err:
                        print(f"DEBUG captured stderr: {captured_err}")
                    if picker_error:
                        print(picker_error)
                    
                    # Aggressively clean up tkinter state
                    try:
                        if tk._default_root is not None:
                            try:
                                tk._default_root.quit()
                            except:
                                pass
                            try:
                                tk._default_root.destroy()
                            except:
                                pass
                        tk._default_root = None
                        tk._support_default_root = True
                    except:
                        pass
                    # Force garbage collection
                    try:
                        import gc
                        gc.collect()
                    except:
                        pass
                
                if picker_error:
                    return {"success": False, "message": picker_error}
                
                # Parse coordinates from captured output
                for line in output.split('\n'):
                    line = line.strip()
                    if ',' in line and not line.startswith('\u2713'):
                        try:
                            x, y = line.split(',')
                            x = int(x.strip())
                            y = int(y.strip())
                            print(f"Coordinates selected: ({x}, {y})")
                            return {"success": True, "x": x, "y": y}
                        except:
                            continue
                
                return {"success": False, "message": "No coordinates selected"}
            else:
                # Run coordinate picker as subprocess (development mode)
                print(f"DEBUG subprocess: Running picker as subprocess")
                print(f"DEBUG subprocess: script_path={script_path}")
                print(f"DEBUG subprocess: image_path={image_path}")
                print(f"DEBUG subprocess: other_units count={len(other_units)}")
                print(f"DEBUG subprocess: other_units_json length={len(other_units_json)} chars")
                
                # Use temp file for other_units to avoid command line length limits on Windows
                import tempfile
                temp_file = None
                try:
                    # Write other_units to temp file to avoid Windows cmd line limit (~8191 chars)
                    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
                    temp_file.write(other_units_json)
                    temp_file.close()
                    temp_file_path = temp_file.name
                    print(f"DEBUG subprocess: Using temp file {temp_file_path}")
                    
                    result = subprocess.run(
                        [sys.executable, script_path, image_path, settings_mode, location, act, str(unit_index),
                         str(roblox_x), str(roblox_y), str(roblox_width), str(roblox_height), f"@{temp_file_path}"],
                        cwd=get_app_path(),
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    
                    print(f"DEBUG subprocess stdout: {result.stdout}")
                    print(f"DEBUG subprocess stderr: {result.stderr}")
                    print(f"DEBUG subprocess returncode: {result.returncode}")
                    
                    # Parse coordinates from output
                    for line in result.stdout.split('\n'):
                        line = line.strip()
                        if ',' in line and not line.startswith('✓'):
                            try:
                                x, y = line.split(',')
                                x = int(x.strip())
                                y = int(y.strip())
                                print(f"Coordinates selected: ({x}, {y})")
                                return {"success": True, "x": x, "y": y}
                            except:
                                continue
                    
                    return {"success": False, "message": "No coordinates selected"}
                except subprocess.TimeoutExpired:
                    return {"success": False, "message": "Coordinate picker timed out"}
                except Exception as e:
                    print(f"DEBUG subprocess error: {e}")
                    import traceback
                    traceback.print_exc()
                    return {"success": False, "message": f"Subprocess error: {str(e)}"}
                finally:
                    # Clean up temp file
                    if temp_file and os.path.exists(temp_file_path):
                        try:
                            os.unlink(temp_file_path)
                        except:
                            pass
            
        except Exception as e:
            print(f"DEBUG outer error: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def get_version(self):
        """Get the current application version"""
        return {"version": VERSION}
    
    def check_for_updates(self):
        """Updates disabled in free version"""
        return {"success": False, "message": "Auto-updates are only available in the Premium version"}
    
    def install_update(self, download_url):
        """Updates disabled in free version"""
        return {"success": False, "message": "Auto-updates are only available in the Premium version"}
    
    def check_extractors(self):
        """Updates disabled in free version"""
        return {"winrar": False, "sevenzip": False, "message": "Auto-updates are only available in the Premium version"}
    
    def download_winrar(self):
        """Updates disabled in free version"""
        return {"success": False, "message": "Auto-updates are only available in the Premium version"}
    
    def download_7zip(self):
        """Updates disabled in free version"""
        return {"success": False, "message": "Auto-updates are only available in the Premium version"}
    
    def restart_application(self):
        """Updates disabled in free version"""
        return {"success": False, "message": "Auto-updates are only available in the Premium version"}


def main():
    # Check for administrator privileges
    if not is_admin():
        print("Not running as administrator. Requesting elevation...")
        run_as_admin()
        return

    api = MacroAPI()
    
    # Load HTML content
    html_path = os.path.join(get_base_path(), 'ui.html')
    
    # Register hotkeys before window creation
    def setup_hotkeys():
        start_key = api.config.get("start_keybind", "f1")
        stop_key = api.config.get("stop_keybind", "f3")
        api.apply_keybinds(start_key, stop_key)
    
    # Create single window with transparent background
    icon_path = os.path.join(get_base_path(), 'iconmacro.png')
    window = webview.create_window(
        'Anime Paradox Free by Ryan',
        html_path,
        js_api=api,
        width=1300,
        height=850,
        resizable=False,
        transparent=False,
        frameless=False,
        on_top=True
    )
    
    api._window = window
    
    # Start webview
    webview.start(setup_hotkeys, debug=False, icon=icon_path)
    
    # Save on exit if enabled
    try:
        config = load_config()
        if config.get("save_on_exit", False):
            print("Save on exit enabled - saving unit config...")
            # The JS context is gone after webview closes, so we just save the current config
            save_config(config)
            print("Config saved on exit.")
    except Exception as e:
        print(f"Error saving on exit: {e}")
    
    # Cleanup on exit
    if api._hotkeys_registered:
        keyboard.unhook_all_hotkeys()
    if api.engine and api.engine.running:
        api.engine.stop()


if __name__ == "__main__":
    main()
