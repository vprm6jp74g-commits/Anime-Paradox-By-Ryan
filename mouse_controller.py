"""Mouse and keyboard control utilities"""
import pydirectinput
import time
import random
import keyboard
import cv2
import numpy as np
from PIL import ImageGrab
import ctypes
from ctypes import windll, Structure, c_long, byref, c_ulong, c_ushort, c_short, POINTER, Union, sizeof
import pyautogui  # For scroll functionality

# Windows API constants
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

# Input type constants
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

# DirectInput scan codes for WASD keys
SCAN_CODES = {
    'a': 0x1E,  # A key
    'w': 0x11,  # W key
    's': 0x1F,  # S key
    'd': 0x20,  # D key
    'o': 0x18,  # O key
}

# Structures for SendInput
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", c_ushort),
        ("wScan", c_ushort),
        ("dwFlags", c_ulong),
        ("time", c_ulong),
        ("dwExtraInfo", ctypes.POINTER(c_ulong))
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", c_ulong),
        ("wParamL", c_short),
        ("wParamH", c_ushort)
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", c_long),
        ("dy", c_long),
        ("mouseData", c_ulong),
        ("dwFlags", c_ulong),
        ("time", c_ulong),
        ("dwExtraInfo", ctypes.POINTER(c_ulong))
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT)
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", c_ulong),
        ("union", INPUT_UNION)
    ]

def send_key_down(key):
    """Send key down using SendInput with DirectInput scan codes"""
    scan_code = SCAN_CODES.get(key.lower(), 0)
    if scan_code == 0:
        print(f"Unknown key: {key}")
        return
    
    extra = ctypes.pointer(c_ulong(0))
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = 0
    inp.union.ki.wScan = scan_code
    inp.union.ki.dwFlags = KEYEVENTF_SCANCODE
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = extra
    
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

def send_key_up(key):
    """Send key up using SendInput with DirectInput scan codes"""
    scan_code = SCAN_CODES.get(key.lower(), 0)
    if scan_code == 0:
        print(f"Unknown key: {key}")
        return
    
    extra = ctypes.pointer(c_ulong(0))
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = 0
    inp.union.ki.wScan = scan_code
    inp.union.ki.dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = extra
    
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

def hold_key_directinput(key, duration):
    """Hold a key for a duration using DirectInput scan codes via SendInput"""
    send_key_down(key)
    time.sleep(duration)
    send_key_up(key)

class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]

def get_cursor_pos():
    """Get current cursor position using Windows API"""
    pt = POINT()
    windll.user32.GetCursorPos(byref(pt))
    return pt.x, pt.y

def win32_move_to(x, y):
    """Move mouse using Windows API for Roblox compatibility"""
    # Convert screen coordinates to absolute coordinates (0-65535 range)
    screen_width = windll.user32.GetSystemMetrics(0)
    screen_height = windll.user32.GetSystemMetrics(1)
    
    abs_x = int(x * 65535 / screen_width)
    abs_y = int(y * 65535 / screen_height)
    
    windll.user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y, 0, 0)

def win32_click(x, y):
    """Click at position using SendInput for Roblox compatibility"""
    # Move to position first
    win32_move_to(x, y)
    time.sleep(0.03)
    
    # Get screen dimensions for absolute coordinates
    screen_width = windll.user32.GetSystemMetrics(0)
    screen_height = windll.user32.GetSystemMetrics(1)
    abs_x = int(x * 65535 / screen_width)
    abs_y = int(y * 65535 / screen_height)
    
    extra = ctypes.pointer(c_ulong(0))
    
    # Mouse down
    inp_down = INPUT()
    inp_down.type = INPUT_MOUSE
    inp_down.union.mi.dx = abs_x
    inp_down.union.mi.dy = abs_y
    inp_down.union.mi.mouseData = 0
    inp_down.union.mi.dwFlags = MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE
    inp_down.union.mi.time = 0
    inp_down.union.mi.dwExtraInfo = extra
    
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(inp_down))
    time.sleep(0.02)
    
    # Mouse up
    inp_up = INPUT()
    inp_up.type = INPUT_MOUSE
    inp_up.union.mi.dx = abs_x
    inp_up.union.mi.dy = abs_y
    inp_up.union.mi.mouseData = 0
    inp_up.union.mi.dwFlags = MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE
    inp_up.union.mi.time = 0
    inp_up.union.mi.dwExtraInfo = extra
    
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(inp_up))

