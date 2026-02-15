"""Placement area editor - standalone script to avoid Tkinter threading issues"""
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
import os
import sys
import json
import ctypes
from ctypes import wintypes

# Windows API
user32 = ctypes.windll.user32
IsWindowVisible = user32.IsWindowVisible
GetWindowLong = user32.GetWindowLongW
GWL_STYLE = -16
WS_VISIBLE = 0x10000000

# Fixed embedded Roblox dimensions (must match main_webview.py)
EMBEDDED_ROBLOX_X = 510  # X offset within parent window
EMBEDDED_ROBLOX_Y = 80   # Y offset within parent window  
EMBEDDED_ROBLOX_WIDTH = 800
EMBEDDED_ROBLOX_HEIGHT = 600


def find_macro_window():
    """Find the Anime Paradox Free by Ryan window and return its position"""
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    GetWindowText = user32.GetWindowTextW
    GetWindowTextLength = user32.GetWindowTextLengthW
    GetWindowRect = user32.GetWindowRect
    
    result = []
    
    def enum_callback(hwnd, lParam):
        if not IsWindowVisible(hwnd):
            return True
            
        length = GetWindowTextLength(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buff, length + 1)
            title = buff.value
            if 'Anime Paradox Free by Ryan' in title:
                rect = wintypes.RECT()
                GetWindowRect(hwnd, ctypes.byref(rect))
                result.append({
                    'hwnd': hwnd,
                    'title': title,
                    'x': rect.left,
                    'y': rect.top,
                    'width': rect.right - rect.left,
                    'height': rect.bottom - rect.top
                })
        return True
    
    EnumWindows(EnumWindowsProc(enum_callback), 0)
    
    if result:
        print(f"Found Macro window: {result[0]['title']} at ({result[0]['x']}, {result[0]['y']})")
        return result[0]
    return None


def find_roblox_window():
    """Find the Roblox window and return its position/size"""
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    GetWindowText = user32.GetWindowTextW
    GetWindowTextLength = user32.GetWindowTextLengthW
    GetWindowRect = user32.GetWindowRect
    GetClassName = user32.GetClassNameW
    
    # Known Roblox window class names
    ROBLOX_CLASSES = {'windowsclient', 'robloxplayerbeta'}
    
    result = []
    
    def enum_callback(hwnd, lParam):
        # Only check visible windows
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
            # Get title for display
            length = GetWindowTextLength(hwnd)
            title = ''
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                GetWindowText(hwnd, buff, length + 1)
                title = buff.value
            
            rect = wintypes.RECT()
            GetWindowRect(hwnd, ctypes.byref(rect))
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            # Filter out tiny windows (toolbars, etc)
            if width > 200 and height > 200:
                result.append({
                    'hwnd': hwnd,
                    'title': title,
                    'x': rect.left,
                    'y': rect.top,
                    'width': width,
                    'height': height
                })
        return True
    
    EnumWindows(EnumWindowsProc(enum_callback), 0)
    
    # Return the largest Roblox window (the game window, not toolbars)
    if result:
        result.sort(key=lambda w: w['width'] * w['height'], reverse=True)
        print(f"Found Roblox window: {result[0]['title']} at ({result[0]['x']}, {result[0]['y']}) size {result[0]['width']}x{result[0]['height']}")
        return result[0]
    return None


def get_location_key(location):
    """Get the folder/config key for a location"""
    location_lower = location.lower()
    if "planet" in location_lower or "namak" in location_lower or "namek" in location_lower:
        return "Planet"
    elif "leaf" in location_lower or "village" in location_lower:
        return "Leaf"
    elif "hollow" in location_lower or "dark" in location_lower:
        return "Dark"
    else:
        return "Leaf"  # Default


