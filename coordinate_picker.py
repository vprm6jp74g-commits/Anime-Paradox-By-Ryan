"""
Coordinate Picker - Click on image to select coordinates for unit placement
"""
import tkinter as tk
from PIL import Image, ImageTk
import sys
import json
import os
def get_app_path():
    """Get the application path for writable data (Settings folder)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(__file__)
class CoordinatePicker:
    def __init__(self, image_path, mode, location, act, unit_index, window_x=0, window_y=0, window_width=800, window_height=600, other_units=None):
        self.image_path = image_path
        self.mode = mode  # 'Story', 'Raid', or 'Siege'
        self.location = location
        self.act = act
        self.unit_index = int(unit_index)
        self.window_x = window_x
        self.window_y = window_y
        self.window_width = window_width
        self.window_height = window_height
        self.selected_x = None
        self.selected_y = None
        self.other_units = other_units or []
        
        # Create window without title bar for perfect overlay
        self.root = tk.Tk()
        self.root.title(f"Select Coordinates for Unit {self.unit_index}")
        self.root.configure(bg='#1a1a2e')
        
        # Make window borderless and always on top
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        
        # Load and display image
        self.original_image = Image.open(image_path)
        
        # Use exact window size (no scaling) to match Roblox window
        self.display_image = self.original_image.resize((window_width, window_height), Image.Resampling.LANCZOS)
        self.scale_factor_x = window_width / self.original_image.width
        self.scale_factor_y = window_height / self.original_image.height
        
        self.photo = ImageTk.PhotoImage(self.display_image)
        
        # Create main container frame
        main_frame = tk.Frame(self.root, bg='#1a1a2e')
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create canvas with exact window dimensions
        self.canvas = tk.Canvas(
            main_frame,
            width=window_width,
            height=window_height,
            bg='#1a1a2e',
            highlightthickness=0
        )
        self.canvas.pack()
        
        # Display image
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
        
        # Draw existing unit markers
        self._draw_other_unit_markers()
        
        # Create semi-transparent instruction overlay at top
        instruction_bar = tk.Frame(main_frame, bg='#1a1a2e', height=30)
        instruction_bar.place(x=0, y=0, width=window_width)
        
        tk.Label(
            instruction_bar,
            text=f"Click to select coordinates for Unit {self.unit_index}",
            font=('Arial', 10, 'bold'),
            bg='#1a1a2e',
            fg='#00d4ff'
        ).pack(side=tk.LEFT, padx=10, pady=5)
        
        # Coordinate display
        self.coord_label = tk.Label(
            instruction_bar,
            text="No selection",
            font=('Arial', 9),
            bg='#1a1a2e',
            fg='#ffffff'
        )
        self.coord_label.pack(side=tk.LEFT, padx=10)
        
        # Create button bar at bottom
        button_frame = tk.Frame(main_frame, bg='#1a1a2e', height=40)
        button_frame.place(x=0, y=window_height - 40, width=window_width)
        
        tk.Button(
            button_frame,
            text="Save",
            command=self.save_coordinates,
            bg='#00d4ff',
            fg='#1a1a2e',
            font=('Arial', 10, 'bold'),
            padx=15,
            pady=5,
            relief=tk.FLAT
        ).pack(side=tk.LEFT, padx=10, pady=5)
        
        tk.Button(
            button_frame,
            text="Cancel",
            command=self.cancel,
            bg='#ff4444',
            fg='white',
            font=('Arial', 10, 'bold'),
            padx=15,
            pady=5,
            relief=tk.FLAT
        ).pack(side=tk.LEFT, padx=5, pady=5)
        
        # Bind click event
        self.canvas.bind('<Button-1>', self.on_click)
        
        # Marker for selected point
        self.marker = None
        
        # Position window to exactly overlay Roblox window
        self.root.geometry(f'{window_width}x{window_height}+{window_x}+{window_y}')
    
    def _draw_other_unit_markers(self):
        """Draw markers for other existing unit positions"""
        print(f"DEBUG: Drawing markers for {len(self.other_units)} units")
        for unit in self.other_units:
            # Skip the current unit being edited
            if unit.get("index") == self.unit_index:
                print(f"DEBUG: Skipping current unit {self.unit_index}")
                continue
            
            # Coordinates are now stored as relative to Roblox window, use them directly
            canvas_x = unit.get("x", 0)
            canvas_y = unit.get("y", 0)
            
            print(f"DEBUG: Unit {unit.get('index')}: relative coords ({canvas_x}, {canvas_y})")
            
            # Skip if outside window bounds
            if canvas_x < 0 or canvas_x > self.window_width or canvas_y < 0 or canvas_y > self.window_height:
                print(f"DEBUG: Unit {unit.get('index')} is outside window bounds, skipping")
                continue
            
            # Draw a small marker for this unit
            marker_size = 8
            unit_index = unit.get("index", "?")
            note = unit.get("note", f"Unit {unit_index}")
            
            # Draw filled circle with different color (green for existing units)
            self.canvas.create_oval(
                canvas_x - marker_size, canvas_y - marker_size,
                canvas_x + marker_size, canvas_y + marker_size,
                outline='#00ff00',
                fill='#80ff80',
                width=2,
                tags='other_unit'
            )
            
            # Draw unit number label
            self.canvas.create_text(
                canvas_x, canvas_y - marker_size - 10,
                text=f"U{unit_index}",
                font=('Arial', 8, 'bold'),
                fill='#00ff00',
                tags='other_unit'
            )
            print(f"DEBUG: Drew marker for Unit {unit_index}")
    
    def on_click(self, event):
        """Handle click on canvas"""
        # Get canvas coordinates
        canvas_x = event.x
        canvas_y = event.y
        
        # Check if click is near an existing unit coordinate (snap feature)
        snap_threshold = 25  # pixels
        snapped_coord = None
        
        for unit in self.other_units:
            unit_x = unit.get("x", 0)
            unit_y = unit.get("y", 0)
            
            # Calculate distance to this unit
            distance = ((canvas_x - unit_x) ** 2 + (canvas_y - unit_y) ** 2) ** 0.5
            
            # If within snap threshold, snap to this coordinate
            if distance <= snap_threshold:
                snapped_coord = (unit_x, unit_y)
                print(f"Snapped to Unit {unit.get('index', '?')} at ({unit_x}, {unit_y})")
                break
        
        # Use snapped or clicked coordinates
        if snapped_coord:
            self.selected_x = snapped_coord[0]
            self.selected_y = snapped_coord[1]
            marker_color = '#ffaa00'  # Orange for snapped coordinates
        else:
            self.selected_x = canvas_x
            self.selected_y = canvas_y
            marker_color = '#ff00ff'  # Magenta for new coordinates
        
        # Update label to show relative coordinates
        snap_indicator = " (SNAPPED)" if snapped_coord else ""
        self.coord_label.config(
            text=f"Relative: ({self.selected_x}, {self.selected_y}){snap_indicator}",
            fg='#00ff00'
        )
        
        # Draw marker
        if self.marker:
            self.canvas.delete(self.marker)
        
        marker_size = 10
        self.marker = self.canvas.create_oval(
            self.selected_x - marker_size, self.selected_y - marker_size,
            self.selected_x + marker_size, self.selected_y + marker_size,
            outline=marker_color,
            width=3
        )
        
        # Draw crosshair
        self.canvas.create_line(
            canvas_x - marker_size*2, canvas_y,
            canvas_x + marker_size*2, canvas_y,
            fill='#ff00ff',
            width=2,
            tags='crosshair'
        )
        self.canvas.create_line(
            canvas_x, canvas_y - marker_size*2,
            canvas_x, canvas_y + marker_size*2,
            fill='#ff00ff',
            width=2,
            tags='crosshair'
        )
    
    def save_coordinates(self):
        """Save selected coordinates to unit config"""
        if self.selected_x is None or self.selected_y is None:
            self.coord_label.config(
                text="Please click on the image first!",
                fg='#ff4444'
            )
            return
        
        try:
            # Load unit config - use app path for writable Settings folder
            # Mode determines the subfolder: Story, Raid, or Siege
            settings_folder = os.path.join(get_app_path(), "Settings", self.mode, self.location)
            os.makedirs(settings_folder, exist_ok=True)
            config_path = os.path.join(settings_folder, f"{self.act}.json")
            
            # Create blank config if it doesn't exist
            if not os.path.exists(config_path):
                blank_config = {
                    "Units": [
                        {
                            "Index": i,
                            "Enabled": False,
                            "Slot": "1",
                            "X": "",
                            "Y": "",
                            "Upgrade": "0",
                            "Note": f"Unit {i}"
                        }
                        for i in range(1, 31)  # 30 unit slots
                    ]
                }
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(blank_config, f, indent=4)
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Update coordinates for this unit
            for unit in config['Units']:
                if unit['Index'] == self.unit_index:
                    unit['X'] = str(self.selected_x)
                    unit['Y'] = str(self.selected_y)
                    break
            
            # Save window info for accurate dot placement on preview
            config['WindowInfo'] = {
                'x': self.window_x,
                'y': self.window_y,
                'width': self.window_width,
                'height': self.window_height
            }
            
            # Save config
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            
            print(f"Coordinates saved: Unit {self.unit_index} at ({self.selected_x}, {self.selected_y})")
            
            # Output coordinates for parent process
            print(f"{self.selected_x},{self.selected_y}")
            
            self._cleanup()
            
        except Exception as e:
            self.coord_label.config(
                text=f"Error saving: {str(e)}",
                fg='#ff4444'
            )
            print(f"Error: {e}")
    
    def cancel(self):
        """Cancel without saving"""
        self._cleanup()
    
    def run(self):
        """Run the picker"""
        try:
            self.root.mainloop()
        except Exception as e:
            print(f"Mainloop error: {e}")
        finally:
            # Ensure cleanup even if mainloop fails
            self._cleanup()
    
    def _cleanup(self):
        """Thoroughly clean up tkinter resources"""
        try:
            self.root.quit()
        except:
            pass
        try:
            self.root.destroy()
        except:
            pass
        try:
            # Clear image references to free memory
            self.photo = None
            self.original_image = None
            self.display_image = None
        except:
            pass
        try:
            # Reset tkinter internal state
            import tkinter as tk
            tk._default_root = None
        except:
            pass

if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Usage: coordinate_picker.py <image_path> <mode> <location> <act> <unit_index> [window_x] [window_y] [window_width] [window_height] [other_units_json]")
        sys.exit(1)
    
    image_path = sys.argv[1]
    mode = sys.argv[2]  # Story, Raid, or Siege
    location = sys.argv[3]
    act = sys.argv[4]
    unit_index = sys.argv[5]
    
    # Optional window positioning parameters
    window_x = int(sys.argv[6]) if len(sys.argv) > 6 else 0
    window_y = int(sys.argv[7]) if len(sys.argv) > 7 else 0
    window_width = int(sys.argv[8]) if len(sys.argv) > 8 else 800
    window_height = int(sys.argv[9]) if len(sys.argv) > 9 else 600
    
    # Parse other units JSON if provided
    other_units = []
    if len(sys.argv) > 10:
        arg = sys.argv[10]
        try:
            # Check if it's a file path (starts with @)
            if arg.startswith('@'):
                file_path = arg[1:]  # Remove @ prefix
                print(f"DEBUG: Reading other_units from file: {file_path}")
                with open(file_path, 'r', encoding='utf-8') as f:
                    other_units = json.load(f)
                print(f"DEBUG: Loaded {len(other_units)} units from file")
            else:
                other_units = json.loads(arg)
        except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
            print(f"DEBUG: Error loading other_units: {e}")
            other_units = []
    
    picker = CoordinatePicker(image_path, mode, location, act, unit_index, window_x, window_y, window_width, window_height, other_units)
    picker.run()