def smooth_move_to(x, y, duration=0.3, steps=20):
    """Smoothly move mouse to position for Roblox button hover detection"""
    start_x, start_y = get_cursor_pos()
    
    for i in range(steps + 1):
        progress = i / steps
        # Ease-in-out for smooth movement
        progress = progress * progress * (3 - 2 * progress)
        
        current_x = int(start_x + (x - start_x) * progress)
        current_y = int(start_y + (y - start_y) * progress)
        
        win32_move_to(current_x, current_y)
        time.sleep(duration / steps)

# Safety settings
pydirectinput.FAILSAFE = True
pydirectinput.PAUSE = 0.05

def click(x, y, delay=0.1):
    """Click at specified coordinates using Windows API for Roblox"""
    print(f"Clicking at coordinates: ({x}, {y})")
    win32_click(x, y)
    time.sleep(delay)
    print(f"Click completed at ({x}, {y})")

def double_click(x, y, delay=0.1):
    """Double click at specified coordinates"""
    print(f"[INPUT] Double-clicking at ({x}, {y})")
    pydirectinput.click(x, y)
    time.sleep(0.1)
    pydirectinput.click(x, y)
    time.sleep(delay)

def right_click(x, y, delay=0.1):
    """Right click at specified coordinates"""
    print(f"[INPUT] Right-clicking at ({x}, {y})")
    pydirectinput.rightClick(x, y)
    time.sleep(delay)

def move_to(x, y, duration=0.2):
    """Move mouse to specified coordinates using smooth Windows API movement"""
    if duration <= 0:
        win32_move_to(x, y)
    else:
        steps = max(10, int(duration * 50))  # More steps for smoother movement
        smooth_move_to(x, y, duration=duration, steps=steps)

def drag_down(start_x, start_y, distance=200, duration=0.5):
    """Hold right click and drag downwards using smooth movement"""
    # Move to start position first
    smooth_move_to(start_x, start_y, duration=0.2, steps=15)
    time.sleep(0.1)
    
    # Hold right click
    pydirectinput.mouseDown(button='right')
    time.sleep(0.15)
    
    # Smoothly drag down
    steps = max(20, int(duration * 40))
    for i in range(steps + 1):
        progress = i / steps
        current_y = int(start_y + distance * progress)
        win32_move_to(start_x, current_y)
        time.sleep(duration / steps)
    
    time.sleep(0.1)
    pydirectinput.mouseUp(button='right')

def scroll_down(clicks=3, delay=0.3):
    """Scroll the mouse wheel down"""
    for _ in range(clicks):
        pyautogui.scroll(-120)  # Negative values scroll down
        time.sleep(0.1)
    time.sleep(delay)

def scroll_up(clicks=3, delay=0.3):
    """Scroll the mouse wheel up"""
    for _ in range(clicks):
        pyautogui.scroll(120)  # Positive values scroll up
        time.sleep(0.1)
    time.sleep(delay)

def hold_key(key, duration=1.0):
    """Hold a key for specified duration"""
    print(f"[INPUT] Holding '{key.upper()}' key for {duration}s...")
    keyboard.press(key)
    time.sleep(duration)
    keyboard.release(key)
    print(f"[INPUT] Released '{key.upper()}' key after {duration}s")

def press_key(key):
    """Press and release a key"""
    print(f"[INPUT] Pressing '{key.upper()}' key")
    keyboard.press_and_release(key)


def win32_press_key(key):
    """Press a key using pydirectinput for Roblox/DirectX game compatibility"""
    try:
        # pydirectinput uses DirectInput which games like Roblox can detect
        pydirectinput.press(key.lower())
        time.sleep(0.02)
    except Exception as e:
        print(f"win32_press_key error: {e}")
        # Fallback to keyboard library
        keyboard.press_and_release(key)

def spam_key(key, times=5, delay=0.1):
    """Press a key multiple times rapidly"""
    print(f"[INPUT] Spamming '{key.upper()}' key x{times} with {delay}s delay")
    for i in range(times):
        keyboard.press_and_release(key)
        time.sleep(delay)
    print(f"[INPUT] Finished spamming '{key.upper()}' key")