class PlacementEditorApp:
    def __init__(self, config_path, mode):
        self.config_path = config_path
        self.mode = mode
        self.config = self._load_config()
        
        print(f"Config path: {self.config_path}")
        print(f"Mode: {self.mode}")
        print(f"Loaded config: {self.config}")
        
        # Try to find the macro window first (for embedded Roblox)
        macro_info = find_macro_window()
        
        if macro_info:
            # Roblox is embedded - position over the embedded area
            # The embedded Roblox is at fixed offset within the macro window
            self.window_x = macro_info['x'] + EMBEDDED_ROBLOX_X
            self.window_y = macro_info['y'] + EMBEDDED_ROBLOX_Y
            self.window_width = EMBEDDED_ROBLOX_WIDTH
            self.window_height = EMBEDDED_ROBLOX_HEIGHT
            print(f"Using embedded Roblox position: ({self.window_x}, {self.window_y}) size {self.window_width}x{self.window_height}")
            self.roblox_info = {
                'x': self.window_x,
                'y': self.window_y,
                'width': self.window_width,
                'height': self.window_height
            }
        else:
            # Fall back to finding standalone Roblox window
            self.roblox_info = find_roblox_window()
            
            if self.roblox_info:
                self.window_width = self.roblox_info['width']
                self.window_height = self.roblox_info['height']
                self.window_x = self.roblox_info['x']
                self.window_y = self.roblox_info['y']
            else:
                print("WARNING: Roblox window not found, using default size")
                self.window_width = 800
                self.window_height = 600
                self.window_x = 100
                self.window_y = 100
        
        # Create main window
        self.root = tk.Tk()
        self.root.title(f"Select Placement Area - {mode} Mode")
        
        # Remove window decorations for exact overlay
        self.root.overrideredirect(True)
        
        # Set exact position and size to match Roblox
        self.root.geometry(f"{self.window_width}x{self.window_height}+{self.window_x}+{self.window_y}")
        
        # Make it topmost
        self.root.attributes('-topmost', True)
        self.root.configure(bg='black')
        
        # Selection state
        self.start_x = None
        self.start_y = None
        self.current_rect = None
        self.selection = None
        
        # Image references - MUST be instance variables to prevent GC
        self._pil_image = None
        self._tk_image = None
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.image_x = 0
        self.image_y = 0
        
        self._setup_ui()
        self._load_image()
        
        # Load existing selection for current location
        location = self.config.get("location", "Leaf Village")
        location_key = get_location_key(location)
        placement_areas = self.config.get("placement_areas", {})
        if placement_areas.get(location_key):
            self._draw_existing_selection()
        
        # Focus the window
        self.root.focus_force()
        self.root.lift()
    
    def _load_config(self):
        """Load config from file"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    print(f"Loaded existing config with keys: {list(data.keys())}")
                    return data
        except Exception as e:
            print(f"Error loading config: {e}")
        return {}
    
    def _save_config(self):
        """Save config to file"""
        try:
            print(f"Saving config to: {self.config_path}")
            print(f"Config data: {json.dumps(self.config, indent=2)}")
            
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2)
            
            # Verify save
            with open(self.config_path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                location = self.config.get("location", "Leaf Village")
                location_key = get_location_key(location)
                saved_areas = saved.get("placement_areas", {})
                config_areas = self.config.get("placement_areas", {})
                if saved_areas.get(location_key) == config_areas.get(location_key):
                    print(f"✓ Config saved and verified successfully for {location_key}!")
                    return True
                else:
                    print("✗ Config verification failed!")
                    return False
        except Exception as e:
            print(f"Error saving config: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _setup_ui(self):
        """Setup the UI"""
        # Instructions bar at top
        self.instructions = tk.Label(
            self.root,
            text="Click and drag to select placement area | S = Save | ESC = Cancel | Drag title bar to move",
            bg='#1a1a2e',
            fg='#00ff00',
            font=('Consolas', 10, 'bold'),
            pady=5,
            cursor='fleur'  # Move cursor
        )
        self.instructions.pack(fill=tk.X)
        
        # Allow dragging the window via the instructions bar
        self.instructions.bind('<Button-1>', self._start_move)
        self.instructions.bind('<B1-Motion>', self._do_move)
        
        # Canvas for image and selection
        self.canvas = tk.Canvas(self.root, bg='#0a0a0a', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Button frame at bottom
        btn_frame = tk.Frame(self.root, bg='#1a1a2e', pady=5)
        btn_frame.pack(fill=tk.X)
        
        save_btn = tk.Button(
            btn_frame,
            text="💾 SAVE",
            command=self._save_and_close,
            bg='#00aa00',
            fg='white',
            font=('Consolas', 11, 'bold'),
            padx=20,
            pady=3
        )
        save_btn.pack(side=tk.LEFT, padx=15)
        
        # Show current selection info
        self.selection_label = tk.Label(
            btn_frame,
            text="No selection",
            bg='#1a1a2e',
            fg='#aaaaaa',
            font=('Consolas', 9)
        )
        self.selection_label.pack(side=tk.LEFT, expand=True)
        
        cancel_btn = tk.Button(
            btn_frame,
            text="❌ CANCEL",
            command=self._cancel,
            bg='#aa0000',
            fg='white',
            font=('Consolas', 11, 'bold'),
            padx=20,
            pady=3
        )
        cancel_btn.pack(side=tk.RIGHT, padx=15)
        
        # Bind events
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.root.bind("<Escape>", lambda e: self._cancel())
        self.root.bind("<s>", lambda e: self._save_and_close())
        self.root.bind("<S>", lambda e: self._save_and_close())
        self.root.bind("<Return>", lambda e: self._save_and_close())
    
    def _start_move(self, event):
        """Start moving the window"""
        self._drag_x = event.x
        self._drag_y = event.y
    
    def _do_move(self, event):
        """Move the window"""
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")
    
    def _load_image(self):
        """Load the mode-specific image"""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        base_folder = os.path.join(script_dir, "starting image")
        
        # Get location from config
        location = self.config.get("location", "Leaf Village")
        
        # Build path based on mode and location
        if self.mode == "Story" or self.mode == "Legend":
            # Map location to folder name
            if "planet" in location.lower() or "namak" in location.lower() or "namek" in location.lower():
                folder_name = "Planet"
            elif "leaf" in location.lower() or "village" in location.lower():
                folder_name = "Leaf"
            elif "hollow" in location.lower() or "dark" in location.lower():
                folder_name = "Dark"
            elif "shibuya" in location.lower():
                folder_name = "Shibuya"
            else:
                folder_name = "Leaf"  # Default
            
            image_folder = os.path.join(base_folder, "Story", folder_name)
        elif self.mode == "Auto-Challenges":
            # Auto-Challenges uses challenge_location which maps to Story folders
            challenge_location = self.config.get("challenge_location", "Leaf Village")
            if "planet" in challenge_location.lower() or "namak" in challenge_location.lower() or "namek" in challenge_location.lower():
                folder_name = "Planet"
            elif "leaf" in challenge_location.lower() or "village" in challenge_location.lower():
                folder_name = "Leaf"
            elif "hollow" in challenge_location.lower() or "dark" in challenge_location.lower():
                folder_name = "Dark"
            elif "shibuya" in challenge_location.lower():
                folder_name = "Shibuya"
            else:
                folder_name = "Leaf"  # Default
            
            image_folder = os.path.join(base_folder, "Story", folder_name)
        elif self.mode == "Portals":
            # Portals mode: starting image/Portals/{portal_selection}/
            portal_selection = self.config.get("portal_selection", "JJK Portal")
            portal_folder = portal_selection.replace(" ", " ")  # Keep spaces in folder name
            image_folder = os.path.join(base_folder, "Portals", portal_folder)
        elif self.mode == "Raid" or self.mode == "Raids":
            # Raid mode: starting image/Raid/{location}/
            image_folder = os.path.join(base_folder, "Raid", location)
        elif self.mode == "Siege":
            # Siege mode: starting image/Siege/{location}/
            image_folder = os.path.join(base_folder, "Siege", location)
        else:
            # For other modes, use base folder
            image_folder = base_folder
        
        print(f"Looking for image in folder: {image_folder}")
        
        # Look for any image in the folder
        image_path = None
        if os.path.exists(image_folder):
            for filename in os.listdir(image_folder):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                    image_path = os.path.join(image_folder, filename)
                    print(f"Found image: {image_path}")
                    break
        
        if not image_path or not os.path.exists(image_path):
            self.root.update()
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            self.canvas.create_text(
                canvas_width // 2, 
                canvas_height // 2,
                text=f"No image found in folder\n\nPlease add an image to:\n{image_folder}\n\nYou can use F4 to take a screenshot\n(after starting the macro)",
                fill='#ff4444',
                font=('Arial', 12),
                justify='center'
            )
            return
        
        try:
            # Load image with PIL
            self._pil_image = Image.open(image_path)
            orig_width, orig_height = self._pil_image.size
            print(f"Loaded image: {orig_width}x{orig_height}")
            
            # Calculate available canvas size (after instructions and buttons)
            self.root.update()
            canvas_width = self.canvas.winfo_width() or (self.window_width)
            canvas_height = self.canvas.winfo_height() or (self.window_height - 70)
            
            print(f"Canvas size: {canvas_width}x{canvas_height}")
            
            # Resize image to EXACTLY fill the canvas (matching embedded Roblox size)
            # This ensures 1:1 coordinate mapping with the game window
            new_width = canvas_width
            new_height = canvas_height
            
            # Calculate scale factors for coordinate conversion
            self.scale_x = new_width / orig_width
            self.scale_y = new_height / orig_height
            
            print(f"Resized image: {new_width}x{new_height} (scale_x={self.scale_x:.3f}, scale_y={self.scale_y:.3f})")
            
            # Resize and convert to Tk format - stretch to fill exactly
            resized = self._pil_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            self._tk_image = ImageTk.PhotoImage(resized)
            
            # Image fills entire canvas - no offset needed
            self.image_x = 0
            self.image_y = 0
            
            self.canvas.create_image(
                self.image_x, self.image_y, 
                anchor=tk.NW, 
                image=self._tk_image,
                tags="background"
            )
            
            print(f"Image displayed at ({self.image_x}, {self.image_y}) - fills canvas")
            
        except Exception as e:
            print(f"Error loading image: {e}")
            import traceback
            traceback.print_exc()
            self.canvas.create_text(
                self.window_width // 2,
                (self.window_height - 80) // 2,
                text=f"Error loading image:\n{str(e)}",
                fill='#ff4444',
                font=('Arial', 14),
                justify='center'
            )
    
    def _draw_existing_selection(self):
        """Draw existing selection from config"""
        location = self.config.get("location", "Leaf Village")
        location_key = get_location_key(location)
        placement_areas = self.config.get("placement_areas", {})
        area = placement_areas.get(location_key)
        
        if area and self._tk_image:
            # Coordinates are now stored as direct game window coordinates
            x1 = area["x"]
            y1 = area["y"]
            x2 = area["x"] + area["width"]
            y2 = area["y"] + area["height"]
            
            self.current_rect = self.canvas.create_rectangle(
                x1, y1, x2, y2,
                outline='#00ff00', width=3
            )
            self.selection = area
            self._update_selection_label()
            print(f"Drew existing selection: {area}")
    
    def _on_mouse_down(self, event):
        """Start drawing selection"""
        self.start_x = event.x
        self.start_y = event.y
        
        if self.current_rect:
            self.canvas.delete(self.current_rect)
        
        self.current_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline='#00ff00', width=3
        )
    
    def _on_mouse_drag(self, event):
        """Update selection rectangle"""
        if self.current_rect and self.start_x is not None:
            self.canvas.coords(
                self.current_rect,
                self.start_x, self.start_y,
                event.x, event.y
            )
    
    def _on_mouse_up(self, event):
        """Finish selection"""
        if self.start_x is None or not self._tk_image:
            return
        
        # Get canvas coordinates (image fills canvas, so no offset adjustment needed)
        x1 = min(self.start_x, event.x)
        y1 = min(self.start_y, event.y)
        x2 = max(self.start_x, event.x)
        y2 = max(self.start_y, event.y)
        
        # Canvas coordinates ARE the game window coordinates now
        # since the image is displayed at the exact game window size
        canvas_x = int(x1)
        canvas_y = int(y1)
        canvas_width = int(x2 - x1)
        canvas_height = int(y2 - y1)
        
        if canvas_width < 10 or canvas_height < 10:
            print("Selection too small, ignoring")
            return
        
        self.selection = {
            "x": max(0, canvas_x),
            "y": max(0, canvas_y),
            "width": canvas_width,
            "height": canvas_height
        }
        
        print(f"New selection (game coords): {self.selection}")
        self._update_selection_label()
    
    def _update_selection_label(self):
        """Update the selection info label"""
        if self.selection:
            self.selection_label.config(
                text=f"Selection: ({self.selection['x']}, {self.selection['y']}) → {self.selection['width']}x{self.selection['height']}",
                fg='#00ff00'
            )
    
    def _cancel(self):
        """Cancel without saving"""
        print("Cancelled without saving")
        self.root.destroy()
    
    def _save_and_close(self):
        """Save and close"""
        if not self.selection:
            messagebox.showwarning("No Selection", "Please draw a selection area first!")
            return
        
        # Save to location-specific placement area
        location = self.config.get("location", "Leaf Village")
        location_key = get_location_key(location)
        
        if "placement_areas" not in self.config:
            self.config["placement_areas"] = {}
        
        self.config["placement_areas"][location_key] = self.selection
        print(f"Saving placement area for {location_key}: {self.selection}")
        
        if self._save_config():
            print(f"✓ Placement area saved successfully for {location_key}!")
            messagebox.showinfo("Saved", f"Placement area saved for {location_key}!\n\nArea: ({self.selection['x']}, {self.selection['y']})\nSize: {self.selection['width']}x{self.selection['height']}")
        else:
            messagebox.showerror("Error", "Failed to save configuration!")
        
        self.root.destroy()
    
    def run(self):
        """Run the editor"""
        print("Starting placement editor mainloop...")
        self.root.mainloop()


def main():
    """Main entry point when run as script"""
    if len(sys.argv) < 3:
        print("Usage: python placement_editor.py <config_path> <mode>")
        sys.exit(1)
    
    config_path = sys.argv[1]
    mode = sys.argv[2]
    
    print(f"\n{'='*50}")
    print(f"PLACEMENT EDITOR")
    print(f"Config: {config_path}")
    print(f"Mode: {mode}")
    print(f"{'='*50}\n")
    
    app = PlacementEditorApp(config_path, mode)
    app.run()


def PlacementEditor(parent, config, on_save_callback):
    """
    Wrapper function to launch placement editor from main.py
    Creates a subprocess to avoid Tkinter threading issues
    """
    import subprocess
    import tempfile
    
    # Get mode from config
    mode = config.get("mode", "Story")
    
    # Save config to temp file
    config_path = "config.json"
    
    # Launch placement editor as subprocess
    script_dir = os.path.dirname(os.path.abspath(__file__))
    editor_script = os.path.join(script_dir, "placement_editor.py")
    
    # Use python executable from sys
    python_exe = sys.executable
    
    print(f"Launching placement editor: mode={mode}")
    subprocess.run([python_exe, editor_script, config_path, mode])
    
    # Reload config after editor closes
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            updated_config = json.load(f)
            on_save_callback(updated_config)
    except Exception as e:
        print(f"Error reloading config after placement editor: {e}")


if __name__ == "__main__":
    main()
