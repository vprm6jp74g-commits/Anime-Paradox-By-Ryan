"""Core macro engine for the Roblox automation"""
import time
import threading
import ctypes
import os
import sys
import psutil
import keyboard
from ctypes import wintypes
from mouse_controller import (
    click, move_to, hold_key, press_key, hold_key_until_condition,
    get_screen_size, find_image_on_screen, wait_for_image, drag_down,
    hold_key_directinput, right_click, scroll_down
)

# Win32 keypress helper
from mouse_controller import win32_press_key
from config import save_config

def get_app_path():
    """Get the application path for writable data"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(__file__)

def get_button_path(relative_path):
    """Get absolute path to button images"""
    return os.path.join(get_app_path(), relative_path)

def close_roblox_instance(hwnd=None):
    """Close a specific Roblox instance by window handle, or all user's instances if hwnd is None"""
    print(f"Trying to close Roblox instance (hwnd={hwnd})")
    try:
        if hwnd:
            # Get process ID from window handle
            GetWindowThreadProcessId = user32.GetWindowThreadProcessId
            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            
            if pid.value:
                try:
                    process = psutil.Process(pid.value)
                    print(f"Closing Roblox process PID {pid.value}")
                    process.kill()
                    return
                except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                    print(f"Could not close specific process: {e}")
        
        # Fallback: close all user's Roblox instances
        print("Falling back to closing all user Roblox instances")
        current_user = os.getlogin()
        for process in psutil.process_iter(['name', 'username']):
            try:
                proc_name = process.info.get('name', '')
                proc_user = process.info.get('username', '')
                
                if ("RobloxPlayer" in proc_name or "Bloxstrap" in proc_name):
                    # Extract username from domain\username format
                    if proc_user and '\\' in proc_user:
                        proc_user = proc_user.split('\\')[-1]
                    
                    if proc_user == current_user:
                        print(f"Closing {proc_name} instance for user {current_user}")
                        process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                continue
    except Exception as e:
        print(f"Error in close_roblox_instance: {e}")

# Windows API
user32 = ctypes.windll.user32