def hold_key_until_condition(key, condition_func, timeout=30, check_interval=0.2, running_check=None):
    """
    Hold a key until a condition is met
    condition_func should return True when condition is met
    running_check should return False when macro should stop
    """
    print(f"[INPUT] Holding '{key.upper()}' key until condition met (timeout: {timeout}s)...")
    keyboard.press(key)
    start_time = time.time()
    result = None
    
    try:
        while time.time() - start_time < timeout:
            # Check if macro should stop
            if running_check and not running_check():
                print(f"[INPUT] Hold interrupted - macro stopped after {time.time() - start_time:.2f}s")
                break
            
            result = condition_func()
            if result:
                print(f"[INPUT] Condition met - releasing '{key.upper()}' key after {time.time() - start_time:.2f}s")
                break
            time.sleep(check_interval)
    finally:
        elapsed = time.time() - start_time
        keyboard.release(key)
        if not result and elapsed >= timeout:
            print(f"[INPUT] Hold timeout reached - released '{key.upper()}' key after {elapsed:.2f}s")
    
    return result

def random_offset(x, y, max_offset=5):
    """Add random offset to coordinates for more human-like behavior"""
    offset_x = random.randint(-max_offset, max_offset)
    offset_y = random.randint(-max_offset, max_offset)
    return (x + offset_x, y + offset_y)

def get_screen_size():
    """Get screen dimensions"""
    return pydirectinput.size()

def get_mouse_position():
    """Get current mouse position"""
    return pydirectinput.position()

class SpiralPattern:
    """Generate coordinates in a spiral pattern around a center point"""
    
    def __init__(self, center_x, center_y, area_width, area_height, step=30):
        self.center_x = center_x
        self.center_y = center_y
        self.area_width = area_width
        self.area_height = area_height
        self.step = step
        self.current_index = 0
        self.coordinates = self._generate_spiral()
    
    def _generate_spiral(self):
        """Generate spiral coordinates within the area"""
        coords = [(self.center_x, self.center_y)]
        
        x, y = self.center_x, self.center_y
        dx, dy = self.step, 0
        steps_in_direction = 1
        steps_taken = 0
        direction_changes = 0
        
        max_coords = 100  # Limit number of coordinates
        
        while len(coords) < max_coords:
            x += dx
            y += dy
            steps_taken += 1
            
            # Check if within bounds
            half_width = self.area_width // 2
            half_height = self.area_height // 2
            
            if (abs(x - self.center_x) <= half_width and 
                abs(y - self.center_y) <= half_height):
                coords.append((x, y))
            
            if steps_taken >= steps_in_direction:
                steps_taken = 0
                direction_changes += 1
                
                # Rotate direction 90 degrees
                dx, dy = -dy, dx
                
                # Increase steps every 2 direction changes
                if direction_changes % 2 == 0:
                    steps_in_direction += 1
                
                # Stop if we've gone too far
                if steps_in_direction > max(self.area_width, self.area_height) // self.step:
                    break
        
        return coords
    
    def get_next(self):
        """Get the next coordinate in the spiral"""
        if self.current_index >= len(self.coordinates):
            return None
        
        coord = self.coordinates[self.current_index]
        self.current_index += 1
        return coord
    
    def reset(self):
        """Reset to the beginning of the spiral"""
        self.current_index = 0
    
    def get_random_in_area(self):
        """Get a random coordinate within the placement area"""
        half_width = self.area_width // 2
        half_height = self.area_height // 2
        
        x = random.randint(self.center_x - half_width, self.center_x + half_width)
        y = random.randint(self.center_y - half_height, self.center_y + half_height)
        return (x, y)

def find_image_on_screen(template_path, confidence=0.65, region=None, grayscale=True):
    """
    Find an image on screen using template matching
    Supports transparency in template images (alpha channel used as mask)
    Returns: (x, y) center coordinates if found, None otherwise
    """
    try:
        # Capture screen
        if region:
            screenshot = ImageGrab.grab(bbox=region)
        else:
            screenshot = ImageGrab.grab()
        
        # Convert to numpy array
        screen_array = np.array(screenshot)
        
        # Load template (strip alpha channel if present, don't use as mask)
        template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
        if template is None:
            print(f"[ERROR] Could not load template image: {template_path}")
            return None
        
        # Check if template has alpha channel and remove it
        if len(template.shape) == 3 and template.shape[2] == 4:
            # Strip alpha channel
            template = template[:, :, :3]
        
        # Convert to grayscale if specified
        if grayscale:
            screen_gray = cv2.cvtColor(screen_array, cv2.COLOR_RGB2GRAY)
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            screen_to_match = screen_gray
            template_to_match = template_gray
        else:
            screen_to_match = cv2.cvtColor(screen_array, cv2.COLOR_RGB2BGR)
            template_to_match = template
        
        # Perform template matching
        result = cv2.matchTemplate(screen_to_match, template_to_match, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        # Check if match confidence is high enough
        if max_val >= confidence:
            # Calculate center of match
            h, w = template_to_match.shape[:2]
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2

            # Adjust for region offset
            if region:
                center_x += region[0]
                center_y += region[1]

            return (center_x, center_y)
        else:
            # If we had a region and got a near-miss, try expanding the capture area a bit to allow for slight mis-positioning
            try:
                if region:
                    margin = 40
                    screen_w = windll.user32.GetSystemMetrics(0)
                    screen_h = windll.user32.GetSystemMetrics(1)

                    left = max(0, region[0] - margin)
                    top = max(0, region[1] - margin)
                    right = min(screen_w, region[2] + margin)
                    bottom = min(screen_h, region[3] + margin)
                    expanded = (left, top, right, bottom)

                    try:
                        screenshot2 = ImageGrab.grab(bbox=expanded)
                    except Exception:
                        screenshot2 = ImageGrab.grab()

                    screen_array2 = np.array(screenshot2)
                    if grayscale:
                        screen_to_match2 = cv2.cvtColor(screen_array2, cv2.COLOR_RGB2GRAY)
                    else:
                        screen_to_match2 = cv2.cvtColor(screen_array2, cv2.COLOR_RGB2BGR)

                    result2 = cv2.matchTemplate(screen_to_match2, template_to_match, cv2.TM_CCOEFF_NORMED)
                    _, max_val2, _, max_loc2 = cv2.minMaxLoc(result2)
                    if max_val2 >= confidence:
                        h2, w2 = template_to_match.shape[:2]
                        center_x2 = max_loc2[0] + w2 // 2 + expanded[0]
                        center_y2 = max_loc2[1] + h2 // 2 + expanded[1]
                        return (center_x2, center_y2)
            except Exception as e:
                print(f"[DEBUG] Expanded search error: {e}")

            return None
    except Exception as e:
        print(f"Error in image detection: {e}")
        return None

def wait_for_image(template_path, timeout=30, check_interval=0.5, confidence=0.65, region=None, running_check=None, warning_callback=None, warning_time=10):
    """
    Wait until image appears on screen
    running_check should return False when macro should stop
    warning_callback: Optional function to call if still searching after warning_time seconds
    warning_time: Seconds to wait before calling warning_callback (default: 10)
    timeout: Maximum seconds to wait, or None for indefinite waiting
    Returns: (x, y) if found within timeout, None otherwise
    """
    import os
    image_name = os.path.basename(template_path)
    print(f"[SEARCH] Looking for image '{image_name}' (confidence: {confidence}, timeout: {timeout}s)...")
    start_time = time.time()
    warning_shown = False
    attempt_count = 0
    while timeout is None or time.time() - start_time < timeout:
        # Check if macro should stop
        if running_check and not running_check():
            print(f"[SEARCH] Search interrupted for '{image_name}' after {time.time() - start_time:.2f}s")
            return None
        
        # Show warning if still searching after warning_time
        if warning_callback and not warning_shown and time.time() - start_time >= warning_time:
            print(f"[SEARCH] Still searching for '{image_name}' after {warning_time}s...")
            warning_callback()
            warning_shown = True
        
        attempt_count += 1
        result = find_image_on_screen(template_path, confidence=confidence, region=region)
        if result:
            elapsed = time.time() - start_time
            print(f"[SEARCH] ✓ Found '{image_name}' at ({result[0]}, {result[1]}) after {elapsed:.2f}s ({attempt_count} attempts)")
            return result
        time.sleep(check_interval)
    
    elapsed = time.time() - start_time
    print(f"[SEARCH] ✗ Timeout: '{image_name}' not found after {elapsed:.2f}s ({attempt_count} attempts)")
    return None