class MacroEngine:
    def __init__(self, config, status_callback=None, attach_callback=None):
        self.config = config
        self.running = False
        self.paused = False
        self.thread = None
        self.status_callback = status_callback
        self.attach_callback = attach_callback  # Callback to trigger UI attach
        self.ocr_tolerance = config.get("ocr_tolerance", 0.6)
        self.use_avg_tolerance = config.get("use_avg_tolerance", False)
        self.avg_tolerance = config.get("avg_tolerance", 0.65)
        self.roblox_region = None  # Will store (x, y, width, height) of Roblox window
        self.roblox_hwnd = None  # Will store window handle of attached Roblox window
        self.is_replay = False
        self.last_challenge_time = 0  # Track when last challenge/hourly task was done
        self.challenges_completed = 0  # Track challenges completed
        self.rrs_bought = 0  # Track RRs bought
        # Stage timing
        self.stage_start_time = None
        # Placement timing configuration (can be set in config)
        self.placement_delay = float(config.get("placement_delay", 0.15))
        self.move_duration = float(config.get("placement_move_duration", 0.12))
        # Allow a longer default timeout for the upgrade confirmation image to appear
        self.upg_confirm_timeout = float(config.get("upg_confirm_timeout", 4.0))
        self.slot_press_delay = float(config.get("slot_press_delay", 0.15))
        # Delay between repeated 'T' presses when spamming upgrades (configurable in settings)
        self.t_press_delay = float(config.get("t_press_delay", 0.08))
        self.placement_between_delay = float(config.get("placement_between_delay", 0.4))
        self.placement_confirm_delay = float(config.get("placement_confirm_delay", 0.3))
        self.reselect_backoff = float(config.get("reselect_backoff", 0.15))
        self.max_retries = int(config.get("placement_max_retries", 8))
    
    def get_confidence(self, default_confidence):
        """Get the confidence value to use for image matching.
        Returns avg_tolerance if enabled, otherwise returns the provided default."""
        if self.use_avg_tolerance:
            return self.avg_tolerance
        return default_confidence
    
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
    
    def get_roblox_window_region(self):
        """Get the Roblox window region and store its handle"""
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        GetWindowText = user32.GetWindowTextW
        GetWindowTextLength = user32.GetWindowTextLengthW
        GetWindowRect = user32.GetWindowRect
        IsWindowVisible = user32.IsWindowVisible
        GetClassName = user32.GetClassNameW
        
        # Known Roblox window class names
        ROBLOX_CLASSES = {'windowsclient', 'robloxplayerbeta'}
        
        result = []
        
        def enum_callback(hwnd, lParam):
            if not IsWindowVisible(hwnd):
                return True
            
            # Check window class name first (most reliable)
            class_buff = ctypes.create_unicode_buffer(256)
            GetClassName(hwnd, class_buff, 256)
            class_name = class_buff.value.lower()
            
            is_roblox = class_name in ROBLOX_CLASSES
            
            # Fallback: check window title
            if not is_roblox:
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buff, length + 1)
                    title = buff.value
                    if 'roblox' in title.lower():
                        is_roblox = True
            
            if is_roblox:
                rect = wintypes.RECT()
                GetWindowRect(hwnd, ctypes.byref(rect))
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 200 and height > 200:
                    result.append((hwnd, rect.left, rect.top, rect.right, rect.bottom))
            return True
        
        EnumWindows(EnumWindowsProc(enum_callback), 0)
        
        if result:
            # Return the largest window (main game window)
            result.sort(key=lambda r: (r[3]-r[1]) * (r[4]-r[2]), reverse=True)
            hwnd, x1, y1, x2, y2 = result[0]
            region = (x1, y1, x2, y2)
            self.roblox_hwnd = hwnd  # Store the window handle
            self.update_status(f"Found Roblox window at ({x1}, {y1}) size {x2-x1}x{y2-y1} (hwnd={hwnd})")
            return region
        return None
    
    def _trigger_attach(self):
        """Trigger the UI attach button functionality"""
        if self.attach_callback:
            self.update_status("Triggering UI attach...")
            result = self.attach_callback()
            if result and result.get("success"):
                self.update_status(f"✓ {result.get('message', 'Attached successfully')}")
                return True
            else:
                self.update_status(f"❌ Attach failed: {result.get('message', 'Unknown error')}")
                return False
        else:
            # Fallback to just getting the region if no attach callback
            self.update_status("No attach callback, using region detection...")
            self.roblox_region = self.get_roblox_window_region()
            return self.roblox_region is not None
        
    def update_status(self, message):
        """Update status message in UI"""
        if self.status_callback:
            self.status_callback(message)
    
    def _convert_to_roblox_protocol(self, link):
        """Convert web URL to roblox:// protocol URL for direct launch"""
        import re
        import os
        
        # Check if it's already a roblox:// URL
        if link.startswith("roblox://"):
            return link
        
        # Extract place ID and private server link code from URL
        place_match = re.search(r'/games/(\d+)', link)
        code_match = re.search(r'privateServerLinkCode=([a-zA-Z0-9_-]+)', link)
        
        if place_match:
            place_id = place_match.group(1)
            if code_match:
                link_code = code_match.group(1)
                return f"roblox://placeId={place_id}&linkCode={link_code}"
            else:
                return f"roblox://placeId={place_id}"
        
        # If we can't parse it, return the original link (will use os.startfile which handles URLs)
        return link
        print(f"[MACRO] {message}")
    
    def _send_discord_webhook(self, is_victory, stage_time_seconds, override_mode=None, override_location=None, override_act=None):
        """Send a Discord webhook notification with screenshot and stats
        
        Args:
            override_mode: If provided, use this instead of config mode (e.g. 'Challenge')
            override_location: If provided, use this instead of config location
            override_act: If provided, use this instead of config act
        """
        import urllib.request
        import json
        import io
        import base64
        from PIL import ImageGrab
        
        webhook_url = self.config.get("discord_webhook_url", "")
        if not webhook_url:
            return
        
        try:
            # Take screenshot of Roblox window
            screenshot_data = None
            if self.roblox_region:
                x1, y1, x2, y2 = self.roblox_region
                screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
                # Convert to bytes
                img_buffer = io.BytesIO()
                screenshot.save(img_buffer, format='PNG')
                img_buffer.seek(0)
                screenshot_data = img_buffer.getvalue()
            
            # Update stats in config
            if is_victory:
                self.config["stats_wins"] = self.config.get("stats_wins", 0) + 1
            else:
                self.config["stats_losses"] = self.config.get("stats_losses", 0) + 1
            save_config(self.config)
            
            wins = self.config.get("stats_wins", 0)
            losses = self.config.get("stats_losses", 0)
            total = wins + losses
            win_rate = round((wins / total) * 100) if total > 0 else 0
            
            # Get hourly task stats
            challenges_completed = self.challenges_completed
            rrs_bought = self.rrs_bought
            
            # Calculate time remaining until next hourly task
            CHALLENGE_INTERVAL = 60 * 60  # 60 minutes in seconds
            timer_str = "--:--"
            is_challenge_mode = (self.config.get("mode") == "Auto-Challenges") or self.config.get("auto_challenges_enabled") or self.config.get("buy_rrs_enabled")
            if self.last_challenge_time > 0 and is_challenge_mode:
                elapsed = time.time() - self.last_challenge_time
                remaining = max(0, CHALLENGE_INTERVAL - elapsed)
                minutes = int(remaining // 60)
                seconds = int(remaining % 60)
                timer_str = f"{minutes:02d}:{seconds:02d}"
            
            # Format time
            minutes = int(stage_time_seconds // 60)
            seconds = int(stage_time_seconds % 60)
            time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
            
            # Get stage info (use overrides if provided, e.g. for challenge games)
            mode = override_mode if override_mode else self.config.get("mode", "Story")
            location = override_location if override_location else self.config.get("location", "Unknown")
            act = override_act if override_act else self.config.get("act", "Act 1")
            
            # Build embed
            result_emoji = "🏆" if is_victory else "💀"
            result_text = "Victory" if is_victory else "Defeat"
            embed_color = 0x4ADE80 if is_victory else 0xEF4444  # Green or Red
            
            embed = {
                "content": None,
                "embeds": [{
                    "title": f"{result_emoji} {result_text}!",
                    "description": f"**{mode}** - {location} ({act})",
                    "color": embed_color,
                    "fields": [
                        {"name": "⏱️ Stage Time", "value": time_str, "inline": True},
                        {"name": "✅ Wins", "value": str(wins), "inline": True},
                        {"name": "❌ Losses", "value": str(losses), "inline": True},
                        {"name": "📊 Win Rate", "value": f"{win_rate}%", "inline": True},
                        {"name": "🎯 Challenges Completed", "value": str(challenges_completed), "inline": True},
                        {"name": "💎 RRs Bought", "value": str(rrs_bought), "inline": True},
                        {"name": "⏳ Next Loop In", "value": timer_str, "inline": True}
                    ],
                    "footer": {"text": "AnimeParadoxMacro"},
                    "image": {"url": "attachment://screenshot.png"} if screenshot_data else None
                }]
            }
            
            # Remove None image field if no screenshot
            if not screenshot_data:
                del embed["embeds"][0]["image"]
            
            # Send with multipart form data if we have a screenshot
            if screenshot_data:
                import uuid
                boundary = str(uuid.uuid4())
                
                body = b''
                # Add JSON payload
                body += f'--{boundary}\r\n'.encode()
                body += b'Content-Disposition: form-data; name="payload_json"\r\n'
                body += b'Content-Type: application/json\r\n\r\n'
                body += json.dumps(embed).encode('utf-8')
                body += b'\r\n'
                
                # Add file
                body += f'--{boundary}\r\n'.encode()
                body += b'Content-Disposition: form-data; name="files[0]"; filename="screenshot.png"\r\n'
                body += b'Content-Type: image/png\r\n\r\n'
                body += screenshot_data
                body += b'\r\n'
                body += f'--{boundary}--\r\n'.encode()
                
                req = urllib.request.Request(
                    webhook_url,
                    data=body,
                    headers={
                        'Content-Type': f'multipart/form-data; boundary={boundary}',
                        'User-Agent': 'AnimeParadoxMacro/1.0'
                    }
                )
            else:
                embed["content"] = None
                data = json.dumps(embed).encode('utf-8')
                req = urllib.request.Request(
                    webhook_url, 
                    data=data, 
                    headers={
                        'Content-Type': 'application/json',
                        'User-Agent': 'AnimeParadoxMacro/1.0'
                    }
                )
            
            urllib.request.urlopen(req, timeout=15)
            self.update_status(f"Discord webhook sent: {result_text}")
            
        except Exception as e:
            self.update_status(f"Webhook error: {str(e)}")
            print(f"Discord webhook error: {e}")
    
    def _check_disconnect(self):
        """Check if disconnect.png is detected and handle reconnection"""
        import webbrowser
        
        disconnect_pos = find_image_on_screen(
            get_button_path("buttons/disconnect.png"),
            confidence=self.get_confidence(0.7),
            region=self.roblox_region
        )
        
        if disconnect_pos:
            self.update_status("⚠️ DISCONNECT DETECTED!")
            private_server_link = self.config.get("private_server_link", "")
            
            if not private_server_link:
                self.update_status("❌ No private server link configured!")
                self.update_status("Please set a private server link in Settings > Auto-Reconnect")
                return False
            
            self.update_status(f"🔄 Reconnecting to private server...")
            try:
                # Close Roblox
                close_roblox_instance(self.roblox_hwnd)
                self.roblox_region = None  # Detach from old window
                time.sleep(2)
                # Launch Roblox directly
                import os
                roblox_url = self._convert_to_roblox_protocol(private_server_link)
                os.startfile(roblox_url)
                self.update_status("✓ Launching Roblox directly")
                self.update_status("Waiting 20 seconds for game to load...")
                time.sleep(20)
                
                # Reattach to Roblox window using UI attach
                self.update_status("Reattaching to Roblox window...")
                if self._trigger_attach():
                    # Click to focus
                    if self.roblox_region:
                        click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                        click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                        click(click_x, click_y)
                        time.sleep(2)
                    self.update_status("✓ Reconnection complete, resuming macro...")
                    return True
                else:
                    self.update_status("❌ Failed to reattach to Roblox window")
                    return False
                
            except Exception as e:
                self.update_status(f"❌ Reconnection failed: {str(e)}")
                return False
        
        return None  # No disconnect detected
    
    def _running_and_not_disconnected(self):
        """Check if macro should continue running (checks both running state and disconnect)"""
        if not self.running:
            return False
        
        # Check for disconnect
        disconnect_result = self._check_disconnect()
        if disconnect_result == False:
            # Disconnect detected but no recovery link configured
            self.running = False
            return False
        elif disconnect_result == True:
            # Reconnected successfully, need to restart navigation
            # This will be handled by the main loop
            pass
        
        return self.running
    
    def _run_auto_challenges_loop(self):
        """Main loop for Auto-Challenges mode"""
        import datetime
        import webbrowser
        challenge_location = self.config.get("challenge_location", "Leaf Village")
        self.update_status(f"Auto-Challenges: Challenge map = {challenge_location}")
        
        def get_next_top_of_hour():
            """Get the timestamp for the next :00 mark"""
            now = datetime.datetime.now()
            # If we're past :00, go to next hour's :00
            # If we're exactly at :00, also go to next hour
            next_hour = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
            return next_hour
        
        def get_current_top_of_hour():
            """Get the timestamp for the current hour's :00 mark (already passed)"""
            now = datetime.datetime.now()
            return now.replace(minute=0, second=0, microsecond=0)
        
        def rejoin_and_reattach():
            """Rejoin using PS link and reattach to Roblox window"""
            private_server_link = self.config.get("private_server_link", "")
            
            # Fallback to public game link if no private server link
            if not private_server_link:
                private_server_link = "https://www.roblox.com/games/76806550943352/Anime-Paradox#ropro-quick-play"
                self.update_status("Auto-Challenges: No private server link, using public game link...")
            
            self.update_status("Auto-Challenges: 🔄 Rejoining via private server link...")
            try:
                # Close Roblox
                close_roblox_instance(self.roblox_hwnd)
                self.roblox_region = None  # Detach from old window
                time.sleep(2)
                # Launch Roblox directly
                import os
                roblox_url = self._convert_to_roblox_protocol(private_server_link)
                os.startfile(roblox_url)
                self.update_status("Auto-Challenges: ✓ Launching Roblox directly")
                self.update_status("Auto-Challenges: Waiting 20 seconds for game to load...")
                time.sleep(20)
                
                # Reattach to Roblox window using UI attach
                self.update_status("Auto-Challenges: Reattaching to Roblox window...")
                if self._trigger_attach():
                    self.update_status(f"Auto-Challenges: ✓ Reattached to Roblox: {self.roblox_region}")
                    # Click to focus
                    if self.roblox_region:
                        click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                        click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                        click(click_x, click_y)
                        time.sleep(2)
                    return True
                else:
                    self.update_status("Auto-Challenges: ❌ Could not attach Roblox window")
                    return False
                    
            except Exception as e:
                self.update_status(f"Auto-Challenges: ❌ Rejoin failed: {str(e)}")
                return False
        
        next_challenge_time = 0  # Force first challenge immediately
        
        while self.running:
            now = datetime.datetime.now()
            current_time_str = now.strftime("%H:%M:%S")
            
            # Check if it's time for a challenge (past the target time or first run)
            if next_challenge_time == 0 or time.time() >= next_challenge_time:
                self.update_status(f"\n=== CHALLENGE TIME === (Current: {current_time_str})")
                
                # Navigate to and complete challenge
                challenge_success = False
                if self._navigate_to_challenge():
                    # Run the challenge game
                    if self._run_challenge_game():
                        challenge_success = True
                        self.update_status("Auto-Challenges: Challenge completed, rejoining...")
                        if not rejoin_and_reattach():
                            self.update_status("Auto-Challenges: Rejoin failed, waiting 10s and retrying...")
                            time.sleep(10)
                            continue
                        self.is_replay = False  # Reset so lobby positioning runs on next stage game
                        self.update_status("Auto-Challenges: Rejoin successful after challenge victory")
                    else:
                        self.update_status("Auto-Challenges: Challenge game failed")
                else:
                    self.update_status("Auto-Challenges: Failed to navigate to challenge")
                
                # If challenge failed, rejoin and skip to stage loop
                if not challenge_success:
                    self.update_status("Auto-Challenges: Challenge failed, attempting rejoin...")
                    if not rejoin_and_reattach():
                        self.update_status("Auto-Challenges: Rejoin failed, waiting 10s and retrying...")
                        time.sleep(10)
                        continue
                    
                    self.is_replay = False  # Reset so lobby positioning runs on next stage game
                    # After rejoin, set next challenge time and go to stage loop
                    self.update_status("Auto-Challenges: Rejoin successful, skipping to stage loop until top of hour")
                
                # Set next challenge time to the next top of the hour (:00)
                next_hour_dt = get_next_top_of_hour()
                next_challenge_time = next_hour_dt.timestamp()
                # Update instance variable so webhook timer can show "Next Loop In"
                self.last_challenge_time = time.time()
                next_hour_str = next_hour_dt.strftime("%H:%M")
                current_str = datetime.datetime.now().strftime("%H:%M:%S")
                
                if challenge_success:
                    self.update_status(f"Auto-Challenges: Challenge done at {current_str}. Next challenge at {next_hour_str}")
                else:
                    self.update_status(f"Auto-Challenges: Skipped challenge at {current_str}. Next challenge at {next_hour_str}")
                
                # Navigate to selected stage
                if not self._navigate_to_selected_stage():
                    self.update_status("Auto-Challenges: Failed to navigate to selected stage, rejoining...")
                    if not rejoin_and_reattach():
                        self.update_status("Auto-Challenges: Rejoin failed, waiting 10s and retrying...")
                        time.sleep(10)
                    else:
                        self.update_status("Auto-Challenges: Rejoin successful")
                        time.sleep(5)
                    continue
            
            # Run selected stage games until top of the hour
            while self.running:
                remaining = next_challenge_time - time.time()
                
                if remaining <= 0:
                    current_str = datetime.datetime.now().strftime("%H:%M:%S")
                    self.update_status(f"Auto-Challenges: Top of hour reached at {current_str}, waiting for stage completion...")
                    # Complete current game then return to challenge
                    break
                
                mins_remaining = int(remaining // 60)
                secs_remaining = int(remaining % 60)
                self.update_status(f"Auto-Challenges: {mins_remaining}m {secs_remaining}s until next challenge")
                
                # Run one stage game
                if not self._run_stage_game_for_challenges():
                    self.update_status("Auto-Challenges: Stage game failed, retrying...")
                    time.sleep(2)
                    continue
                
                # After win, check if we should do challenge
                if time.time() >= next_challenge_time:
                    # Click return to go back to lobby
                    current_str = datetime.datetime.now().strftime("%H:%M:%S")
                    self.update_status(f"Auto-Challenges: Time for challenge at {current_str}, clicking Return...")
                    return_pos = wait_for_image(get_button_path("buttons/return.png"), timeout=30, confidence=self.get_confidence(0.65),
                                                region=self.roblox_region, running_check=lambda: self.running)
                    if return_pos:
                        move_to(*return_pos, duration=0.3)
                        time.sleep(0.2)
                        click(*return_pos)
                        time.sleep(2)
                    break
            
            # After inner loop ends (top of hour reached or time met), 
            # continue to outer loop to run the challenge
            continue
    
    def _buy_rrs(self):
        """Navigate to summon shop and buy RRs: Areas -> Summon -> walk left until traitbuy -> spam click"""
        self.update_status("Buy RRs: Starting...")
        
        # Step 1: Find and click "Areas"
        self.update_status("Buy RRs: Looking for Areas button...")
        areas_pos = wait_for_image(get_button_path("buttons/Areas.png"), timeout=30, confidence=0.65,
                                   region=self.roblox_region, running_check=lambda: self.running,
                                   warning_callback=lambda: self.update_status("⚠️ Still searching... Try 'Calculate Best Tolerance' in Settings if stuck!"),
                                   warning_time=10)
        if not self._check_running():
            return False
        if not areas_pos:
            self.update_status("Buy RRs: ✗ Could not find Areas button")
            return False
        
        move_to(*areas_pos, duration=0.3)
        time.sleep(0.2)
        click(*areas_pos)
        time.sleep(1.0)
        self.update_status("Buy RRs: ✓ Clicked Areas")
        
        # Step 2: Find and click "summon.png"
        self.update_status("Buy RRs: Looking for Summon button...")
        summon_pos = wait_for_image(get_button_path("buttons/summon.png"), timeout=30, confidence=0.65,
                                    region=self.roblox_region, running_check=lambda: self.running)
        if not self._check_running():
            return False
        if not summon_pos:
            self.update_status("Buy RRs: ✗ Could not find Summon button")
            return False
        
        move_to(*summon_pos, duration=0.3)
        time.sleep(0.2)
        click(*summon_pos)
        time.sleep(1.0)
        self.update_status("Buy RRs: ✓ Clicked Summon")
        
        # Step 3: Spam E while holding A to walk left for 10 seconds
        self.update_status("Buy RRs: Walking left and spamming E...")
        walk_start = time.time()
        while self.running and (time.time() - walk_start) < 10:  # Walk for 10 seconds
            hold_key_directinput('a', 0.3)
            press_key('e')
            time.sleep(0.1)
        
        if not self._check_running():
            return False
        
        # Step 4: Find essence.png, hover, scroll down, then spam click at 469, 229 relative to window
        self.update_status("Buy RRs: Looking for essence...")
        time.sleep(1.0)
        essence_pos = wait_for_image(get_button_path("buttons/essence.png"), timeout=15, confidence=0.65,
                                     region=self.roblox_region, running_check=lambda: self.running)
        if not self._check_running():
            return False
        
        if essence_pos:
            self.update_status(f"Buy RRs: Found essence at {essence_pos}, hovering...")
            move_to(*essence_pos, duration=0.3)
            time.sleep(0.3)
            
            # Scroll down for a bit
            self.update_status("Buy RRs: Scrolling down...")
            scroll_down(clicks=8, delay=0.3)
            time.sleep(0.5)
        else:
            self.update_status("Buy RRs: Could not find essence, attempting click anyway...")
        
        # Spam click at 469, 229 relative to window
        if self.roblox_region:
            click_x = self.roblox_region[0] + 469
            click_y = self.roblox_region[1] + 229
            self.update_status(f"Buy RRs: Spam clicking at ({click_x}, {click_y})...")
            for i in range(10):
                if not self.running:
                    return False
                move_to(click_x, click_y, duration=0.05)
                click(click_x, click_y)
                time.sleep(0.15)
        
        self.update_status("Buy RRs: ✓ Done buying RRs!")
        self.rrs_bought += 3  # Each buy RR loop purchases 3
        return True
    
    def _navigate_to_challenge(self):
        """Navigate to challenge: Areas -> Challenges -> walk forward -> Regular -> trait -> offset click -> Start"""
        self.update_status("Challenge Nav: Starting challenge navigation...")
        
        # Step 1: Find and click "Areas"
        self.update_status("Challenge Nav: Looking for Areas button...")
        areas_pos = wait_for_image(get_button_path("buttons/Areas.png"), timeout=30, confidence=0.65,
                                   region=self.roblox_region, running_check=lambda: self.running,
                                   warning_callback=lambda: self.update_status("⚠️ Still searching... Try 'Calculate Best Tolerance' in Settings if stuck!"),
                                   warning_time=10)
        if not self._check_running():
            return False
        if not areas_pos:
            self.update_status("Challenge Nav: ✗ Could not find Areas button")
            return False
        
        move_to(*areas_pos, duration=0.3)
        time.sleep(0.2)
        click(*areas_pos)
        time.sleep(1.0)
        self.update_status("Challenge Nav: ✓ Clicked Areas")
        
        # Step 2: Find and click "challenges.png"
        self.update_status("Challenge Nav: Looking for Challenges button...")
        challenges_pos = wait_for_image(get_button_path("buttons/challenges.png"), timeout=30, confidence=0.75,
                                        region=self.roblox_region, running_check=lambda: self.running)
        if not self._check_running():
            return False
        if not challenges_pos:
            self.update_status("Challenge Nav: ✗ Could not find Challenges button")
            return False
        
        move_to(*challenges_pos, duration=0.3)
        time.sleep(0.2)
        click(*challenges_pos)
        time.sleep(1.0)
        self.update_status("Challenge Nav: ✓ Clicked Challenges")
        
        # Step 3: Walk left (hold A) until creatematch appears
        self.update_status("Challenge Nav: Walking left to find Create Match...")
        walk_start = time.time()
        creatematch_pos = None
        while self.running and (time.time() - walk_start) < 15:  # Max 15 seconds walk
            # Check for creatematch.png while walking
            creatematch_pos = find_image_on_screen(get_button_path("buttons/creatematch.png"), confidence=self.get_confidence(0.65), region=self.roblox_region)
            if creatematch_pos:
                break
            hold_key_directinput('a', 0.5)
            time.sleep(0.1)
        
        if not self._check_running():
            return False
        if not creatematch_pos:
            self.update_status("Challenge Nav: ✗ Could not find Create Match button")
            return False
        
        self.update_status("Challenge Nav: ✓ Found Create Match button")
        
        # Step 5: Click creatematch.png
        move_to(*creatematch_pos, duration=0.3)
        time.sleep(0.2)
        click(*creatematch_pos)
        time.sleep(1.0)
        self.update_status("Challenge Nav: ✓ Clicked Create Match")
        
        # Step 6: Search for regular.png
        self.update_status("Challenge Nav: Looking for Regular button...")
        regular_pos = wait_for_image(get_button_path("buttons/regular.png"), timeout=15, confidence=self.get_confidence(0.65),
                                      region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        if not regular_pos:
            self.update_status("Challenge Nav: ✗ Could not find Regular button")
            return False
        
        self.update_status("Challenge Nav: ✓ Found Regular button")
        
        # Step 7: Click regular.png
        move_to(*regular_pos, duration=0.3)
        time.sleep(0.2)
        click(*regular_pos)
        time.sleep(1.0)
        self.update_status("Challenge Nav: ✓ Clicked Regular")
        
        # Step 8: Search for trait.png
        self.update_status("Challenge Nav: Looking for trait button...")
        trait_pos = wait_for_image(get_button_path("buttons/trait.png"), timeout=15, confidence=0.65,
                                   region=self.roblox_region, running_check=lambda: self.running)
        if not self._check_running():
            return False
        if not trait_pos:
            self.update_status("Challenge Nav: ✗ Could not find trait button")
            return False
        
        # Step 6: Hover over trait, then move X offset 200 and click
        self.update_status("Challenge Nav: Hovering over trait and clicking offset...")
        move_to(*trait_pos, duration=0.3)
        time.sleep(0.3)
        # Move 200 pixels to the right and click
        click_x = trait_pos[0] + 200
        click_y = trait_pos[1]
        move_to(click_x, click_y, duration=0.2)
        time.sleep(0.2)
        click(click_x, click_y)
        time.sleep(1.0)
        self.update_status("Challenge Nav: ✓ Clicked trait offset")
        
        # Step 7: Detect which challenge map is shown (use best match)
        self.update_status("Challenge Nav: Detecting challenge map...")
        self._detected_challenge_map = self._detect_challenge_map_best_match()
        self.update_status(f"Challenge Nav: ✓ Detected map: {self._detected_challenge_map}")
        
        # Step 8: Click Start button
        self.update_status("Challenge Nav: Looking for Start button...")
        start_pos = wait_for_image(get_button_path("buttons/start.png"), timeout=15, confidence=0.65,
                                   region=self.roblox_region, running_check=lambda: self.running)
        if not self._check_running():
            return False
        if not start_pos:
            self.update_status("Challenge Nav: ✗ Could not find Start button")
            return False
        
        move_to(*start_pos, duration=0.3)
        time.sleep(0.2)
        click(*start_pos)
        time.sleep(1.0)
        self.update_status("Challenge Nav: ✓ Clicked Start - Challenge navigation complete")
        
        return True
    
    def _run_challenge_game(self):
        """Run a single challenge game with auto-detected positioning"""
        self.update_status("Challenge Game: Waiting for Yes button...")
        
        # Wait for Yes button
        yes_pos = wait_for_image(get_button_path("buttons/Yes.png"), timeout=60, confidence=0.65,
                                 region=self.roblox_region, running_check=lambda: self.running)
        if not self._check_running():
            return False
        if not yes_pos:
            self.update_status("Challenge Game: ✗ Could not find Yes button")
            return False
        
        self.update_status("Challenge Game: ✓ Found Yes button")
        
        # Zoom out first
        self.update_status("Challenge Game: Zooming out...")
        if self.roblox_region:
            center_x = (self.roblox_region[0] + self.roblox_region[2]) // 2
            center_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
            drag_distance = (self.roblox_region[3] - self.roblox_region[1]) - 100
            drag_down(center_x, center_y, distance=drag_distance, duration=0.3)
        time.sleep(0.3)
        # Get zoom duration for Auto-Challenges mode
        zoom_duration = self.config.get("zoom_duration_auto-challenges", 0.3)
        hold_key('o', duration=zoom_duration)
        time.sleep(0.5)
        
        # Get the detected map from navigation phase (or detect now if not set)
        detected_map = getattr(self, '_detected_challenge_map', None)
        if not detected_map:
            detected_map = self.config.get("challenge_location", "Leaf Village")
        self.update_status(f"Challenge Game: Using map: {detected_map}")
        
        # Do positioning based on detected map BEFORE clicking Yes
        self.update_status(f"Challenge Game: Performing {detected_map} positioning...")
        self._do_challenge_positioning(detected_map)
        
        # Place early units (PlaceBeforeYes=true) before clicking Yes
        self.update_status(f"Challenge Game: Placing early units (before Yes)...")
        self._place_units_from_config(early_placement=True, override_mode="Auto-Challenges", override_location=detected_map, override_act="Act 1")
        
        # Click Yes to start after positioning and early placement
        self.update_status("Challenge Game: Clicking Yes...")
        yes_pos = find_image_on_screen(get_button_path("buttons/Yes.png"), confidence=0.65, region=self.roblox_region)
        if yes_pos:
            move_to(*yes_pos, duration=0.3)
            time.sleep(0.2)
            click(*yes_pos)
            self.update_status("Challenge Game: ✓ Clicked Yes")
        else:
            self.update_status("Challenge Game: ⚠ Yes button not found")
        time.sleep(1.0)
        
        # Start timing
        self.stage_start_time = time.time()
        
        # Extra delay for game to fully load after Yes button (especially important for Planet Namek positioning)
        self.update_status("Challenge Game: Waiting for game to fully load...")
        time.sleep(2.0)
        
        # Phase 2: Place units (using challenge config based on DETECTED map, not config setting)
        self.update_status(f"Challenge Game: Placing units using {detected_map} challenge config...")
        self._place_units_from_config(early_placement=False, override_mode="Auto-Challenges", override_location=detected_map, override_act="Act 1")
        
        # Phase 3: Wait for victory (replay on defeat)
        self.update_status("Challenge Game: Waiting for victory/defeat...")
        
        while self.running:
            game_result = None
            last_anti_afk_click = time.time()
            
            while self.running and game_result is None:
                victory_pos = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                # Check Y coordinate to prevent false positives near top of screen
                if victory_pos and victory_pos[1] > 30:
                    game_result = 'victory'
                    break
                
                defeat_pos = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.6, region=self.roblox_region)
                if defeat_pos:
                    game_result = 'defeat'
                    break
                
                # Check for click button (spam click until victory)
                click_pos = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.5, region=self.roblox_region)
                if click_pos:
                    self.update_status("Challenge Game: Click button detected, spam clicking...")
                    # Spam click until victory or defeat is detected
                    spam_count = 0
                    while self.running and spam_count < 100:
                        click(*click_pos)
                        time.sleep(0.05)
                        spam_count += 1
                        
                        # Check for victory during spam
                        victory_check = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                        if victory_check:
                            self.update_status("Challenge Game: Victory detected after clicking!")
                            game_result = 'victory'
                            break
                        
                        # Check for defeat during spam
                        defeat_check = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.6, region=self.roblox_region)
                        if defeat_check:
                            self.update_status("Challenge Game: Defeat detected after clicking!")
                            game_result = 'defeat'
                            break
                    
                    if game_result:
                        break
                
                # Anti-AFK: click 6 times every 10 seconds in top right corner
                if time.time() - last_anti_afk_click > 10:
                    if self.roblox_region:
                        afk_x = self.roblox_region[2] - 50  # 50 pixels from right edge
                        afk_y = self.roblox_region[1] + 50  # 50 pixels from top edge
                        for _ in range(6):
                            move_to(afk_x, afk_y, duration=0.1)
                            time.sleep(0.05)
                            click(afk_x, afk_y)
                            time.sleep(0.05)
                        self.update_status("Anti-AFK: Clicked corner 6x")
                    last_anti_afk_click = time.time()
                
                time.sleep(0.5)
            
            if not self._check_running():
                return False
            
            # Handle result
            if game_result == 'victory':
                # Calculate stage time
                stage_time = time.time() - self.stage_start_time if self.stage_start_time else 0
                self.update_status(f"Challenge Game: VICTORY! Time: {int(stage_time)}s")
                
                # Increment challenges completed BEFORE sending webhook so it shows the updated count
                self.challenges_completed += 1
                
                # Send webhook with challenge-specific info
                self._send_discord_webhook(True, stage_time, override_mode="Challenge", override_location=detected_map, override_act="Act 1")
                
                # Victory - return True to trigger rejoin
                return True
                
            elif game_result == 'defeat':
                # Calculate stage time and send webhook with challenge info
                stage_time = time.time() - self.stage_start_time if self.stage_start_time else 0
                self.update_status(f"Challenge Game: DEFEAT! Time: {int(stage_time)}s - Replaying...")
                self._send_discord_webhook(False, stage_time, override_mode="Challenge", override_location=detected_map, override_act="Act 1")
                
                # Wait for replay.png and click it to retry
                time.sleep(1)
                replay_pos = wait_for_image(get_button_path("buttons/replay.png"), timeout=30, confidence=0.4,
                                            region=self.roblox_region, running_check=lambda: self.running)
                if replay_pos:
                    move_to(*replay_pos, duration=0.3)
                    time.sleep(0.2)
                    click(*replay_pos)
                    time.sleep(3)
                    self.update_status("Challenge Game: Replaying after defeat...")
                    # Reset timer for new attempt
                    self.stage_start_time = time.time()
                    # Continue loop to try again
                else:
                    self.update_status("Challenge Game: Replay button not found, aborting")
                    return False
    
    def _detect_challenge_map(self):
        """Detect which challenge map we're on by checking images in buttons folder (shared with story mode)"""
        # Check for each map image (using same images as story mode)
        leaf_pos = find_image_on_screen(get_button_path("buttons/leaf.png"), confidence=0.6, region=self.roblox_region)
        if leaf_pos:
            return "Leaf Village"
        
        planet_pos = find_image_on_screen(get_button_path("buttons/planet.png"), confidence=0.6, region=self.roblox_region)
        if planet_pos:
            return "Planet Namek"
        
        dark_pos = find_image_on_screen(get_button_path("buttons/hollow.png"), confidence=0.6, region=self.roblox_region)
        if dark_pos:
            return "Dark Hollow"
        
        # Default to challenge_location from config if not detected
        return self.config.get("challenge_location", "Leaf Village")
    
    def _detect_challenge_map_best_match(self):
        """Detect challenge map using best match comparison across all 3 maps with multiple checks"""
        import cv2
        import numpy as np
        from mss import mss
        
        # Take screenshot of game region
        if not self.roblox_region:
            return self.config.get("challenge_location", "Leaf Village")
        
        # Check each map image and get confidence scores
        maps_to_check = [
            ("Leaf Village", "buttons/challengemaps/leaf.png"),
            ("Planet Namek", "buttons/challengemaps/planet.png"),
            ("Dark Hollow", "buttons/challengemaps/dark.png"),
            ("Shibuya", "buttons/challengemaps/shibuya.png")
        ]
        
        map_scores = {
            "Leaf Village": [],
            "Planet Namek": [],
            "Dark Hollow": [],
            "Shibuya": []
        }
        
        # Perform 5 checks with small delays between them
        num_checks = 5
        for check_num in range(num_checks):
            try:
                with mss() as sct:
                    monitor = {
                        "left": self.roblox_region[0],
                        "top": self.roblox_region[1],
                        "width": self.roblox_region[2] - self.roblox_region[0],
                        "height": self.roblox_region[3] - self.roblox_region[1]
                    }
                    screenshot = np.array(sct.grab(monitor))
                    screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2BGR)
                
                # Check each map in this screenshot
                for map_name, image_path in maps_to_check:
                    full_path = get_button_path(image_path)
                    if not os.path.exists(full_path):
                        # Fallback to buttons folder
                        full_path = get_button_path(f"buttons/{image_path.split('/')[-1]}")
                    
                    if os.path.exists(full_path):
                        template = cv2.imread(full_path)
                        if template is not None:
                            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
                            _, max_val, _, _ = cv2.minMaxLoc(result)
                            map_scores[map_name].append(max_val)
                            self.update_status(f"Challenge Nav: Check {check_num + 1} - {map_name} = {max_val:.3f}")
                
                # Small delay between checks
                if check_num < num_checks - 1:
                    time.sleep(0.1)
                    
            except Exception as e:
                self.update_status(f"Challenge Nav: Detection error on check {check_num + 1}: {e}")
        
        # Find the map with the highest overall score
        best_map = None
        best_confidence = 0.0
        
        for map_name, scores in map_scores.items():
            if scores:
                max_score = max(scores)
                avg_score = sum(scores) / len(scores)
                # Use maximum score as the primary indicator
                self.update_status(f"Challenge Nav: {map_name} - Max: {max_score:.3f}, Avg: {avg_score:.3f}, Checks: {len(scores)}")
                
                if max_score > best_confidence:
                    best_confidence = max_score
                    best_map = map_name
        
        if best_map and best_confidence >= 0.5:
            self.update_status(f"Challenge Nav: ✓ Best match: {best_map} ({best_confidence:.3f})")
            return best_map
        else:
            self.update_status(f"Challenge Nav: ⚠ Low confidence ({best_confidence:.3f}), using config default")
        
        # Default to challenge_location from config if not detected
        return self.config.get("challenge_location", "Leaf Village")
    
    def _do_challenge_positioning(self, map_name):
        """Do positioning based on detected challenge map"""
        map_lower = map_name.lower()
        
        if "leaf" in map_lower or "village" in map_lower:
            self.update_status("Challenge Positioning: Leaf Village sequence...")
            hold_key_directinput('a', 2.0)
            time.sleep(0.3)
            hold_key_directinput('w', 1.8)
            time.sleep(0.3)
        
        elif "planet" in map_lower or "namek" in map_lower or "namak" in map_lower:
            self.update_status("Challenge Positioning: Planet Namek sequence...")
            hold_key_directinput('s', 1.1)
            time.sleep(0.3)
            hold_key_directinput('a', 0.2)
            time.sleep(0.3)
        
        elif "dark" in map_lower or "hollow" in map_lower:
            self.update_status("Challenge Positioning: Dark Hollow sequence...")
            hold_key_directinput('a', 1.2)
            time.sleep(0.3)
        
        elif "shibuya" in map_lower:
            self.update_status("Challenge Positioning: Shibuya sequence...")
            hold_key_directinput('w', 0.3)
            time.sleep(0.3)
        
        self.update_status("Challenge Positioning: ✓ Complete")
    
    def _navigate_to_selected_stage(self):
        """Navigate to the selected stage (the one configured in Stage tab) for between-challenge farming"""
        self.update_status("Stage Nav: Navigating to selected stage for farming...")
        
        # Wait for Areas button in lobby
        self.update_status("Stage Nav: Looking for Areas button...")
        areas_pos = wait_for_image(get_button_path("buttons/Areas.png"), timeout=30, confidence=0.65,
                                   region=self.roblox_region, running_check=lambda: self.running,
                                   warning_callback=lambda: self.update_status("⚠️ Still searching... Try 'Calculate Best Tolerance' in Settings if stuck!"),
                                   warning_time=10)
        if not self._check_running():
            return False
        if not areas_pos:
            self.update_status("Stage Nav: ✗ Could not find Areas button")
            return False
        
        move_to(*areas_pos, duration=0.3)
        time.sleep(0.2)
        click(*areas_pos)
        time.sleep(1.0)
        
        # Use Story mode navigation for the selected stage
        # The selected stage is based on challenge_location config
        challenge_location = self.config.get("challenge_location", "Leaf Village")
        self.update_status(f"Stage Nav: Going to Story mode for {challenge_location}...")
        
        # Navigate through Story mode
        return self._navigate_story_mode_for_challenges(challenge_location)
    
    def _navigate_story_mode_for_challenges(self, location):
        """Navigate Story mode for challenge farming"""
        # Click Story button
        self.update_status("Stage Nav: Looking for Story button...")
        story_pos = wait_for_image(get_button_path("buttons/Story.png"), timeout=30, confidence=0.65,
                                   region=self.roblox_region, running_check=lambda: self.running)
        if not self._check_running():
            return False
        if not story_pos:
            self.update_status("Stage Nav: ✗ Could not find Story button")
            return False
        
        move_to(*story_pos, duration=0.3)
        time.sleep(0.2)
        click(*story_pos)
        time.sleep(1.0)
        
        # Click X button
        x_pos = wait_for_image(get_button_path("buttons/X.png"), timeout=30, confidence=0.65,
                               region=self.roblox_region, running_check=lambda: self.running)
        if x_pos:
            move_to(*x_pos, duration=0.3)
            time.sleep(0.2)
            click(*x_pos)
            time.sleep(0.5)
        
        # Walk forward
        hold_key('w', duration=3.0)
        time.sleep(0.5)
        
        # Navigate to specific location
        location_lower = location.lower()
        if "leaf" in location_lower or "village" in location_lower:
            return self._navigate_to_leaf_village()
        elif "planet" in location_lower or "namek" in location_lower:
            return self._navigate_to_planet_namek()
        elif "dark" in location_lower or "hollow" in location_lower:
            return self._navigate_to_dark_hollow()
        elif "shibuya" in location_lower:
            return self._navigate_to_shibuya()
        
        return True
    
    def _navigate_to_leaf_village(self):
        """Navigate to Leaf Village and select Act 1"""
        self.update_status("Stage Nav: Looking for Leaf Village...")
        leaf_pos = wait_for_image(get_button_path("buttons/leaf.png"), timeout=30, confidence=0.65,
                                  region=self.roblox_region, running_check=lambda: self.running)
        if leaf_pos:
            move_to(*leaf_pos, duration=0.3)
            time.sleep(0.2)
            click(*leaf_pos)
            time.sleep(0.5)
        
        # Click Act 1
        act_pos = wait_for_image(get_button_path("buttons/Acts/act1.png"), timeout=15, confidence=0.55,
                                 region=self.roblox_region, running_check=lambda: self.running)
        if act_pos:
            move_to(*act_pos, duration=0.3)
            time.sleep(0.2)
            click(*act_pos)
            time.sleep(0.5)
        
        return True
    
    def _navigate_to_planet_namek(self):
        """Navigate to Planet Namek and select Act 1"""
        self.update_status("Stage Nav: Looking for Planet Namek...")
        planet_pos = wait_for_image(get_button_path("buttons/planet.png"), timeout=30, confidence=0.65,
                                    region=self.roblox_region, running_check=lambda: self.running)
        if planet_pos:
            move_to(*planet_pos, duration=0.3)
            time.sleep(0.2)
            click(*planet_pos)
            time.sleep(0.5)
        
        # Click Act 1
        act_pos = wait_for_image(get_button_path("buttons/Acts/act1.png"), timeout=15, confidence=0.55,
                                 region=self.roblox_region, running_check=lambda: self.running)
        if act_pos:
            move_to(*act_pos, duration=0.3)
            time.sleep(0.2)
            click(*act_pos)
            time.sleep(0.5)
        
        return True
    
    def _navigate_to_dark_hollow(self):
        """Navigate to Dark Hollow and select Act 1"""
        self.update_status("Stage Nav: Looking for Dark Hollow...")
        dark_pos = wait_for_image(get_button_path("buttons/dark.png"), timeout=30, confidence=0.65,
                                  region=self.roblox_region, running_check=lambda: self.running)
        if dark_pos:
            move_to(*dark_pos, duration=0.3)
            time.sleep(0.2)
            click(*dark_pos)
            time.sleep(0.5)
        
        # Click Act 1
        act_pos = wait_for_image(get_button_path("buttons/Acts/act1.png"), timeout=15, confidence=0.55,
                                 region=self.roblox_region, running_check=lambda: self.running)
        if act_pos:
            move_to(*act_pos, duration=0.3)
            time.sleep(0.2)
            click(*act_pos)
            time.sleep(0.5)
        
        return True
    
    def _navigate_to_shibuya(self):
        """Navigate to Shibuya and select Act 1"""
        self.update_status("Stage Nav: Looking for Shibuya...")
        shibuya_pos = wait_for_image(get_button_path("buttons/shibuya.png"), timeout=30, confidence=0.65,
                                     region=self.roblox_region, running_check=lambda: self.running)
        if shibuya_pos:
            move_to(*shibuya_pos, duration=0.3)
            time.sleep(0.2)
            click(*shibuya_pos)
            time.sleep(0.5)
        
        # Click Act 1
        act_pos = wait_for_image(get_button_path("buttons/Acts/act1.png"), timeout=15, confidence=0.55,
                                 region=self.roblox_region, running_check=lambda: self.running)
        if act_pos:
            move_to(*act_pos, duration=0.3)
            time.sleep(0.2)
            click(*act_pos)
            time.sleep(0.5)
        
        return True
    
    def _run_stage_game_for_challenges(self):
        """Run a single stage game during challenge farming period"""
        # Wait for Yes button
        yes_pos = wait_for_image(get_button_path("buttons/Yes.png"), timeout=120, confidence=0.65,
                                 region=self.roblox_region, running_check=lambda: self.running)
        if not self._check_running():
            return False
        if not yes_pos:
            return False
        
        time.sleep(2.0)
        
        # Zoom out and position (if not replay)
        if not self.is_replay:
            if self.roblox_region:
                center_x = (self.roblox_region[0] + self.roblox_region[2]) // 2
                center_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                drag_distance = (self.roblox_region[3] - self.roblox_region[1]) - 100
                drag_down(center_x, center_y, distance=drag_distance, duration=0.3)
            time.sleep(0.3)
            # Get zoom duration for Auto-Challenges mode
            zoom_duration = self.config.get("zoom_duration_auto-challenges", 0.3)
            hold_key('o', duration=zoom_duration)
            time.sleep(0.5)
            
            # Do positioning based on challenge_location
            challenge_location = self.config.get("challenge_location", "Leaf Village")
            self._do_challenge_positioning(challenge_location)
        
        # Click Yes
        yes_pos = find_image_on_screen(get_button_path("buttons/Yes.png"), confidence=0.65, region=self.roblox_region)
        if yes_pos:
            move_to(*yes_pos, duration=0.3)
            time.sleep(0.2)
            click(*yes_pos)
        time.sleep(1.0)
        
        self.stage_start_time = time.time()
        
        # Place units using the Story config for challenge_location
        challenge_location = self.config.get("challenge_location", "Leaf Village")
        self._place_units_from_config(early_placement=False, override_mode="Story", override_location=challenge_location, override_act="Act 1")
        
        # Wait for victory/defeat
        game_result = None
        last_anti_afk_click = time.time()
        while self.running and game_result is None:
            victory_pos = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
            # Check Y coordinate to prevent false positives near top of screen
            if victory_pos and victory_pos[1] > 30:
                game_result = 'victory'
                break
            
            defeat_pos = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.6, region=self.roblox_region)
            if defeat_pos:
                game_result = 'defeat'
                break
            
            # Check for click button (spam click until victory)
            click_pos = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
            if click_pos:
                self.update_status("Story Game: Click button detected, spam clicking...")
                # Spam click until victory or defeat is detected
                spam_count = 0
                while self.running and spam_count < 100:
                    click(*click_pos)
                    time.sleep(0.1)
                    spam_count += 1
                    
                    # Check for victory during spam
                    victory_check = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                    if victory_check:
                        self.update_status("Story Game: Victory detected after clicking!")
                        game_result = 'victory'
                        break
                    
                    # Check for defeat during spam
                    defeat_check = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.6, region=self.roblox_region)
                    if defeat_check:
                        self.update_status("Story Game: Defeat detected after clicking!")
                        game_result = 'defeat'
                        break
                
                if game_result:
                    break
            
            # Anti-AFK: click 6 times every 10 seconds in top right corner
            if time.time() - last_anti_afk_click > 10:
                if self.roblox_region:
                    afk_x = self.roblox_region[2] - 50  # 50 pixels from right edge
                    afk_y = self.roblox_region[1] + 50  # 50 pixels from top edge
                    for _ in range(6):
                        move_to(afk_x, afk_y, duration=0.1)
                        time.sleep(0.05)
                        click(afk_x, afk_y)
                        time.sleep(0.05)
                    self.update_status("Anti-AFK: Clicked corner 6x")
                last_anti_afk_click = time.time()
            
            time.sleep(0.5)
        
        if not self._check_running():
            return False
        
        # Send webhook - use challenge_location as loc since we're in challenge loop farming stages
        stage_time = time.time() - self.stage_start_time if self.stage_start_time else 0
        is_victory = (game_result == 'victory')
        challenge_location = self.config.get("challenge_location", "Leaf Village")
        self._send_discord_webhook(is_victory, stage_time, override_mode="Challenge Loop", override_location=challenge_location)
        
        # Click Replay to continue farming
        self.update_status("Stage Game: Clicking Replay...")
        time.sleep(2)
        replay_pos = wait_for_image(get_button_path("buttons/replay.png"), timeout=30, confidence=0.65,
                                    region=self.roblox_region, running_check=lambda: self.running)
        if replay_pos:
            move_to(*replay_pos, duration=0.3)
            time.sleep(0.2)
            click(*replay_pos)
            time.sleep(1)
            self.is_replay = True
        
        return True

    def _ensure_starting_image_folders(self):
        """Create starting image folder structure for all modes"""
        base_folder = os.path.join(get_app_path(), "starting image")
        
        # Story folders
        story_folders = ["Leaf", "Dark", "Planet", "Shibuya"]
        for folder in story_folders:
            os.makedirs(os.path.join(base_folder, "Story", folder), exist_ok=True)
        
        # Portals folders - create for each portal type
        portal_types = ["JJK Portals"]  # Add more portal types here as needed
        for portal in portal_types:
            os.makedirs(os.path.join(base_folder, "Portals", portal), exist_ok=True)
        
        # Raid and Siege folders are created dynamically based on location
        os.makedirs(os.path.join(base_folder, "Raid"), exist_ok=True)
        os.makedirs(os.path.join(base_folder, "Siege"), exist_ok=True)
    
    def _verify_required_images(self):
        """Verify all required button images exist. Returns (success, missing_images_list)"""
        missing = []
        
        # Core images (always required)
        core_images = [
            "buttons/Areas.png",
            "buttons/disconnect.png",
            "buttons/return.png",
            "buttons/victory.png",
            "buttons/defeat.png",
            "buttons/creatematch.png",
        ]
        
        # Mode-specific images based on current config
        mode = self.config.get("mode", "Story")
        
        # Story/Legend mode images
        if mode in ["Story", "Legend"]:
            core_images.extend([
                "buttons/leaf.png",
                "buttons/hollow.png",
                "buttons/planet.png",
                "buttons/shibuya.png",
                "buttons/Acts/act1.png",
                "buttons/Acts/act2.png",
                "buttons/Acts/act3.png",
                "buttons/Acts/act4.png",
                "buttons/Acts/act5.png",
                "buttons/Acts/act6.png",
            ])
            if mode == "Legend":
                core_images.append("buttons/Legend.png")
        
        # Portals mode images
        elif mode == "Portals":
            core_images.extend([
                "buttons/Items.png",
                "buttons/JJKportal.png",
                "buttons/use.png",
                "buttons/otherstart.png",
            ])
        
        # Raids mode images
        elif mode == "Raids":
            core_images.append("buttons/raids.png")
        
        # Siege mode images
        elif mode == "Siege":
            core_images.append("buttons/siege.png")
        
        # Auto-Challenges images
        if mode == "Auto-Challenges" or self.config.get("buy_rrs_enabled") or self.config.get("auto_challenges_enabled"):
            core_images.extend([
                "buttons/challenges.png",
                "buttons/regular.png",
                "buttons/trait.png",
                "buttons/start.png",
                "buttons/challengemaps/leaf.png",
                "buttons/challengemaps/planet.png",
                "buttons/challengemaps/dark.png",
                "buttons/challengemaps/shibuya.png",
            ])
        
        # Buy RRs images (if enabled)
        if self.config.get("buy_rrs_enabled"):
            core_images.extend([
                "buttons/summon.png",
                "buttons/essence.png",
            ])
        
        # Check each image
        for img_path in core_images:
            full_path = get_button_path(img_path)
            if not os.path.exists(full_path):
                missing.append(img_path)
        
        return (len(missing) == 0, missing)
    
    def start(self):
        """Start the macro"""
        if self.running:
            return
        
        # Ensure folder structure exists
        self._ensure_starting_image_folders()
        
        # Verify required images
        success, missing = self._verify_required_images()
        if not success:
            error_msg = "❌ MISSING REQUIRED IMAGES:\n\n" + "\n".join(f"  • {img}" for img in missing[:10])
            if len(missing) > 10:
                error_msg += f"\n  ... and {len(missing) - 10} more"
            error_msg += "\n\nPlease ensure all button images are in the buttons/ folder."
            self.update_status(error_msg)
            
            # Show error popup
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                messagebox.showerror(
                    "Missing Required Images",
                    f"Cannot start macro - {len(missing)} required image(s) missing:\n\n" + 
                    "\n".join(f"• {img}" for img in missing[:15]) +
                    (f"\n... and {len(missing) - 15} more" if len(missing) > 15 else "")
                )
                root.destroy()
            except:
                pass
            
            return  # Don't start if images are missing
        
        self.running = True
        self.paused = False
        self.thread = threading.Thread(target=self._run_macro, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop the macro"""
        self.running = False
        self.paused = False
        self.update_status("Macro stopped")
    
    def pause(self):
        """Pause the macro"""
        self.paused = True
        self.update_status("Macro paused")
    
    def resume(self):
        """Resume the macro"""
        self.paused = False
        self.update_status("Macro resumed")
    
    def _check_running(self):
        """Check if macro should continue running"""
        while self.paused and self.running:
            time.sleep(0.1)
        return self.running
    
    def _run_macro(self):
        """Main macro execution loop"""
        try:
            self.update_status("=== MACRO STARTED ===")
            self.update_status("Step 1: Initializing...")
            self.is_replay = False
            self.update_status("Step 1: Initialization complete")
            
            # Get Roblox window region
            self.update_status("Step 2: Detecting Roblox window...")
            # If region already provided externally (e.g. attached by UI), use it
            if self.roblox_region:
                self.update_status(f"Step 2: Using pre-set Roblox region: {self.roblox_region}")
            else:
                self.roblox_region = self.get_roblox_window_region()
                if not self.roblox_region:
                    self.update_status("Step 2: Roblox window not found, using full screen")
                    self.roblox_region = None
                else:
                    self.update_status("Step 2: Roblox window detected")
            
            # Click into game window to focus it (click further to the right side)
            self.update_status("Step 3: Focusing Roblox window...")
            if self.roblox_region:
                # Click 70% to the right of the window to avoid UI elements
                click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                click(click_x, click_y)
            else:
                screen_width, screen_height = get_screen_size()
                click(int(screen_width * 0.7), screen_height // 2)
            time.sleep(0.5)
            self.update_status("Step 3: Window focused")
            
            # Initial game navigation
            self.update_status("Step 4: Navigating to game...")
            
            # Check if Auto-Challenges mode
            mode = self.config.get("mode", "Story")
            if mode == "Auto-Challenges":
                self.update_status("=== AUTO-CHALLENGES MODE ===")
                self._run_auto_challenges_loop()
                return
            
            # Check if Custom mode
            if mode == "Custom":
                self.update_status("=== CUSTOM MODE ===")
                self._run_custom_mode()
                return
            
            # Check if Buy RRs is enabled (takes priority over challenges)
            buy_rrs_enabled = self.config.get("buy_rrs_enabled", False)
            if buy_rrs_enabled:
                self.update_status("=== BUY RRs ENABLED - Buying RRs first ===")
                if self._buy_rrs():
                    self.last_challenge_time = time.time()  # Start timer after Buy RRs completes
                    self.update_status("Buy RRs complete! Rejoining game...")
                else:
                    self.last_challenge_time = time.time()  # Start timer even on failure
                    self.update_status("Buy RRs failed, continuing anyway. Rejoining game...")
                
                # Rejoin after buying RRs
                import os
                close_roblox_instance(self.roblox_hwnd)
                self.roblox_region = None
                time.sleep(2)
                private_server_link = self.config.get("private_server_link", "")
                if not private_server_link:
                    private_server_link = "https://www.roblox.com/games/76806550943352/Anime-Paradox#ropro-quick-play"
                roblox_url = self._convert_to_roblox_protocol(private_server_link)
                os.startfile(roblox_url)
                self.update_status("Waiting 20 seconds for game to load...")
                time.sleep(20)
                if not self._trigger_attach():
                    self.update_status("Failed to reattach to Roblox window")
                    return
                if self.roblox_region:
                    click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                    click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                    click(click_x, click_y)
                    time.sleep(2)
                self.update_status("Buy RRs done, now continuing startup...")
            
            # Check if auto challenges is enabled for normal modes (Story, Raid, Siege, Legend)
            auto_challenges_enabled = self.config.get("auto_challenges_enabled", False)
            self.last_challenge_time = 0  # Track when last challenge was done
            CHALLENGE_INTERVAL = 60 * 60  # 60 minutes in seconds
            
            if auto_challenges_enabled:
                self.update_status("=== AUTO CHALLENGES ENABLED - Starting with challenge first ===")
                # Do a challenge first before starting normal loop
                if self._navigate_to_challenge():
                    if self._run_challenge_game():
                        self.last_challenge_time = time.time()
                        self.update_status("Initial challenge complete! Rejoining game...")
                    else:
                        self.last_challenge_time = time.time()  # Set timer even on failure so hourly check can run
                        self.update_status("Initial challenge game failed, but will retry in 1 hour. Rejoining game...")
                else:
                    self.last_challenge_time = time.time()  # Set timer even on failure so hourly check can run
                    self.update_status("Failed to navigate to initial challenge, but will retry in 1 hour. Rejoining game...")
                
                # Always rejoin after initial challenge attempt
                import os
                # Close Roblox
                close_roblox_instance(self.roblox_hwnd)
                self.roblox_region = None  # Detach from old window
                time.sleep(2)
                # Get link
                private_server_link = self.config.get("private_server_link", "")
                if not private_server_link:
                    private_server_link = "https://www.roblox.com/games/76806550943352/Anime-Paradox#ropro-quick-play"
                # Launch Roblox directly
                roblox_url = self._convert_to_roblox_protocol(private_server_link)
                os.startfile(roblox_url)
                self.update_status("Waiting 20 seconds for game to load...")
                time.sleep(20)
                # Reattach to Roblox window using UI attach
                self.update_status("Reattaching to Roblox window...")
                if not self._trigger_attach():
                    self.update_status("Failed to reattach to Roblox window")
                    return
                # Click to focus
                if self.roblox_region:
                    click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                    click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                    click(click_x, click_y)
                    time.sleep(2)
                self.update_status("Now starting normal stage loop...")
            
            if not self._navigate_to_game():
                self.update_status("Step 4: Navigation failed")
                return
            self.update_status("Step 4: Navigation complete (with positioning)")
            
            rejoin_after_games_enabled = self.config.get("rejoin_after_games_enabled", False)
            rejoin_after_games_enabled = self.config.get("rejoin_after_games_enabled", False)
            rejoin_after_games_count = self.config.get("rejoin_after_games_count", 5)
            games_since_rejoin = 0  # Track games since last rejoin
            if rejoin_after_games_enabled:
                self.update_status(f"Auto-Rejoin: Will rejoin every {rejoin_after_games_count} games")
            
            game_count = 0
            while self.running:
                disconnect_result = self._check_disconnect()
                if disconnect_result == False:
                    self.update_status("Stopping macro due to disconnect with no recovery link")
                    return
                elif disconnect_result == True:
                    self.update_status("Step 4: Re-navigating after reconnect...")
                    if not self._navigate_to_game():
                        self.update_status("Step 4: Navigation failed after reconnect")
                        return
                    self.update_status("Step 4: Navigation complete after reconnect")
                
                game_count += 1
                
                if auto_challenges_enabled and self.last_challenge_time > 0:
                    time_since_challenge = time.time() - self.last_challenge_time
                    remaining = CHALLENGE_INTERVAL - time_since_challenge
                    mins_remaining = max(0, int(remaining // 60))
                    self.update_status(f"\n=== GAME {game_count} START === ({mins_remaining}m until challenge)")
                else:
                    self.update_status(f"\n=== GAME {game_count} START ===")
                
                game_result = None
                
                self.update_status("Phase 1: Waiting for Yes button...")
                yes_pos = wait_for_image(get_button_path("buttons/Yes.png"), timeout=None, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
                
                if not self._check_running():
                    return
                
                if yes_pos:
                    self.update_status("Phase 1: Found Yes button, waiting 2 seconds...")
                    time.sleep(2.0)
                    
                    if not self.is_replay:
                        self.update_status("Phase 1.5: Zooming out...")
                        time.sleep(0.3)
                        if self.roblox_region:
                            center_x = (self.roblox_region[0] + self.roblox_region[2]) // 2
                            center_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                            drag_distance = (self.roblox_region[3] - self.roblox_region[1]) - 100
                            drag_down(center_x, center_y, distance=drag_distance, duration=0.3)
                        else:
                            screen_width, screen_height = get_screen_size()
                            drag_down(screen_width // 2, screen_height // 2, distance=screen_height - 100, duration=0.3)
                        time.sleep(0.3)
                        
                        self.update_status("Phase 1.6: Holding O key...")
                        time.sleep(0.3)
                        # Get zoom duration based on current mode
                        mode = self.config.get("mode", "Story")
                        zoom_duration = self.config.get(f"zoom_duration_{mode.lower()}", 0.3)
                        hold_key('o', duration=zoom_duration)
                        time.sleep(0.5)
                    else:
                        self.update_status("Phase 1.5-1.6: Skipping zoom/O key (replay)")
                    
                    mode = self.config.get("mode", "Story")
                    location = self.config.get("location", "Leaf Village")
                    location_lower = location.lower()
                    self.update_status(f"Positioning: Mode={mode}, Location={location}, Location_lower={location_lower}")
                    
                    if mode == "Siege" and ("blue" in location_lower or "dungeon" in location_lower):
                        self.update_status("Positioning: Blue Dungeon (Siege) detected, starting positioning sequence...")
                        
                        # Walk forward with W for 4.2 seconds
                        self.update_status("Positioning: Holding 'W' to move forward for 4.2 seconds...")
                        hold_key_directinput('w', 4.2)
                        time.sleep(0.3)
                        
                        self.update_status("Positioning: ✓ Positioning sequence complete")
                    
                    elif mode == "Raids" and ("frozen" in location_lower or "gate" in location_lower):
                        self.update_status("Positioning: Frozen Gate (Raid) detected, starting positioning sequence...")
                        
                        # Walk forward for 1.5 seconds
                        self.update_status("Positioning: Walking forward (W) for 1.5 seconds...")
                        hold_key_directinput('w', 1.5)
                        time.sleep(0.3)
                        
                        self.update_status("Positioning: ✓ Positioning sequence complete")
                    
                    elif "leaf" in location_lower or "village" in location_lower:
                        self.update_status("Positioning: Leaf Village detected, starting positioning sequence...")
                        
                        # Hold A to move right for 5 seconds
                        self.update_status("Positioning: Holding 'A' to move right for 5 seconds...")
                        hold_key_directinput('a', 2.0)
                        time.sleep(0.3)
                        
                        # Hold W to move forward for 2 seconds
                        self.update_status("Positioning: Holding 'W' to move forward for 2 seconds...")
                        hold_key_directinput('w', 1.8)
                        time.sleep(0.3)
                        
                        self.update_status("Positioning: ✓ Positioning sequence complete")
                    
                    elif "namak" in location_lower or "namek" in location_lower or "planet" in location_lower:
                        self.update_status("Positioning: Planet Namek detected, starting positioning sequence...")
                        
                        # Hold S to move down for 0.8 seconds
                        self.update_status("Positioning: Holding 'S' to move down for 0.8 seconds...")
                        hold_key_directinput('s', 1.1)
                        time.sleep(0.3)
                        
                        # Hold A to move left for 0.2 seconds
                        self.update_status("Positioning: Holding 'A' to move left for 0.2 seconds...")
                        hold_key_directinput('a', 0.2)
                        time.sleep(0.3)
                        
                        self.update_status("Positioning: ✓ Positioning sequence complete")
                    
                    elif "hollow" in location_lower or "dark" in location_lower:
                        self.update_status("Positioning: Dark Hollow detected, starting positioning sequence...")
                        
                        # Hold A to move left for 0.3 seconds
                        self.update_status("Positioning: Holding 'A' to move left for 0.3 seconds...")
                        hold_key_directinput('a', 1.2)
                        time.sleep(0.3)
                        
                        self.update_status("Positioning: ✓ Positioning sequence complete")
                    
                    elif "shibuya" in location_lower:
                        self.update_status("Positioning: Shibuya detected, starting positioning sequence...")
                        
                        # Walk forward for 3.5 seconds
                        self.update_status("Positioning: Holding 'W' to move forward for 3.5 seconds...")
                        hold_key_directinput('w', 3.5)
                        time.sleep(0.3)
                        
                        self.update_status("Positioning: ✓ Positioning sequence complete")
                    
                    elif mode == "Portals":
                        self.update_status("Positioning: Portal mode detected, starting positioning sequence...")
                        
                        # Walk left for 1 second
                        self.update_status("Positioning: Holding 'A' to move left for 1 second...")
                        hold_key_directinput('a', 1.0)
                        time.sleep(0.3)
                        
                        self.update_status("Positioning: ✓ Positioning sequence complete")
                    
                    self.update_status("Phase 1.7: Checking for early placement units...")
                    self._place_units_from_config(early_placement=True)
                    
                    self.update_status("Phase 1.8: Re-locating Yes button...")
                    yes_pos = find_image_on_screen(get_button_path("buttons/Yes.png"), confidence=0.65, region=self.roblox_region)
                    if not yes_pos:
                        self.update_status("Phase 1.8: Could not re-locate Yes button, searching again...")
                        yes_pos = wait_for_image(get_button_path("buttons/Yes.png"), timeout=None, confidence=0.60, region=self.roblox_region, running_check=lambda: self.running)
                    
                    if yes_pos:
                        self.update_status("Phase 1.9: Clicking Yes button...")
                        move_to(*yes_pos, duration=0.3)
                        time.sleep(0.2)
                        click(*yes_pos)
                        self.stage_start_time = time.time()
                        time.sleep(0.5)
                    else:
                        self.update_status("Phase 1.9: Warning - Yes button not found after early placement")
                else:
                    self.update_status("Phase 1: Could not find Yes button, skipping...")
                
                self.update_status("Phase 2: Starting unit placement...")
                self._place_units_from_config(early_placement=False)
                
                self.update_status("Phase 3: Waiting for game to end...")
                game_ended = False
                last_anti_afk_click = time.time()
                
                while self.running and not game_ended:
                    victory_pos = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.65, region=self.roblox_region)
                    if victory_pos and victory_pos[1] > 30:
                        self.update_status("Phase 3: Victory detected!")
                        game_result = 'victory'
                        time.sleep(1)
                        game_ended = True
                        break
                    
                    defeat_pos = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.65, region=self.roblox_region)
                    if defeat_pos:
                        self.update_status("Phase 3: Defeat detected!")
                        game_result = 'defeat'
                        time.sleep(1)
                        game_ended = True
                        break
                    
                    click_pos = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                    if click_pos:
                        self.update_status("Phase 3: Click button detected, spam clicking...")
                        # Spam click until victory is detected
                        spam_count = 0
                        while self.running and spam_count < 100:
                            click(*click_pos)
                            time.sleep(0.1)
                            spam_count += 1
                            
                            # Check for victory during spam
                            victory_check = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.65, region=self.roblox_region)
                            if victory_check:
                                self.update_status("Phase 3: Victory detected after clicking!")
                                game_result = 'victory'
                                game_ended = True
                                break
                        
                        if game_ended:
                            break
                    
                    # Anti-AFK: click 6 times every 10 seconds in top right corner
                    if time.time() - last_anti_afk_click > 10:
                        if self.roblox_region:
                            afk_x = self.roblox_region[2] - 50  # 50 pixels from right edge
                            afk_y = self.roblox_region[1] + 50  # 50 pixels from top edge
                            for _ in range(6):
                                move_to(afk_x, afk_y, duration=0.1)
                                time.sleep(0.05)
                                click(afk_x, afk_y)
                                time.sleep(0.05)
                            self.update_status("Anti-AFK: Clicked corner 6x")
                        last_anti_afk_click = time.time()
                    
                    time.sleep(0.5)
                
                if not self._check_running():
                    return
                
                # Calculate stage time and send webhook
                if game_result and self.stage_start_time:
                    stage_time = time.time() - self.stage_start_time
                    is_victory = game_result == 'victory'
                    # Send webhook notification with screenshot
                    self._send_discord_webhook(is_victory, stage_time)
                
                # Check if it's time for hourly side tasks (Buy RRs and/or Challenges)
                if (auto_challenges_enabled or buy_rrs_enabled) and self.last_challenge_time > 0:
                    time_since_challenge = time.time() - self.last_challenge_time
                    if time_since_challenge >= CHALLENGE_INTERVAL:
                        self.update_status(f"=== 60 MINUTES ELAPSED - Starting hourly side tasks ===")
                        self.is_replay = False  # Reset so navigation works after rejoin
                        
                        import os
                        
                        # STEP 1: Buy RRs if enabled
                        if buy_rrs_enabled:
                            self.update_status("Hourly: Rejoining for Buy RRs...")
                            close_roblox_instance(self.roblox_hwnd)
                            self.roblox_region = None
                            time.sleep(2)
                            private_server_link = self.config.get("private_server_link", "")
                            if not private_server_link:
                                private_server_link = "https://www.roblox.com/games/76806550943352/Anime-Paradox#ropro-quick-play"
                            roblox_url = self._convert_to_roblox_protocol(private_server_link)
                            os.startfile(roblox_url)
                            self.update_status("Waiting 20 seconds for game to load...")
                            time.sleep(20)
                            if not self._trigger_attach():
                                self.update_status("Failed to reattach to Roblox window")
                                return
                            if self.roblox_region:
                                click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                                click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                                click(click_x, click_y)
                                time.sleep(2)
                            
                            # Navigate and buy RRs
                            self.update_status("Hourly: Navigating to summon for Buy RRs...")
                            if self._buy_rrs():
                                self.update_status("Hourly: ✓ Buy RRs complete!")
                            else:
                                self.update_status("Hourly: Buy RRs failed")
                            
                            # Rejoin after Buy RRs
                            self.update_status("Hourly: Rejoining after Buy RRs...")
                            close_roblox_instance(self.roblox_hwnd)
                            self.roblox_region = None
                            time.sleep(2)
                            private_server_link = self.config.get("private_server_link", "")
                            if not private_server_link:
                                private_server_link = "https://www.roblox.com/games/76806550943352/Anime-Paradox#ropro-quick-play"
                            roblox_url = self._convert_to_roblox_protocol(private_server_link)
                            os.startfile(roblox_url)
                            self.update_status("Waiting 20 seconds for game to load...")
                            time.sleep(20)
                            if not self._trigger_attach():
                                self.update_status("Failed to reattach to Roblox window")
                                return
                            if self.roblox_region:
                                click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                                click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                                click(click_x, click_y)
                                time.sleep(2)
                        
                        # STEP 2: Challenge if enabled
                        if auto_challenges_enabled:
                            self.update_status("Hourly: Starting challenge...")
                            
                            # Navigate to challenge and run it (loops until win)
                            challenge_success = False
                            if self._navigate_to_challenge():
                                if self._run_challenge_game():
                                    self.last_challenge_time = time.time()
                                    self.update_status("Challenge complete!")
                                    challenge_success = True
                                else:
                                    self.update_status("Challenge game failed - will retry next hour")
                                    self.last_challenge_time = time.time()  # Reset timer even on failure to prevent immediate retry
                            else:
                                self.update_status("Failed to navigate to challenge - will retry next hour")
                                self.last_challenge_time = time.time()  # Reset timer even on failure to prevent immediate retry
                            
                            # Rejoin after challenge
                            self.update_status("Hourly: Rejoining after challenge...")
                            close_roblox_instance(self.roblox_hwnd)
                            self.roblox_region = None
                            time.sleep(2)
                            private_server_link = self.config.get("private_server_link", "")
                            if not private_server_link:
                                private_server_link = "https://www.roblox.com/games/76806550943352/Anime-Paradox#ropro-quick-play"
                            roblox_url = self._convert_to_roblox_protocol(private_server_link)
                            os.startfile(roblox_url)
                            self.update_status("Waiting 20 seconds for game to load...")
                            time.sleep(20)
                            if not self._trigger_attach():
                                self.update_status("Failed to reattach to Roblox window")
                                return
                            if self.roblox_region:
                                click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                                click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                                click(click_x, click_y)
                                time.sleep(2)
                        else:
                            # If only Buy RRs (no challenge), still set timer
                            self.last_challenge_time = time.time()
                        
                        # STEP 3: Navigate back to selected stage
                        self.update_status("Hourly: Navigating back to normal stage...")
                        if not self._navigate_to_game():
                            self.update_status("Failed to navigate back to stage after hourly tasks")
                            return
                        self.update_status("Hourly: Back to normal loop!")
                        continue
                
                # Track games for rejoin-after-games
                games_since_rejoin += 1
                
                # Check if we should rejoin after X games
                if rejoin_after_games_enabled and games_since_rejoin >= rejoin_after_games_count:
                    self.update_status(f"=== AUTO-REJOIN: {games_since_rejoin} games completed, rejoining ===")
                    self.is_replay = False
                    games_since_rejoin = 0
                    
                    # Rejoin
                    import subprocess
                    import os
                    close_roblox_instance(self.roblox_hwnd)
                    self.roblox_region = None
                    time.sleep(2)
                    private_server_link = self.config.get("private_server_link", "")
                    if not private_server_link:
                        private_server_link = "https://www.roblox.com/games/76806550943352/Anime-Paradox#ropro-quick-play"
                    roblox_url = self._convert_to_roblox_protocol(private_server_link)
                    os.startfile(roblox_url)
                    self.update_status("Auto-Rejoin: Waiting 20 seconds for game to load...")
                    time.sleep(20)
                    if not self._trigger_attach():
                        self.update_status("Auto-Rejoin: Failed to reattach to Roblox window")
                        return
                    if self.roblox_region:
                        click_x = self.roblox_region[0] + int((self.roblox_region[2] - self.roblox_region[0]) * 0.7)
                        click_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                        click(click_x, click_y)
                        time.sleep(2)
                    
                    # Re-navigate to stage
                    self.update_status("Auto-Rejoin: Navigating back to stage...")
                    if not self._navigate_to_game():
                        self.update_status("Auto-Rejoin: Failed to navigate back to stage")
                        return
                    self.update_status(f"Auto-Rejoin: Back to normal loop! Next rejoin in {rejoin_after_games_count} games")
                    continue
                
                mode = self.config.get("mode", "Story")
                if mode == "Portals":
                    self.update_status("Phase 4 (Portal): Looking for 'Use Portal' button...")
                    useportal_pos = wait_for_image(get_button_path("buttons/useportal.png"), timeout=30, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
                    
                    if not self._check_running():
                        return
                    
                    if useportal_pos:
                        self.update_status("Phase 4 (Portal): Found 'Use Portal', clicking...")
                        move_to(*useportal_pos, duration=0.3)
                        time.sleep(0.2)
                        click(*useportal_pos)
                        time.sleep(1.0)
                        
                        # Re-scan for portal items - scan all portal types with tier priority
                        portal_types = [
                            ("buttons/hightier.png", 5),  # Highest tier
                            ("buttons/redportal.png", 4),
                            ("buttons/goldportal.png", 3),
                            ("buttons/purpleportal.png", 2),
                            ("buttons/JJKportal.png", 1)  # Lowest tier
                        ]
                        
                        self.update_status("Phase 4 (Portal): Scanning for all portal types with scrolling...")
                        all_matches = []  # Will store tuples of (x, y, scroll_level, tier_priority)
                        scroll_attempts = 0
                        max_scroll_attempts = 5
                        clicks_per_scroll = 3  # How many scroll clicks per level
                        total_clicks_scrolled = 0  # Track actual scroll clicks
                        
                        while scroll_attempts < max_scroll_attempts:
                            if not self._check_running():
                                return
                            
                            # Scan for all portal types at current scroll position
                            self.update_status(f"Phase 4 (Portal): Scroll {scroll_attempts + 1}/{max_scroll_attempts} - Scanning...")
                            
                            # Use tier detection - checks all tier images and picks best match per portal
                            detected_portals = self._find_portals_with_tier_detection(portal_types, confidence=0.65)
                            for px, py, best_tier in detected_portals:
                                all_matches.append((px, py, scroll_attempts, best_tier))
                                tier_names = {5: "High Tier", 4: "Red", 3: "Gold", 2: "Purple", 1: "JJK"}
                                self.update_status(f"Phase 4 (Portal): Found {tier_names.get(best_tier, best_tier)} portal at scroll {scroll_attempts + 1}")
                            
                            self.update_status(f"Phase 4 (Portal): Scroll {scroll_attempts + 1} found {len(detected_portals)} portals ({len(all_matches)} total)")
                            
                            # Always continue scrolling to check all positions
                            scroll_attempts += 1
                            if scroll_attempts < max_scroll_attempts:
                                # Move mouse over the portal list area before scrolling
                                if self.roblox_region:
                                    scroll_x = self.roblox_region[0] + (self.roblox_region[2] - self.roblox_region[0]) // 2
                                    scroll_y = self.roblox_region[1] + (self.roblox_region[3] - self.roblox_region[1]) // 2
                                    move_to(scroll_x, scroll_y, duration=0.2)
                                    time.sleep(0.2)
                                self.update_status("Phase 4 (Portal): Scrolling down slowly to find more portals...")
                                from mouse_controller import scroll_down, scroll_up
                                scroll_down(clicks=clicks_per_scroll, delay=0.5)
                                total_clicks_scrolled += clicks_per_scroll
                                time.sleep(1.2)
                        
                        if not self._check_running():
                            return
                        
                        if not all_matches:
                            self.update_status("Phase 4 (Portal): ✗ Could not find any portal items")
                        else:
                            # Select highest tier portal, then highest scroll level (furthest down in list), then furthest right
                            highest_tier = max(all_matches, key=lambda p: p[3])[3]  # Get highest tier value
                            highest_tier_portals = [p for p in all_matches if p[3] == highest_tier]
                            # Among highest tier, select highest scroll_level, then furthest right (p[2]=scroll_level, p[0]=x)
                            furthest_portal = max(highest_tier_portals, key=lambda p: (p[2], p[0]))
                            furthest_x, furthest_y, furthest_scroll_level, furthest_tier = furthest_portal
                            
                            tier_names = {5: "High Tier", 4: "Red", 3: "Gold", 2: "Purple", 1: "JJK"}
                            self.update_status(f"Phase 4 (Portal): ✓ Found {len(all_matches)} total portals, selecting tier {tier_names.get(furthest_tier, furthest_tier)} at scroll level {furthest_scroll_level + 1}/{max_scroll_attempts}")
                            
                            # Calculate how many clicks to scroll back
                            # We're currently at total_clicks_scrolled position
                            # Target portal is at (furthest_scroll_level * clicks_per_scroll) position
                            target_click_position = furthest_scroll_level * clicks_per_scroll
                            clicks_to_scroll_back = total_clicks_scrolled - target_click_position
                            
                            if clicks_to_scroll_back > 0:
                                self.update_status(f"Phase 4 (Portal): Scrolling back up {clicks_to_scroll_back} clicks (from {total_clicks_scrolled} to {target_click_position}) to portal position...")
                                from mouse_controller import scroll_up
                                scroll_up(clicks=clicks_to_scroll_back, delay=0.5)
                                time.sleep(0.8)
                            
                            # Click the portal
                            self.update_status(f"Phase 4 (Portal): Clicking tier {tier_names.get(furthest_tier, furthest_tier)} portal at ({furthest_x}, {furthest_y})")
                            move_to(furthest_x, furthest_y, duration=0.3)
                            time.sleep(0.2)
                            click(furthest_x, furthest_y)
                            time.sleep(1.0)
                            
                            self.is_replay = True
                            self.update_status("Phase 4 (Portal): ✓ Portal selected, proceeding to positioning...")
                    else:
                        self.update_status("Phase 4 (Portal): Could not find 'Use Portal' button")
                else:
                    self.update_status("Phase 4: Looking for Replay button...")
                    replay_pos = wait_for_image(get_button_path("buttons/replay.png"), timeout=30, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
                    
                    if replay_pos:
                        self.update_status("Phase 4: Found Replay, clicking...")
                        move_to(*replay_pos, duration=0.3)
                        time.sleep(0.2)
                        click(*replay_pos)
                        time.sleep(1)
                        self.is_replay = True
                    else:
                        self.update_status("Phase 4: Could not find Replay button")
                
                self.update_status(f"=== GAME {game_count} COMPLETE ({games_since_rejoin}/{rejoin_after_games_count} until rejoin) ===")
                time.sleep(2)
                    
        except Exception as e:
            self.update_status(f"Error: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            self.running = False
            self.update_status("=== MACRO STOPPED ===")
    
    def _execute_preset(self, preset_name, unit_index):
        """Execute a preset's actions after unit placement/upgrade
        
        Args:
            preset_name: Name of the preset file (without .json)
            unit_index: Unit index for status messages
        """
        import json
        
        # Load preset file
        preset_path = os.path.join(get_app_path(), "presets", f"{preset_name}.json")
        if not os.path.exists(preset_path):
            self.update_status(f"Unit {unit_index}: Preset '{preset_name}' not found, skipping")
            return
        
        try:
            with open(preset_path, 'r', encoding='utf-8') as f:
                preset_data = json.load(f)
        except Exception as e:
            self.update_status(f"Unit {unit_index}: Error loading preset '{preset_name}': {e}")
            return
        
        actions = preset_data.get("Actions", [])
        enabled_actions = [a for a in actions if a.get("Enabled", False)]
        
        if not enabled_actions:
            self.update_status(f"Unit {unit_index}: Preset '{preset_name}' has no enabled actions")
            return
        
        self.update_status(f"Unit {unit_index}: Running preset '{preset_name}' ({len(enabled_actions)} actions)...")
        
        for action in enabled_actions:
            if not self.running:
                break
            
            action_type = action.get("Type", "Left Click")
            wait_time = float(action.get("Wait", 0.2))
            x = int(action.get("X", 0))
            y = int(action.get("Y", 0))
            text = action.get("Text", "")
            note = action.get("Note", "")
            action_idx = action.get("Index", "?")
            
            if action_type == "Wait":
                self.update_status(f"  Preset #{action_idx}: Waiting {wait_time}s{' - ' + note if note else ''}")
                time.sleep(wait_time)
            
            elif action_type == "Left Click":
                # Convert relative coordinates to absolute if Roblox region exists
                abs_x, abs_y = x, y
                if self.roblox_region:
                    abs_x = self.roblox_region[0] + x
                    abs_y = self.roblox_region[1] + y
                self.update_status(f"  Preset #{action_idx}: Left click ({x}, {y}){' - ' + note if note else ''}")
                time.sleep(wait_time)
                move_to(abs_x, abs_y, duration=0.1)
                time.sleep(0.05)
                click(abs_x, abs_y)
            
            elif action_type == "Right Click":
                abs_x, abs_y = x, y
                if self.roblox_region:
                    abs_x = self.roblox_region[0] + x
                    abs_y = self.roblox_region[1] + y
                self.update_status(f"  Preset #{action_idx}: Right click ({x}, {y}){' - ' + note if note else ''}")
                time.sleep(wait_time)
                move_to(abs_x, abs_y, duration=0.1)
                time.sleep(0.05)
                import pyautogui
                pyautogui.rightClick(abs_x, abs_y)
            
            elif action_type == "Type":
                self.update_status(f"  Preset #{action_idx}: Typing '{text}'{' - ' + note if note else ''}")
                time.sleep(wait_time)
                # Use win32 key sending for each character
                for char in text:
                    win32_press_key(char)
                    time.sleep(0.05)
            
            elif action_type == "Reposition":
                key = action.get("Key", "w").lower()
                hold_duration = float(action.get("HoldDuration", 1.0))
                self.update_status(f"  Preset #{action_idx}: Reposition - holding '{key.upper()}' for {hold_duration}s{' - ' + note if note else ''}")
                time.sleep(wait_time)
                hold_key_directinput(key, hold_duration)
                time.sleep(0.1)
        
        self.update_status(f"Unit {unit_index}: Preset '{preset_name}' complete!")
    
    def _run_custom_mode(self):
        """Run custom mode - zoom steps then place units from config, looping on game replays"""
        self.update_status("Custom Mode: Waiting for Yes button to start...")
        
        game_count = 0
        while self.running:
            game_count += 1
            self.update_status(f"\n=== CUSTOM GAME {game_count} START ===")
            
            # Phase 1: Wait for "Yes" button
            self.update_status("Custom: Waiting for Yes button...")
            yes_pos = wait_for_image(get_button_path("buttons/Yes.png"), timeout=None, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
            
            if not self._check_running():
                return
            
            if yes_pos:
                self.update_status("Custom: Found Yes button, waiting 2 seconds...")
                time.sleep(2.0)
                
                # Phase 1.5: Zoom out (skip on replay)
                if not self.is_replay:
                    self.update_status("Custom: Zooming out...")
                    time.sleep(0.3)
                    if self.roblox_region:
                        center_x = (self.roblox_region[0] + self.roblox_region[2]) // 2
                        center_y = (self.roblox_region[1] + self.roblox_region[3]) // 2
                        drag_distance = (self.roblox_region[3] - self.roblox_region[1]) - 100
                        drag_down(center_x, center_y, distance=drag_distance, duration=0.3)
                    else:
                        screen_width, screen_height = get_screen_size()
                        drag_down(screen_width // 2, screen_height // 2, distance=screen_height - 100, duration=0.3)
                    time.sleep(0.3)
                    
                    # Hold O key for zoom
                    self.update_status("Custom: Holding O key...")
                    time.sleep(0.3)
                    zoom_duration = self.config.get("zoom_duration_custom", 0.3)
                    hold_key('o', duration=zoom_duration)
                    time.sleep(0.5)
                else:
                    self.update_status("Custom: Skipping zoom (replay)")
                
                # Phase 2: Place units early (PlaceBeforeYes)
                self.update_status("Custom: Early unit placement...")
                self._place_units_from_config(early_placement=True)
                
                if not self._check_running():
                    return
                
                # Phase 3: Click Yes button
                self.update_status("Custom: Clicking Yes button...")
                yes_pos = wait_for_image(get_button_path("buttons/Yes.png"), timeout=10, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
                if yes_pos:
                    click(*yes_pos)
                    time.sleep(1.0)
                
                if not self._check_running():
                    return
                
                # Phase 4: Place remaining units
                self.update_status("Custom: Main unit placement...")
                self._place_units_from_config(early_placement=False)
                
                if not self._check_running():
                    return
                
                # Phase 5: Wait for game end (Victory or Defeat)
                self.update_status("Custom: Waiting for game result...")
                game_result = None
                
                while self.running and not game_result:
                    # Check for Victory
                    victory_pos = find_image_on_screen(get_button_path("buttons/Victory.png"), confidence=0.6, region=self.roblox_region)
                    # Check Y coordinate to prevent false positives near top of screen
                    if victory_pos and victory_pos[1] > 30:
                        game_result = 'victory'
                        self.update_status("Custom: Victory detected!")
                        break
                    
                    # Check for Defeat
                    defeat_pos = find_image_on_screen(get_button_path("buttons/Defeat.png"), confidence=0.6, region=self.roblox_region)
                    if defeat_pos:
                        game_result = 'defeat'
                        self.update_status("Custom: Defeat detected!")
                        break
                    
                    time.sleep(1.0)
                
                if not self._check_running():
                    return
                
                # Phase 6: Click Continue/Replay
                self.update_status("Custom: Looking for Continue button...")
                time.sleep(2.0)
                
                # Look for Continue button
                continue_pos = wait_for_image(get_button_path("buttons/Continue.png"), timeout=15, confidence=0.6, region=self.roblox_region, running_check=lambda: self.running)
                if continue_pos:
                    self.update_status("Custom: Clicking Continue...")
                    click(*continue_pos)
                    time.sleep(1.0)
                
                if not self._check_running():
                    return
                
                # Look for Replay button
                replay_pos = wait_for_image(get_button_path("buttons/Replay.png"), timeout=15, confidence=0.6, region=self.roblox_region, running_check=lambda: self.running)
                if replay_pos:
                    self.update_status("Custom: Clicking Replay...")
                    self.is_replay = True
                    click(*replay_pos)
                    time.sleep(1.0)
                else:
                    self.update_status("Custom: No Replay button found, ending loop")
                    break
                
                self.update_status(f"Custom: Game {game_count} complete ({game_result}), starting next game...")
            else:
                self.update_status("Custom: Timed out waiting for Yes button")
                break
        
        self.update_status("=== CUSTOM MODE COMPLETE ===")
    
    def _place_units_from_config(self, early_placement=False, override_mode=None, override_location=None, override_act=None):
        """Place units based on current location/act config"""
        import json
        
        location = override_location or self.config.get("location", "Leaf")
        act = override_act or self.config.get("act", "Act 1")
        mode = override_mode or self.config.get("mode", "Story")
        
        # Map mode folder names
        if mode == "Auto-Challenges":
            mode_folder = "Challenges"
        elif mode == "Raids":
            mode_folder = "Raid"
        else:
            mode_folder = mode
        
        config_path = os.path.join(get_app_path(), "Settings", mode_folder, location, f"{act}.json")
        
        if not os.path.exists(config_path):
            self.update_status(f"Unit Placement: No config found for {location}/{act}")
            return False
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                unit_config = json.load(f)
        except Exception as e:
            self.update_status(f"Unit Placement: Error loading config: {e}")
            return False
        
        units = unit_config.get("Units", [])
        if early_placement:
            enabled_units = [u for u in units if u.get("Enabled", False) and u.get("PlaceBeforeYes", False)]
        else:
            enabled_units = [u for u in units if u.get("Enabled", False) and not u.get("PlaceBeforeYes", False)]
        
        if not enabled_units:
            if early_placement:
                self.update_status("Unit Placement: No early placement units in config")
            else:
                self.update_status("Unit Placement: No enabled units in config")
            return False
        
        placement_type = "early" if early_placement else "normal"
        self.update_status(f"Unit Placement: Placing {len(enabled_units)} {placement_type} units...")
        
        for unit in enabled_units:
            if not self._check_running():
                return False
            
            unit_index = unit.get("Index", 0)
            action = unit.get("Action", "Place")
            
            if action == "Wait For":
                wait_seconds = 0
                try:
                    wait_seconds = float(unit.get("WaitSeconds", "0"))
                except ValueError:
                    wait_seconds = 0
                if wait_seconds > 0:
                    self.update_status(f"Unit Placement: Unit {unit_index} - Waiting {wait_seconds}s...")
                    # Wait in small increments so we can check for stop
                    waited = 0.0
                    while waited < wait_seconds and self.running:
                        sleep_chunk = min(0.5, wait_seconds - waited)
                        time.sleep(sleep_chunk)
                        waited += sleep_chunk
                        if not self._check_running():
                            return False
                    self.update_status(f"Unit Placement: Unit {unit_index} - Wait complete")
                else:
                    self.update_status(f"Unit Placement: Unit {unit_index} - Wait For with 0s, skipping")
                continue
            
            if action == "R-Click":
                x = unit.get("X", "")
                y = unit.get("Y", "")
                if not x or not y:
                    self.update_status(f"Unit Placement: Unit {unit_index} - No coordinates set for R-Click, skipping")
                    continue
                try:
                    rel_x = int(x)
                    rel_y = int(y)
                    if self.roblox_region:
                        click_x = self.roblox_region[0] + rel_x
                        click_y = self.roblox_region[1] + rel_y
                    else:
                        click_x = rel_x
                        click_y = rel_y
                except ValueError:
                    self.update_status(f"Unit Placement: Unit {unit_index} - Invalid coordinates for R-Click, skipping")
                    continue
                self.update_status(f"Unit Placement: Unit {unit_index} - R-Click at ({click_x}, {click_y}) [rel: {rel_x}, {rel_y}]")
                move_to(click_x, click_y, duration=0.1)
                time.sleep(0.15)
                right_click(click_x, click_y)
                time.sleep(self.placement_between_delay)
                continue
            
            # ========== PLACE ACTION (default) ==========
            slot = unit.get("Slot", "1")
            x = unit.get("X", "")
            y = unit.get("Y", "")
            upgrade_raw = unit.get("Upgrade", "0")
            # Handle "Max" upgrade level (use a large number like 99 to keep upgrading)
            if str(upgrade_raw).lower() == "max":
                upgrade_level = 99  # Will upgrade until no more upgrades available
            else:
                try:
                    upgrade_level = int(upgrade_raw)
                except ValueError:
                    upgrade_level = 0
            
            # Skip if coordinates not set
            if not x or not y:
                self.update_status(f"Unit Placement: Unit {unit_index} - No coordinates set, skipping")
                continue
            
            try:
                # Coordinates are relative to Roblox window - convert to absolute screen coordinates
                rel_x = int(x)
                rel_y = int(y)
                if self.roblox_region:
                    click_x = self.roblox_region[0] + rel_x
                    click_y = self.roblox_region[1] + rel_y
                else:
                    # Fallback if no region detected
                    click_x = rel_x
                    click_y = rel_y
            except ValueError:
                self.update_status(f"Unit Placement: Unit {unit_index} - Invalid coordinates, skipping")
                continue
            
            self.update_status(f"Unit Placement: Placing Unit {unit_index} (Slot {slot}) at ({click_x}, {click_y}) [rel: {rel_x}, {rel_y}]")
            
            # ========== INFINITE PLACEMENT LOOP - NEVER GIVE UP ==========
            attempt_count = 0
            
            while self.running:
                if not self._check_running():
                    return False
                
                # Check for victory first - game may end before we finish placing
                victory_pos = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                # Check Y coordinate to prevent false positives near top of screen
                if victory_pos and victory_pos[1] > 30:
                    self.update_status("Unit Placement: Victory detected!")
                    return True
                
                # Check for click button - spam click if found
                click_btn_pos = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                if click_btn_pos:
                    self.update_status("Unit Placement: Click button detected, spam clicking...")
                    spam_count = 0
                    while self.running and spam_count < 100:
                        click(*click_btn_pos)
                        time.sleep(0.1)
                        spam_count += 1
                        victory_check = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                        if victory_check:
                            self.update_status("Unit Placement: Victory detected!")
                            return True
                        defeat_check = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.6, region=self.roblox_region)
                        if defeat_check:
                            self.update_status("Unit Placement: Defeat detected!")
                            return False
                    return True  # After spam, consider it done
                
                # Check for defeat at start of each placement attempt
                if find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.65, region=self.roblox_region):
                    self.update_status(f"Unit Placement: Defeat detected before placement attempt!")
                    return False
                
                attempt_count += 1
                self.update_status(f"Unit Placement: === ATTEMPT {attempt_count} for Unit {unit_index} ===")
                
                # Step 1: Press slot key multiple times and click to initiate placement
                self.update_status(f"Unit Placement: Pressing slot {slot} and clicking...")
                if str(slot) != "0":
                    # Press slot key 3 times with delays to ensure it registers
                    for _ in range(3):
                        win32_press_key(slot)
                        time.sleep(0.1)
                    time.sleep(self.slot_press_delay)
                
                move_to(click_x, click_y, duration=0.1)
                time.sleep(0.15)
                click(click_x, click_y)
                time.sleep(0.25)
                
                # Step 2: INFINITE CONFIRMATION LOOP - keep pressing slot and clicking until upg.png found
                self.update_status(f"Unit Placement: Starting infinite confirmation loop...")
                confirm_clicks = 0
                upg_pos = None
                
                while self.running:
                    if not self._check_running():
                        return False
                    
                    # Check for victory first - game may end during placement
                    victory_pos = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                    # Check Y coordinate to prevent false positives near top of screen
                    if victory_pos and victory_pos[1] > 30:
                        self.update_status("Unit Placement: Victory detected during placement!")
                        return True
                    
                    # Check for click button - spam click if found
                    click_btn_pos = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                    if click_btn_pos:
                        self.update_status("Unit Placement: Click button detected during placement, spam clicking...")
                        spam_count = 0
                        while self.running and spam_count < 100:
                            click(*click_btn_pos)
                            time.sleep(0.1)
                            spam_count += 1
                            victory_check = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                            if victory_check:
                                self.update_status("Unit Placement: Victory detected!")
                                return True
                            defeat_check = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.6, region=self.roblox_region)
                            if defeat_check:
                                self.update_status("Unit Placement: Defeat detected!")
                                return False
                        return True  # After spam, consider it done
                    
                    # Check for defeat EVERY iteration
                    if find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.65, region=self.roblox_region):
                        self.update_status(f"Unit Placement: Defeat detected during placement!")
                        return False
                    
                    confirm_clicks += 1
                    
                    # Alternate between two confirmation methods
                    if confirm_clicks % 2 == 1:
                        # Odd attempts: Hover away briefly, then just click placement (no slot key)
                        # Move mouse away first
                        if self.roblox_region:
                            away_x = self.roblox_region[0] + 100
                            away_y = self.roblox_region[1] + 100
                        else:
                            away_x = click_x + 200
                            away_y = click_y + 200
                        move_to(away_x, away_y, duration=0.05)
                        time.sleep(0.05)
                        # Now click placement coordinates
                        move_to(click_x, click_y, duration=0.1)
                        time.sleep(0.1)
                        click(click_x, click_y)
                    else:
                        # Even attempts: Press slot key then click
                        if str(slot) != "0":
                            win32_press_key(slot)
                            time.sleep(0.1)
                        move_to(click_x, click_y, duration=0.1)
                        time.sleep(0.1)
                        click(click_x, click_y)
                    
                    time.sleep(self.placement_confirm_delay)
                    
                    # Check for upg.png after each click
                    upg_pos = find_image_on_screen("unit stuff/upg.png", confidence=0.70, region=self.roblox_region)
                    if upg_pos:
                        self.update_status(f"Unit Placement: Found upg.png at {upg_pos} after {confirm_clicks} confirm clicks!")
                        break
                    
                    # Log progress every 10 clicks
                    if confirm_clicks % 10 == 0:
                        self.update_status(f"Unit Placement: Confirm clicks: {confirm_clicks}...")
                
                # Step 3: Check if placement succeeded
                if upg_pos:
                    self.update_status(f"Unit Placement: ✓ Unit {unit_index} PLACED after {confirm_clicks} confirm clicks!")
                    
                    # === PLACEMENT SUCCEEDED - NOW DO POST-PLACEMENT ACTIONS ===
                    
                    # POST-ACTION 1: AutoUpgrade toggle (only when AutoUpgrade is explicitly enabled)
                    if unit.get("AutoUpgrade", False):
                        self.update_status(f"Unit Placement: Pressing AutoUpgrade for Unit {unit_index}...")
                        time.sleep(0.7)  # Wait longer to ensure panel is stable
                        
                        # Find and click autoupg.png once, then click off
                        autoup_pos = find_image_on_screen(get_button_path("buttons/autoupg.png"), confidence=0.55, region=self.roblox_region)
                        if autoup_pos:
                            move_to(*autoup_pos, duration=self.move_duration)
                            time.sleep(0.2)
                            click(*autoup_pos)
                            self.update_status(f"Unit Placement: Clicked autoupg.png")
                            time.sleep(0.5)  # Wait longer after clicking
                        else:
                            self.update_status(f"Unit Placement: autoupg.png not found, pressing Z key...")
                            win32_press_key('z')
                            time.sleep(0.5)
                    
                    # POST-ACTION 2: Manual upgrades
                    if upgrade_level > 0:
                        is_max = (upgrade_level == 99)
                        
                        if is_max:
                            # MAX UPGRADE - infinite loop until upgmax.png or defeat
                            self.update_status(f"Unit Placement: Upgrading Unit {unit_index} to MAX (pressing T)...")
                            upgrade_presses = 0
                            
                            # Try to find upgrade.png position for hovering
                            upgrade_btn_pos = find_image_on_screen(get_button_path("buttons/upgrade.png"), confidence=0.70, region=self.roblox_region)
                            if upgrade_btn_pos:
                                self.update_status(f"Unit Placement: Found upgrade.png at {upgrade_btn_pos} for hover alternation")
                            else:
                                # Use default position above unit if upgrade.png not found
                                upgrade_btn_pos = (click_x, click_y - 80)
                            
                            while self.running:
                                # Check for victory first
                                victory_pos = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                                # Check Y coordinate to prevent false positives near top of screen
                                if victory_pos and victory_pos[1] > 30:
                                    self.update_status("Unit Placement: Victory detected!")
                                    break
                                
                                # Check for click button (spam click until victory)
                                click_pos = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                                if click_pos:
                                    self.update_status(f"Unit Placement: Click button detected, spam clicking for victory...")
                                    spam_count = 0
                                    while self.running and spam_count < 100:
                                        click(*click_pos)
                                        time.sleep(0.1)
                                        spam_count += 1
                                        
                                        # Check for victory during spam
                                        victory_check = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                                        if victory_check:
                                            self.update_status("Unit Placement: Victory detected!")
                                            break
                                        
                                        # Check for defeat during spam
                                        defeat_check = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.6, region=self.roblox_region)
                                        if defeat_check:
                                            self.update_status("Unit Placement: Defeat detected!")
                                            break
                                    break  # Exit upgrade loop after click spam
                                
                                # Check for defeat
                                defeat_pos = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.65, region=self.roblox_region)
                                if defeat_pos:
                                    self.update_status(f"Unit Placement: Defeat detected, clicking and stopping upgrades")
                                    click(*defeat_pos)
                                    time.sleep(0.3)
                                    break
                                
                                # Check for max upgrade reached (very high confidence to avoid false positives)
                                if find_image_on_screen("unit stuff/upgmax.png", confidence=0.75, region=self.roblox_region):
                                    self.update_status(f"Unit Placement: ✓ MAX upgrade reached after {upgrade_presses} T presses!")
                                    break
                                
                                # Alternate between upgrade.png and unit position
                                if upgrade_presses % 2 == 0:
                                    # Hover to upgrade.png and click
                                    move_to(*upgrade_btn_pos, duration=0.05)
                                    click(*upgrade_btn_pos)
                                else:
                                    # Hover back to unit position and click
                                    move_to(click_x, click_y, duration=0.05)
                                    click(click_x, click_y)
                                
                                # Press T key 5 times per iteration for faster upgrades
                                for _ in range(5):
                                    win32_press_key('t')
                                    time.sleep(0.01)  # Small delay between presses
                                upgrade_presses += 5
                                if upgrade_presses % 10 == 0:
                                    self.update_status(f"Unit Placement: T presses: {upgrade_presses}...")
                                
                                # Immediate check for click.png right after T press (before sleep)
                                immediate_click_check = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                                if immediate_click_check:
                                    continue  # Go back to top of loop to process click spam
                                
                                # Split the delay into smaller checks to detect click.png faster
                                sleep_time = self.t_press_delay
                                sleep_interval = 0.05
                                elapsed = 0
                                while elapsed < sleep_time:
                                    time.sleep(sleep_interval)
                                    elapsed += sleep_interval
                                    # Quick check for click.png during sleep
                                    quick_click_check = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                                    if quick_click_check:
                                        break  # Exit sleep early if click detected
                        else:
                            # FIXED LEVEL UPGRADE
                            self.update_status(f"Unit Placement: Upgrading Unit {unit_index} to level {upgrade_level} (pressing T)...")
                            
                            # Try to find upgrade.png position for hovering
                            upgrade_btn_pos = find_image_on_screen(get_button_path("buttons/upgrade.png"), confidence=0.70, region=self.roblox_region)
                            if upgrade_btn_pos:
                                self.update_status(f"Unit Placement: Found upgrade.png at {upgrade_btn_pos} for hover alternation")
                            else:
                                # Use default position above unit if upgrade.png not found
                                upgrade_btn_pos = (click_x, click_y - 80)
                            
                            for target_lvl in range(1, upgrade_level + 1):
                                if not self.running:
                                    break
                                
                                target_img = f"unit stuff/upg{target_lvl}.png"
                                self.update_status(f"Unit Placement: Upgrading to level {target_lvl}...")
                                upgrade_presses = 0
                                
                                while self.running:
                                    # Check for victory first
                                    victory_pos = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                                    # Check Y coordinate to prevent false positives near top of screen
                                    if victory_pos and victory_pos[1] > 30:
                                        self.update_status("Unit Placement: Victory detected!")
                                        break
                                    
                                    # Check for click button (spam click until victory)
                                    click_pos = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                                    if click_pos:
                                        self.update_status(f"Unit Placement: Click button detected, spam clicking for victory...")
                                        spam_count = 0
                                        while self.running and spam_count < 100:
                                            click(*click_pos)
                                            time.sleep(0.1)
                                            spam_count += 1
                                            
                                            # Check for victory during spam
                                            victory_check = find_image_on_screen(get_button_path("buttons/victory.png"), confidence=0.6, region=self.roblox_region)
                                            if victory_check:
                                                self.update_status("Unit Placement: Victory detected!")
                                                break
                                            
                                            # Check for defeat during spam
                                            defeat_check = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.6, region=self.roblox_region)
                                            if defeat_check:
                                                self.update_status("Unit Placement: Defeat detected!")
                                                break
                                        break  # Exit upgrade loop after click spam
                                    
                                    # Check for defeat
                                    defeat_pos = find_image_on_screen(get_button_path("buttons/defeat.png"), confidence=0.65, region=self.roblox_region)
                                    if defeat_pos:
                                        self.update_status(f"Unit Placement: Defeat detected, clicking and stopping upgrades")
                                        click(*defeat_pos)
                                        time.sleep(0.3)
                                        break
                                    
                                    # Check if level reached (very high confidence to avoid false positives)
                                    if find_image_on_screen(target_img, confidence=0.90, region=self.roblox_region):
                                        self.update_status(f"Unit Placement: ✓ Level {target_lvl} reached!")
                                        break
                                    
                                    # Alternate between upgrade.png and unit position
                                    if upgrade_presses % 2 == 0:
                                        # Hover to upgrade.png and click
                                        move_to(*upgrade_btn_pos, duration=0.05)
                                        click(*upgrade_btn_pos)
                                    else:
                                        # Hover back to unit position and click
                                        move_to(click_x, click_y, duration=0.05)
                                        click(click_x, click_y)
                                    
                                    # Press T key 5 times per iteration for faster upgrades
                                    for _ in range(5):
                                        win32_press_key('t')
                                        time.sleep(0.01)  # Small delay between presses
                                    upgrade_presses += 5
                                    if upgrade_presses % 10 == 0:
                                        self.update_status(f"Unit Placement: T presses: {upgrade_presses}...")
                                    
                                    # Immediate check for click.png right after T press (before sleep)
                                    immediate_click_check = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                                    if immediate_click_check:
                                        continue  # Go back to top of loop to process click spam
                                    
                                    # Split the delay into smaller checks to detect click.png faster
                                    sleep_time = self.t_press_delay
                                    sleep_interval = 0.05
                                    elapsed = 0
                                    while elapsed < sleep_time:
                                        time.sleep(sleep_interval)
                                        elapsed += sleep_interval
                                        # Quick check for click.png during sleep
                                        quick_click_check = find_image_on_screen(get_button_path("buttons/click.png"), confidence=0.45, region=self.roblox_region)
                                        if quick_click_check:
                                            break  # Exit sleep early if click detected
                    
                    # POST-ACTION 3: Auto ability activation (if enabled for this unit)
                    if unit.get("AutoAbility", False):
                        self.update_status(f"Unit Placement: Activating ability...")
                        if self.roblox_region:
                            ability_x = self.roblox_region[0] + 376
                            ability_y = self.roblox_region[1] + 226
                            move_to(ability_x, ability_y, duration=0.2)
                            time.sleep(0.1)
                            # Click ability button 20 times with fast clicks
                            for _ in range(20):
                                click(ability_x, ability_y)
                                time.sleep(0.02)
                            time.sleep(0.2)
                    
                    # POST-ACTION 4: Execute preset actions (if a preset is assigned)
                    preset_name = unit.get("Preset", "")
                    if preset_name:
                        self._execute_preset(preset_name, unit_index)
                    
                    # POST-ACTION 5: Close unit panel by clicking unit position and verify it closes
                    self.update_status(f"Unit Placement: Closing unit panel...")
                    time.sleep(0.3)
                    
                    # Click the unit position to close the panel
                    move_to(click_x, click_y, duration=self.move_duration)
                    time.sleep(0.1)
                    click(click_x, click_y)
                    time.sleep(0.2)
                    
                    # Verify panel is closed by checking if upg.png disappears
                    panel_close_attempts = 0
                    while self.running and panel_close_attempts < 10:
                        upg_check = find_image_on_screen("unit stuff/upg.png", confidence=0.70, region=self.roblox_region)
                        if not upg_check:
                            self.update_status(f"Unit Placement: ✓ Panel closed successfully")
                            break
                        
                        # Panel still open, click again
                        panel_close_attempts += 1
                        self.update_status(f"Unit Placement: Panel still open, clicking again (attempt {panel_close_attempts})...")
                        move_to(click_x, click_y, duration=0.05)
                        time.sleep(0.05)
                        click(click_x, click_y)
                        time.sleep(0.2)
                    
                    time.sleep(self.placement_between_delay)  # Configurable delay between placements
                    break  # EXIT the outer placement loop - unit is done!
            
            # Small delay between units
            time.sleep(0.3)
        
        self.update_status(f"Unit Placement: Completed placing {len(enabled_units)} units")
        return True
    
    def _navigate_to_game(self):
        """Navigate through menus to start the game"""
        if self.is_replay:
            self.update_status("Navigation: Skipping (replay)")
            return True
            
        mode = self.config.get("mode", "Story")
        self.update_status(f"Navigation: Mode = {mode}")
        
        # Portals mode has its own navigation (doesn't use Areas)
        if mode == "Portals":
            return self._navigate_portal_mode()
        
        # Step 1: Find and click "Areas"
        self.update_status("Navigation: Searching for 'Areas' button (image detection)...")
        areas_pos = wait_for_image(get_button_path("buttons/Areas.png"), timeout=35, confidence=self.get_confidence(0.65),
                                    region=self.roblox_region, running_check=lambda: self.running,
                                    warning_callback=lambda: self.update_status("⚠️ Still searching... Try 'Calculate Best Tolerance' in Settings if stuck!"),
                                    warning_time=10)
        
        if not self._check_running():
            return False
        
        if not areas_pos:
            self.update_status("Navigation: ✗ Could not find 'Areas' button")
            return False
        
        self.update_status(f"Navigation: ✓ Found 'Areas' at {areas_pos}, hovering and clicking...")
        move_to(*areas_pos, duration=0.3)
        time.sleep(0.2)
        click(*areas_pos)
        time.sleep(1.0)
        
        # For Raids and Siege, go directly to mode-specific navigation
        if mode == "Raids":
            return self._navigate_raid_mode()
        elif mode == "Siege":
            return self._navigate_siege_mode()
        
        # For Story/Legend modes, click Story button first
        # Step 2: Look for and click "Story"
        self.update_status("Navigation: Searching for 'Story' button (image detection)...")
        story_pos = wait_for_image(get_button_path("buttons/Story.png"), timeout=None, confidence=0.65,
                                   region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not story_pos:
            self.update_status("Navigation: ✗ Could not find 'Story' button")
            return False
        
        self.update_status(f"Navigation: ✓ Found 'Story' at {story_pos}, hovering and clicking...")
        move_to(*story_pos, duration=0.3)
        time.sleep(0.2)
        click(*story_pos)
        time.sleep(1.0)
        
        # Step 3: Look for "X" button
        self.update_status("Navigation: Searching for 'X' button...")
        x_pos = wait_for_image(get_button_path("buttons/X.png"), timeout=None, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not x_pos:
            self.update_status("Navigation: ✗ Could not find 'X' button")
            return False
        
        self.update_status(f"Navigation: ✓ Found 'X' at {x_pos}, hovering and clicking...")
        move_to(*x_pos, duration=0.3)
        time.sleep(0.2)
        click(*x_pos)
        time.sleep(0.5)
        
        # Step 4: Hold W to walk forward
        self.update_status("Navigation: Walking forward (holding W)...")
        hold_key('w', duration=3.0)
        time.sleep(0.5)
        
        if mode == "Story":
            return self._navigate_story_mode()
        elif mode == "Legend":
            return self._navigate_legend_mode()
        
        return True
    
    def _navigate_raid_mode(self):
        """Navigate Raid mode - Areas -> Raid -> walk forward -> walk left -> find Frozen Gate"""
        location = self.config.get("location", "Frozen Gate")
        act = self.config.get("act", "Act 1").replace(" ", "").replace("Act", "")
        self.update_status(f"Raid Mode: Location = {location}, Act = {act}")
        
        # Step 1: Find and click "raids" button
        self.update_status("Raid Mode: Step 1 - Searching for 'Raid' button...")
        raid_pos = wait_for_image(get_button_path("buttons/raids.png"), timeout=30, confidence=0.65, 
                                  region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not raid_pos:
            self.update_status("Raid Mode: ✗ Could not find 'Raid' button")
            return False
        
        self.update_status(f"Raid Mode: ✓ Found 'Raid' at {raid_pos}, clicking...")
        move_to(*raid_pos, duration=0.3)
        time.sleep(0.2)
        click(*raid_pos)
        time.sleep(1.0)
        
        # Step 2: Walk forward with W for 3 seconds
        self.update_status("Raid Mode: Step 2 - Walking forward (W) for 3 seconds...")
        hold_key_directinput('w', 3.0)
        time.sleep(0.3)
        
        # Step 3: Walk left with A for 3 seconds
        self.update_status("Raid Mode: Step 3 - Walking left (A) for 3 seconds...")
        hold_key_directinput('a', 3.0)
        time.sleep(0.3)
        
        # Step 4: Find and click "Create Match" button
        self.update_status("Raid Mode: Step 4 - Searching for 'Create Match' button...")
        creatematch_pos = wait_for_image(get_button_path("buttons/creatematch.png"), timeout=30, confidence=0.65, 
                                         region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not creatematch_pos:
            self.update_status("Raid Mode: ✗ Could not find 'Create Match' button")
            return False
        
        self.update_status(f"Raid Mode: ✓ Found 'Create Match' at {creatematch_pos}, clicking...")
        move_to(*creatematch_pos, duration=0.3)
        time.sleep(0.2)
        click(*creatematch_pos)
        time.sleep(1.0)
        
        # Step 5: Find Frozen Gate button
        self.update_status("Raid Mode: Step 5 - Searching for 'Frozen Gate'...")
        frozen_pos = wait_for_image(get_button_path("buttons/Frozen.png"), timeout=30, confidence=0.65, 
                                    region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not frozen_pos:
            self.update_status("Raid Mode: ✗ Could not find 'Frozen Gate'")
            return False
        
        # Step 6: Click on Frozen Gate
        self.update_status(f"Raid Mode: ✓ Found 'Frozen Gate' at {frozen_pos}, clicking...")
        move_to(*frozen_pos, duration=0.3)
        time.sleep(0.2)
        click(*frozen_pos)
        time.sleep(0.5)
        
        # Step 7: Click on the act
        act_image = get_button_path(f"buttons/Acts/act{act}.png")
        self.update_status(f"Raid Mode: Step 7 - Searching for Act {act}...")
        act_pos = wait_for_image(act_image, timeout=30, confidence=0.55, 
                                 region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if act_pos:
            self.update_status(f"Raid Mode: ✓ Found Act {act}, clicking...")
            move_to(*act_pos, duration=0.3)
            time.sleep(0.2)
            click(*act_pos)
            time.sleep(0.5)
        else:
            self.update_status(f"Raid Mode: ✗ Could not find Act {act}")
            return False
        
        # Step 8: Click Start button
        self.update_status("Raid Mode: Step 8 - Searching for 'Start' button...")
        start_pos = wait_for_image(get_button_path("buttons/Start.png"), timeout=30, confidence=0.65, 
                                   region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if start_pos:
            self.update_status("Raid Mode: ✓ Found 'Start' button, clicking...")
            move_to(*start_pos, duration=0.3)
            time.sleep(0.2)
            click(*start_pos)
            time.sleep(0.5)
        else:
            self.update_status("Raid Mode: ✗ Could not find 'Start' button")
            return False
        
        # Step 9: Click other start button
        self.update_status("Raid Mode: Step 9 - Searching for other start button...")
        otherstart_pos = wait_for_image(get_button_path("buttons/otherstart.png"), timeout=30, confidence=0.65, 
                                        region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if otherstart_pos:
            self.update_status("Raid Mode: ✓ Found other start button, clicking...")
            move_to(*otherstart_pos, duration=0.4)
            time.sleep(0.3)
            click(*otherstart_pos)
            time.sleep(0.5)
        else:
            self.update_status("Raid Mode: ✗ Could not find other start button")
            return False
        
        # Step 10: Wait for game to load and detect Yes button
        if location == "Frozen Gate":
            self.update_status("Raid Mode: Step 10 - Waiting for game to load (detecting 'Yes' button)...")
            yes_pos = wait_for_image(get_button_path("buttons/Yes.png"), timeout=None, confidence=0.65, 
                                     region=self.roblox_region, running_check=lambda: self.running)
            
            if not self._check_running():
                return False
            
            if not yes_pos:
                self.update_status("Raid Mode: ✗ Could not find 'Yes' button")
                return False
            
            self.update_status("Raid Mode: ✓ Found 'Yes' button at {yes_pos}")
        
        self.update_status("Raid Mode: Navigation complete!")
        return True
    
    def _navigate_siege_mode(self):
        """Navigate Siege mode - Areas -> Siege -> walk forward -> hold D until Blue detected"""
        location = self.config.get("location", "Blue Dungeon")
        act = self.config.get("act", "Act 1").replace(" ", "").replace("Act", "")
        self.update_status(f"Siege Mode: Location = {location}, Act = {act}")
        
        # Step 1: Find and click "siege" button
        self.update_status("Siege Mode: Step 1 - Searching for 'Siege' button...")
        siege_pos = wait_for_image(get_button_path("buttons/siege.png"), timeout=30, confidence=0.65, 
                                   region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not siege_pos:
            self.update_status("Siege Mode: ✗ Could not find 'Siege' button")
            return False
        
        self.update_status(f"Siege Mode: ✓ Found 'Siege' at {siege_pos}, clicking...")
        move_to(*siege_pos, duration=0.3)
        time.sleep(0.2)
        click(*siege_pos)
        time.sleep(1.0)
        
        # Step 2: Walk forward with W for 1.5 seconds
        self.update_status("Siege Mode: Step 2 - Walking forward (W) for 1.5 seconds...")
        hold_key_directinput('w', 1.5)
        time.sleep(0.3)
        
        # Step 3: Hold D to walk right until "Create Match" is found
        self.update_status("Siege Mode: Step 3 - Walking right (D) until 'Create Match' appears...")
        
        def check_create_match():
            return find_image_on_screen(get_button_path("buttons/creatematch.png"), confidence=0.65, region=self.roblox_region)
        
        creatematch_pos = hold_key_until_condition('d', check_create_match, timeout=15, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not creatematch_pos:
            # Try waiting a bit more if not found
            self.update_status("Siege Mode: Create Match not found while walking, searching...")
            creatematch_pos = wait_for_image(get_button_path("buttons/creatematch.png"), timeout=10, confidence=0.65, 
                                             region=self.roblox_region, running_check=lambda: self.running)
        
        if not creatematch_pos:
            self.update_status("Siege Mode: ✗ Could not find 'Create Match' button")
            return False
        
        self.update_status(f"Siege Mode: ✓ Found 'Create Match' at {creatematch_pos}, clicking...")
        move_to(*creatematch_pos, duration=0.3)
        time.sleep(0.2)
        click(*creatematch_pos)
        time.sleep(1.0)
        
        # Step 4: Find Blue Dungeon button
        self.update_status("Siege Mode: Step 4 - Searching for 'Blue Dungeon'...")
        blue_pos = wait_for_image(get_button_path("buttons/Blue.png"), timeout=30, confidence=0.65, 
                                  region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not blue_pos:
            self.update_status("Siege Mode: ✗ Could not find 'Blue Dungeon'")
            return False
        
        # Step 5: Click on Blue Dungeon
        self.update_status(f"Siege Mode: ✓ Found 'Blue Dungeon' at {blue_pos}, clicking...")
        move_to(*blue_pos, duration=0.3)
        time.sleep(0.2)
        click(*blue_pos)
        time.sleep(0.5)
        
        # Step 6: Click on the act
        act_image = get_button_path(f"buttons/Acts/act{act}.png")
        self.update_status(f"Siege Mode: Step 6 - Searching for Act {act}...")
        act_pos = wait_for_image(act_image, timeout=30, confidence=0.55, 
                                 region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if act_pos:
            self.update_status(f"Siege Mode: ✓ Found Act {act}, clicking...")
            move_to(*act_pos, duration=0.3)
            time.sleep(0.2)
            click(*act_pos)
            time.sleep(0.5)
        else:
            self.update_status(f"Siege Mode: ✗ Could not find Act {act}")
            return False
        
        # Step 7: Click Start button
        self.update_status("Siege Mode: Step 7 - Searching for 'Start' button...")
        start_pos = wait_for_image(get_button_path("buttons/Start.png"), timeout=30, confidence=0.65, 
                                   region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if start_pos:
            self.update_status("Siege Mode: ✓ Found 'Start' button, clicking...")
            move_to(*start_pos, duration=0.3)
            time.sleep(0.2)
            click(*start_pos)
            time.sleep(0.5)
        else:
            self.update_status("Siege Mode: ✗ Could not find 'Start' button")
            return False
        
        # Step 8: Click other start button
        self.update_status("Siege Mode: Step 8 - Searching for other start button...")
        otherstart_pos = wait_for_image(get_button_path("buttons/otherstart.png"), timeout=30, confidence=0.65, 
                                        region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if otherstart_pos:
            self.update_status("Siege Mode: ✓ Found other start button, clicking...")
            move_to(*otherstart_pos, duration=0.4)
            time.sleep(0.3)
            click(*otherstart_pos)
            time.sleep(0.5)
        else:
            self.update_status("Siege Mode: ✗ Could not find other start button")
            return False
        
        self.update_status("Siege Mode: Navigation complete!")
        return True

    def _navigate_legend_mode(self):
        """Navigate Legend mode menus - same as Story but clicks Legend.png after Create Match"""
        location = self.config.get("location", "Planet Namek")
        act = self.config.get("act", "Act 1").replace(" ", "").replace("Act", "")
        self.update_status(f"Legend Mode: Location = {location}, Act = {act}")
        
        # Hold A until "Create Match" appears
        self.update_status("Legend Mode: Holding 'A' to find 'Create Match'...")
        
        def check_create_match():
            return find_image_on_screen(get_button_path("buttons/creatematch.png"), confidence=0.65, region=self.roblox_region)
        
        create_match_pos = hold_key_until_condition('a', check_create_match, timeout=30, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not create_match_pos:
            self.update_status("Legend Mode: ✗ Could not find 'Create Match'")
            return False
        
        self.update_status("Legend Mode: ✓ Found 'Create Match', hovering and clicking...")
        move_to(*create_match_pos, duration=0.3)
        time.sleep(0.2)
        click(*create_match_pos)
        time.sleep(0.5)
        
        # Click Legend.png button
        self.update_status("Legend Mode: Searching for 'Legend' button...")
        legend_pos = wait_for_image(get_button_path("buttons/Legend.png"), timeout=10, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if legend_pos:
            self.update_status(f"Legend Mode: ✓ Found 'Legend' at {legend_pos}, clicking...")
            move_to(*legend_pos, duration=0.3)
            time.sleep(0.2)
            click(*legend_pos)
            time.sleep(0.5)
        else:
            self.update_status("Legend Mode: ✗ Could not find 'Legend' button")
            return False
        
        # Find and click the stage/location button
        self.update_status(f"Legend Mode: Step 1 - Searching for stage '{location}'...")
        
        # Map location names to stage image files (same as Story)
        location_lower = location.lower()
        stage_image = None
        act_image = None
        
        if "hollow" in location_lower or "dark" in location_lower:
            stage_image = get_button_path("buttons/hollow.png")
            # Use unified act images named act1..act6.png
            act_image = get_button_path(f"buttons/Acts/act{act}.png")
        elif "namak" in location_lower or "namek" in location_lower or "planet" in location_lower:
            stage_image = get_button_path("buttons/planet.png")
            act_image = get_button_path(f"buttons/Acts/act{act}.png")
        
        if not stage_image:
            self.update_status(f"Legend Mode: ⚠ No image mapping for '{location}', skipping...")
            return False
        
        # Click stage button - use lower confidence for hollow.png
        stage_confidence = 0.50 if "hollow" in stage_image else 0.65
        self.update_status(f"Legend Mode: Looking for stage image: {stage_image}")
        stage_pos = wait_for_image(stage_image, timeout=None, confidence=stage_confidence, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if stage_pos:
            self.update_status(f"Legend Mode: ✓ Found stage '{location}', hovering and clicking...")
            move_to(*stage_pos, duration=0.3)
            time.sleep(0.2)
            click(*stage_pos)
            time.sleep(0.5)
        else:
            self.update_status(f"Legend Mode: ✗ Could not find stage '{location}'")
            return False
        
        # Click act button
        self.update_status(f"Legend Mode: Step 2 - Searching for Act {act}...")
        self.update_status(f"Legend Mode: Looking for act image: {act_image}")
        act_pos = wait_for_image(act_image, timeout=None, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if act_pos:
            self.update_status(f"Legend Mode: ✓ Found Act {act}, hovering and clicking...")
            move_to(*act_pos, duration=0.3)
            time.sleep(0.2)
            click(*act_pos)
            time.sleep(0.5)
        else:
            self.update_status(f"Legend Mode: ✗ Could not find Act {act}")
            return False
        
        # Check if nightmare mode is enabled
        nightmare_enabled = self.config.get("nightmare", False)
        if nightmare_enabled:
            self.update_status("Legend Mode: Step 2.5 - Nightmare mode enabled, searching for Nightmare button...")
            nightmare_pos = wait_for_image(get_button_path("buttons/nightmare.png"), timeout=10, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
            
            if not self._check_running():
                return False
            
            if nightmare_pos:
                self.update_status(f"Legend Mode: ✓ Found Nightmare, clicking...")
                move_to(*nightmare_pos, duration=0.3)
                time.sleep(0.2)
                click(*nightmare_pos)
                time.sleep(0.5)
            else:
                self.update_status("Legend Mode: ⚠ Could not find Nightmare button, continuing without...")
        
        # Click Start
        self.update_status("Legend Mode: Step 3 - Searching for 'Start' button...")
        start_pos = wait_for_image(get_button_path("buttons/Start.png"), timeout=None, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if start_pos:
            self.update_status(f"Legend Mode: ✓ Found 'Start', hovering and clicking...")
            move_to(*start_pos, duration=0.3)
            time.sleep(0.2)
            click(*start_pos)
            time.sleep(0.5)
        else:
            self.update_status("Legend Mode: ✗ Could not find 'Start' button")
            return False
        
        # Click other start button
        self.update_status("Legend Mode: Step 4 - Searching for other start button...")
        otherstart_pos = wait_for_image(get_button_path("buttons/otherstart.png"), timeout=None, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if otherstart_pos:
            self.update_status("Legend Mode: ✓ Found other start button, hovering and clicking...")
            move_to(*otherstart_pos, duration=0.4)
            time.sleep(0.3)
            click(*otherstart_pos)
            time.sleep(0.5)
        else:
            self.update_status("Legend Mode: ✗ Could not find other start button")
            return False
        
        self.update_status("Legend Mode: Navigation complete!")
        return True
    
    def _find_all_visible_image_matches(self, template_path, confidence=0.65):
        """Find all visible matches of a template, excluding each in a 60x63 box.
        Returns list of screen-coordinate positions [(x, y), ...]"""
        from PIL import ImageGrab
        import cv2
        import numpy as np
        
        template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
        if template is None:
            self.update_status(f"Image Scan: Could not load template: {template_path}")
            return []
        if len(template.shape) == 3 and template.shape[2] == 4:
            template = template[:, :, :3]
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        th, tw = template_gray.shape[:2]
        
        # Capture screen once
        if self.roblox_region:
            screenshot = ImageGrab.grab(bbox=self.roblox_region)
            offset_x, offset_y = self.roblox_region[0], self.roblox_region[1]
        else:
            screenshot = ImageGrab.grab()
            offset_x, offset_y = 0, 0
        
        screen_array = np.array(screenshot)
        screen_gray = cv2.cvtColor(screen_array, cv2.COLOR_RGB2GRAY).copy()
        
        found_positions = []
        
        for _ in range(50):  # Safety limit
            if not self.running:
                break
            
            result = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            
            if max_val >= confidence:
                center_x = max_loc[0] + tw // 2
                center_y = max_loc[1] + th // 2
                
                screen_cx = center_x + offset_x
                screen_cy = center_y + offset_y
                
                found_positions.append((screen_cx, screen_cy))
                
                # Hover to it
                move_to(screen_cx, screen_cy, duration=0.15)
                time.sleep(0.05)
                
                # Zero out 60x63 exclusion box in screenshot (centered on match)
                ex1 = max(0, center_x - 30)
                ey1 = max(0, center_y - 31)
                ex2 = min(screen_gray.shape[1], center_x + 30)
                ey2 = min(screen_gray.shape[0], center_y + 32)
                screen_gray[ey1:ey2, ex1:ex2] = 0
            else:
                break
        
        return found_positions
    
    def _find_portals_with_tier_detection(self, portal_types, confidence=0.70):
        """Find all portal positions and determine tier by checking which tier image matches best.
        portal_types: list of (image_path_relative, tier_priority)
        Returns list of (x, y, best_tier) tuples."""
        from PIL import ImageGrab
        import cv2
        import numpy as np
        
        # Capture screen once
        if self.roblox_region:
            screenshot = ImageGrab.grab(bbox=self.roblox_region)
            offset_x, offset_y = self.roblox_region[0], self.roblox_region[1]
        else:
            screenshot = ImageGrab.grab()
            offset_x, offset_y = 0, 0
        
        screen_array = np.array(screenshot)
        screen_gray = cv2.cvtColor(screen_array, cv2.COLOR_RGB2GRAY)
        
        # For each tier image, find all match positions and their confidence values
        # Store as (screen_x, screen_y, tier_priority, match_confidence)
        raw_matches = []
        
        for portal_image, tier_priority in portal_types:
            template = cv2.imread(get_button_path(portal_image), cv2.IMREAD_UNCHANGED)
            if template is None:
                continue
            if len(template.shape) == 3 and template.shape[2] == 4:
                template = template[:, :, :3]
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            th, tw = template_gray.shape[:2]
            
            if tw >= screen_gray.shape[1] or th >= screen_gray.shape[0]:
                continue
            
            # Work on a copy so we can zero out found matches per tier
            screen_copy = screen_gray.copy()
            
            for _ in range(20):  # Safety limit per tier
                result = cv2.matchTemplate(screen_copy, template_gray, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                
                if max_val >= confidence:
                    center_x = max_loc[0] + tw // 2
                    center_y = max_loc[1] + th // 2
                    screen_cx = center_x + offset_x
                    screen_cy = center_y + offset_y
                    
                    raw_matches.append((screen_cx, screen_cy, tier_priority, max_val))
                    
                    # Zero out this area so we find the next match
                    ex1 = max(0, center_x - 30)
                    ey1 = max(0, center_y - 31)
                    ex2 = min(screen_copy.shape[1], center_x + 30)
                    ey2 = min(screen_copy.shape[0], center_y + 32)
                    screen_copy[ey1:ey2, ex1:ex2] = 0
                else:
                    break
        
        if not raw_matches:
            return []
        
        # Group nearby matches (within 50px) and pick the highest confidence tier for each group
        grouped_portals = []  # list of (x, y, best_tier)
        used = [False] * len(raw_matches)
        
        for i, (x1, y1, tier1, conf1) in enumerate(raw_matches):
            if used[i]:
                continue
            
            # Find all matches near this position
            group = [(x1, y1, tier1, conf1)]
            used[i] = True
            
            for j, (x2, y2, tier2, conf2) in enumerate(raw_matches):
                if used[j]:
                    continue
                distance = ((x1 - x2)**2 + (y1 - y2)**2)**0.5
                if distance < 50:
                    group.append((x2, y2, tier2, conf2))
                    used[j] = True
            
            # Pick the tier with the highest confidence from this group
            best = max(group, key=lambda g: g[3])
            grouped_portals.append((best[0], best[1], best[2]))
            
            tier_names = {5: "High Tier", 4: "Red", 3: "Gold", 2: "Purple", 1: "JJK"}
            self.update_status(f"Portal Scan: Detected {tier_names.get(best[2], best[2])} portal (conf={best[3]:.2f})")
        
        return grouped_portals
    
    def _find_furthest_destination_with_scroll(self, template_path, confidence=0.65):
        """Scan for destination images with scrolling. Scroll after every 2 found.
        Returns the furthest (down + right) position from the last visible batch."""
        last_positions = []
        rounds_without_matches = 0
        
        for _ in range(20):  # Safety limit on scroll rounds
            if not self.running:
                break
            
            matches = self._find_all_visible_image_matches(template_path, confidence)
            
            if matches:
                rounds_without_matches = 0
                last_positions = matches
                self.update_status(f"Portal Mode: Found {len(matches)} destinations in view")
                
                # Scroll down to check for more
                if self.roblox_region:
                    cx = (self.roblox_region[0] + self.roblox_region[2]) // 2
                    cy = (self.roblox_region[1] + self.roblox_region[3]) // 2
                    move_to(cx, cy, duration=0.2)
                self.update_status("Portal Mode: Scrolling down slowly...")
                scroll_down(clicks=3, delay=0.5)
                time.sleep(0.5)
            else:
                rounds_without_matches += 1
                if rounds_without_matches >= 2:
                    break  # Two empty rounds in a row = done
                if last_positions:
                    # Had matches before, try one more scroll
                    if self.roblox_region:
                        cx = (self.roblox_region[0] + self.roblox_region[2]) // 2
                        cy = (self.roblox_region[1] + self.roblox_region[3]) // 2
                        move_to(cx, cy, duration=0.2)
                    scroll_down(clicks=3, delay=0.5)
                    time.sleep(0.5)
                else:
                    time.sleep(0.5)  # Wait for UI to load
        
        if not last_positions:
            return None
        
        furthest = max(last_positions, key=lambda p: (p[1], p[0]))
        self.update_status(f"Portal Mode: Selected furthest destination at {furthest}")
        return furthest
    
    def _navigate_portal_mode(self):
        """Navigate Portal mode - Items -> find all portal types -> select highest tier furthest portal -> use -> start"""
        self.update_status(f"Portal Mode: Scanning for all portal types...")
        
        # Define all portal types with tier priority (hightier is highest)
        portal_types = [
            ("buttons/hightier.png", 5),  # Highest tier
            ("buttons/redportal.png", 4),
            ("buttons/goldportal.png", 3),
            ("buttons/purpleportal.png", 2),
            ("buttons/JJKportal.png", 1)  # Lowest tier
        ]
        
        # Step 1: Find and click "Items" button
        self.update_status("Portal Mode: Step 1 - Searching for 'Items' button...")
        items_pos = wait_for_image(get_button_path("buttons/Items.png"), timeout=30, confidence=0.65,
                                   region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not items_pos:
            self.update_status("Portal Mode: ✗ Could not find 'Items' button")
            return False
        
        self.update_status(f"Portal Mode: ✓ Found 'Items', clicking...")
        move_to(*items_pos, duration=0.3)
        time.sleep(0.2)
        click(*items_pos)
        time.sleep(1.0)
        
        # Step 1.5: Click search bar and type portal name to filter
        if self.roblox_region:
            search_x = self.roblox_region[0] + 251
            search_y = self.roblox_region[1] + 149
            self.update_status(f"Portal Mode: Clicking search at ({search_x}, {search_y}) and typing 'JJK'...")
            move_to(search_x, search_y, duration=0.3)
            time.sleep(0.2)
            click(search_x, search_y)
            time.sleep(0.3)
            keyboard.write("JJK", delay=0.1)
            time.sleep(0.5)
        
        # Step 2: Scan for all portal types and select highest tier furthest portal
        self.update_status("Portal Mode: Step 2 - Scanning for all portal types (up to 20 seconds)...")
        all_portal_matches = []  # Store (x, y, tier_priority)
        start_time = time.time()
        attempt = 0
        
        while not all_portal_matches and (time.time() - start_time) < 20:
            if not self._check_running():
                return False
            
            attempt += 1
            self.update_status(f"Portal Mode: Attempt {attempt} to find portal items...")
            
            # Use tier detection - checks all tier images and picks best match per portal
            detected = self._find_portals_with_tier_detection(portal_types, confidence=0.60)
            for px, py, best_tier in detected:
                all_portal_matches.append((px, py, best_tier))
            
            if not all_portal_matches:
                time.sleep(1.0)
        
        if not self._check_running():
            return False
        
        if not all_portal_matches:
            self.update_status("Portal Mode: ✗ Could not find any portal items after 20 seconds")
            return False
        
        # Select highest tier portal, then furthest down and to the right
        highest_tier = max(all_portal_matches, key=lambda p: p[2])[2]
        highest_tier_portals = [p for p in all_portal_matches if p[2] == highest_tier]
        matches = highest_tier_portals  # Use for the retry loop compatibility
        
        # Retry loop for portal selection + Use button + otherstart button
        portal_retry_count = 0
        while self._check_running():
            portal_retry_count += 1
            if portal_retry_count > 1:
                self.update_status(f"Portal Mode: Retry attempt {portal_retry_count - 1} - Re-scanning for portals...")
                time.sleep(2)
                # Re-scan for all portal types
                start_time = time.time()
                attempt = 0
                all_portal_matches = []
                
                while not all_portal_matches and (time.time() - start_time) < 20:
                    if not self._check_running():
                        return False
                    
                    attempt += 1
                    self.update_status(f"Portal Mode: Retry attempt {attempt} to find portal items...")
                    
                    # Use tier detection - checks all tier images and picks best match per portal
                    detected = self._find_portals_with_tier_detection(portal_types, confidence=0.60)
                    for px, py, best_tier in detected:
                        all_portal_matches.append((px, py, best_tier))
                    
                    if not all_portal_matches:
                        time.sleep(1.0)
                
                if not all_portal_matches:
                    self.update_status("Portal Mode: ✗ Could not re-find portal items, retrying...")
                    continue
                
                # Select highest tier
                highest_tier = max(all_portal_matches, key=lambda p: p[2])[2]
                highest_tier_portals = [p for p in all_portal_matches if p[2] == highest_tier]
                matches = highest_tier_portals
            
            # matches now contains (x, y, tier_priority)
            furthest_portal = max(matches, key=lambda p: (p[1], p[0]))
            tier_names = {5: "High Tier", 4: "Red", 3: "Gold", 2: "Purple", 1: "JJK"}
            self.update_status(f"Portal Mode: ✓ Found {len(matches)} tier {tier_names.get(furthest_portal[2], furthest_portal[2])} portals, selecting furthest at ({furthest_portal[0]}, {furthest_portal[1]})")
            move_to(furthest_portal[0], furthest_portal[1], duration=0.3)
            time.sleep(0.2)
            click(furthest_portal[0], furthest_portal[1])
            time.sleep(0.5)
            
            # Step 3: Find and click "use" button
            self.update_status("Portal Mode: Step 3 - Searching for 'Use' button...")
            use_pos = wait_for_image(get_button_path("buttons/use.png"), timeout=15, confidence=0.65,
                                     region=self.roblox_region, running_check=lambda: self.running)
            
            if not self._check_running():
                return False
            
            if not use_pos:
                # Recheck with higher confidence
                self.update_status("Portal Mode: Rechecking 'Use' button with higher confidence (0.7)...")
                use_pos = wait_for_image(get_button_path("buttons/use.png"), timeout=5, confidence=0.7,
                                         region=self.roblox_region, running_check=lambda: self.running)
                
                if not self._check_running():
                    return False
                
                if not use_pos:
                    self.update_status("Portal Mode: ⚠ Could not find 'Use' button, retrying portal selection...")
                    continue  # Retry from portal selection
            
            self.update_status("Portal Mode: ✓ Found 'Use' button, clicking...")
            move_to(*use_pos, duration=0.3)
            time.sleep(0.2)
            click(*use_pos)
            time.sleep(1.0)
            
            # Step 4: Wait for and click otherstart.png to start the portal
            self.update_status("Portal Mode: Step 4 - Waiting for 'otherstart' button to start portal...")
            otherstart_pos = wait_for_image(get_button_path("buttons/otherstart.png"), timeout=30, confidence=0.65,
                                            region=self.roblox_region, running_check=lambda: self.running)
            
            if not self._check_running():
                return False
            
            if not otherstart_pos:
                self.update_status("Portal Mode: ⚠ Could not find 'otherstart' button, retrying portal selection...")
                continue  # Retry from portal selection
            
            self.update_status("Portal Mode: ✓ Found 'otherstart' button, clicking to start portal...")
            move_to(*otherstart_pos, duration=0.3)
            time.sleep(0.2)
            click(*otherstart_pos)
            time.sleep(1.0)
            
            self.update_status("Portal Mode: ✓ Navigation complete, portal starting!")
            return True
        
        return False
    
    def _navigate_story_mode(self):
        """Navigate Story mode menus"""
        location = self.config.get("location", "Leaf Village")
        act = self.config.get("act", "Act 1").replace(" ", "").replace("Act", "")
        self.update_status(f"Story Mode: Location = {location}, Act = {act}")
        
        # Hold A until "Create Match" appears
        self.update_status("Story Mode: Holding 'A' to find 'Create Match'...")
        
        def check_create_match():
            return find_image_on_screen(get_button_path("buttons/creatematch.png"), confidence=0.65, region=self.roblox_region)
        
        create_match_pos = hold_key_until_condition('a', check_create_match, timeout=30, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if not create_match_pos:
            self.update_status("Story Mode: ✗ Could not find 'Create Match'")
            return False
        
        self.update_status("Story Mode: ✓ Found 'Create Match', hovering and clicking...")
        move_to(*create_match_pos, duration=0.3)
        time.sleep(0.2)
        click(*create_match_pos)
        time.sleep(0.5)
        
        # Find and click the stage/location button
        self.update_status(f"Story Mode: Step 1 - Searching for stage '{location}'...")
        
        # Map location names to stage image files
        location_lower = location.lower()
        stage_image = None
        act_image = None
        
        if "hollow" in location_lower or "dark" in location_lower:
            stage_image = get_button_path("buttons/hollow.png")
            act_image = get_button_path(f"buttons/Acts/act{act}.png")
        elif "leaf" in location_lower or "village" in location_lower:
            stage_image = get_button_path("buttons/leaf.png")
            act_image = get_button_path(f"buttons/Acts/act{act}.png")
        elif "namak" in location_lower or "namek" in location_lower or "planet" in location_lower:
            stage_image = get_button_path("buttons/planet.png")
            act_image = get_button_path(f"buttons/Acts/act{act}.png")
        elif "shibuya" in location_lower:
            stage_image = get_button_path("buttons/shibuya.png")
            act_image = get_button_path(f"buttons/Acts/act{act}.png")
        
        if not stage_image:
            self.update_status(f"Story Mode: ⚠ No image mapping for '{location}', skipping...")
            return False
        
        # Click stage button - use lower confidence for hollow.png
        stage_confidence = 0.50 if "hollow" in stage_image else 0.65
        self.update_status(f"Story Mode: Looking for stage image: {stage_image}")
        stage_pos = wait_for_image(stage_image, timeout=None, confidence=stage_confidence, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if stage_pos:
            self.update_status(f"Story Mode: ✓ Found stage '{location}', hovering and clicking...")
            move_to(*stage_pos, duration=0.3)
            time.sleep(0.2)
            click(*stage_pos)
            time.sleep(0.5)
        else:
            self.update_status(f"Story Mode: ✗ Could not find stage '{location}'")
            return False
        
        # Click act button
        self.update_status(f"Story Mode: Step 2 - Searching for Act {act}...")
        self.update_status(f"Story Mode: Looking for act image: {act_image}")
        act_pos = wait_for_image(act_image, timeout=None, confidence=0.55, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if act_pos:
            self.update_status(f"Story Mode: ✓ Found Act {act}, hovering and clicking...")
            move_to(*act_pos, duration=0.3)
            time.sleep(0.2)
            click(*act_pos)
            time.sleep(0.5)
        else:
            self.update_status(f"Story Mode: ✗ Could not find Act {act}")
            return False
        
        # Check if nightmare mode is enabled
        nightmare_enabled = self.config.get("nightmare", False)
        if nightmare_enabled:
            self.update_status("Story Mode: Step 2.5 - Nightmare mode enabled, searching for Nightmare button...")
            nightmare_pos = wait_for_image(get_button_path("buttons/nightmare.png"), timeout=10, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
            
            if not self._check_running():
                return False
            
            if nightmare_pos:
                self.update_status("Story Mode: ✓ Found Nightmare button, hovering and clicking...")
                move_to(*nightmare_pos, duration=0.3)
                time.sleep(0.2)
                click(*nightmare_pos)
                time.sleep(0.5)
            else:
                self.update_status("Story Mode: ✗ Could not find Nightmare button, continuing without it...")
        
        # Click Start button
        self.update_status("Story Mode: Step 3 - Searching for Start button...")
        self.update_status("Story Mode: Looking for start image: buttons/Start.png")
        start_pos = wait_for_image(get_button_path("buttons/Start.png"), timeout=None, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if start_pos:
            self.update_status("Story Mode: ✓ Found Start button, hovering and clicking...")
            move_to(*start_pos, duration=0.3)
            time.sleep(0.2)
            click(*start_pos)
            time.sleep(0.5)
        else:
            self.update_status("Story Mode: ✗ Could not find Start button")
            return False
        
        # Click other start button
        self.update_status("Story Mode: Step 4 - Searching for other start button...")
        self.update_status("Story Mode: Looking for other start image: buttons/otherstart.png")
        otherstart_pos = wait_for_image(get_button_path("buttons/otherstart.png"), timeout=None, confidence=0.65, region=self.roblox_region, running_check=lambda: self.running)
        
        if not self._check_running():
            return False
        
        if otherstart_pos:
            self.update_status("Story Mode: ✓ Found other start button, hovering and clicking...")
            move_to(*otherstart_pos, duration=0.4)
            time.sleep(0.3)
            click(*otherstart_pos)
            time.sleep(0.5)
        else:
            self.update_status("Story Mode: ✗ Could not find other start button")
            return False
        
        return True

