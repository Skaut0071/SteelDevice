import os
import sys
import time
import json
import threading
import winreg
import hid
import pystray
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
from PIL import Image, ImageDraw
from dataclasses import dataclass
from typing import Optional, Dict

# Configuration
CONFIG_FILE = "battery_config.json"
DEFAULT_CONFIG = {
    "update_interval": 300,
    "autostart": True,
    "debug_mode": True
}

STEELSERIES_VID = 0x1038

# All known SteelSeries headsets with HID battery commands
KNOWN_HEADSETS = [
    # Arctis Nova Series
    {"name": "Arctis Nova 5", "pid": 0x2232, "interface": 3, "cmd": [0x00, 0xb0], "battery_idx": 3, "buf_size": 64, "status_idx": 4},
    {"name": "Arctis Nova 7", "pid": 0x2202, "interface": 3, "cmd": [0x00, 0xb0], "battery_idx": 2, "buf_size": 8, "status_idx": 3},
    {"name": "Arctis Nova 7X", "pid": 0x2206, "interface": 3, "cmd": [0x00, 0xb0], "battery_idx": 2, "buf_size": 8, "status_idx": 3},
    {"name": "Arctis Nova 7P", "pid": 0x220a, "interface": 3, "cmd": [0x00, 0xb0], "battery_idx": 2, "buf_size": 8, "status_idx": 3},
    
    # Arctis 7 Series
    {"name": "Arctis 7 Plus", "pid": 0x220e, "interface": 3, "cmd": [0x00, 0xb0], "battery_idx": 2, "buf_size": 8, "status_idx": 3},
    {"name": "Arctis 7 2019", "pid": 0x12ad, "interface": 3, "cmd": [0x06, 0x18], "battery_idx": 2, "buf_size": 8, "status_idx": None},
    {"name": "Arctis 7 2017", "pid": 0x1260, "interface": 3, "cmd": [0x06, 0x18], "battery_idx": 2, "buf_size": 8, "status_idx": None},
    {"name": "Arctis 7X", "pid": 0x12d7, "interface": 3, "cmd": [0x06, 0x12], "battery_idx": 3, "buf_size": 8, "status_idx": 4},
    
    # Arctis 9 Series
    {"name": "Arctis 9", "pid": 0x12c2, "interface": 3, "cmd": [0x00, 0x20], "battery_idx": 3, "buf_size": 12, "status_idx": 4},
    
    # Arctis Pro Series
    {"name": "Arctis Pro Wireless", "pid": 0x1290, "interface": 3, "cmd": [0x40, 0xaa], "battery_idx": 0, "buf_size": 2, "status_idx": None},
    {"name": "Arctis Pro 2019", "pid": 0x1252, "interface": 3, "cmd": [0x06, 0x18], "battery_idx": 2, "buf_size": 8, "status_idx": None},
    {"name": "Arctis Pro GameDAC", "pid": 0x1280, "interface": 3, "cmd": [0x06, 0x18], "battery_idx": 2, "buf_size": 8, "status_idx": None},
    
    # Arctis 1 Wireless
    {"name": "Arctis 1 Wireless", "pid": 0x12b3, "interface": 3, "cmd": [0x06, 0x12], "battery_idx": 3, "buf_size": 8, "status_idx": 4},
]

@dataclass
class DeviceStatus:
    name: str
    battery: Optional[int] = None
    charging: bool = False
    connected: bool = False

class BatteryMonitor:
    def __init__(self):
        self.config = self.load_config()
        
        # Save config if it doesn't exist (first run)
        if not os.path.exists(CONFIG_FILE):
            self.save_config()
            self.debug("Created default config file")
        
        self.mouse = DeviceStatus("Mouse")
        self.headset = DeviceStatus("Headset")
        self.icon = None
        self.running = True
        self.settings_window = None
        
        # Create hidden tkinter root for settings window
        self.root = tk.Tk()
        self.root.withdraw()
        
    def load_config(self):
        """Load config with proper defaults"""
        config = {
            "update_interval": 300,
            "autostart": False,
            "debug_mode": False,
            "colors": {
                "high": "#00FF00",
                "medium": "#FFFF00",
                "low": "#FF0000",
                "charging": "#FFA500",
                "error": "#808080"
            }
        }
        
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    loaded = json.load(f)
                
                # Update config with loaded values
                if isinstance(loaded, dict):
                    for key, value in loaded.items():
                        if key == 'colors' and isinstance(value, dict):
                            # Merge colors
                            config['colors'].update(value)
                        else:
                            config[key] = value
        except Exception as e:
            print(f"Config load error: {e}")
        
        return config
    
    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Config save error: {e}")
    
    def setup_autostart(self):
        """Add/remove program from Windows startup"""
        try:
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
            app_name = "SteelSeriesBatteryMonitor"
            app_path = os.path.abspath(sys.argv[0])
            
            if self.config.get("autostart", False):
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{app_path}"')
                self.debug("Autostart enabled")
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, app_name)
                    self.debug("Autostart disabled")
                except FileNotFoundError:
                    pass
        except Exception as e:
            self.debug(f"Error setting up autostart: {e}")
    
    def debug(self, msg):
        if self.config.get("debug_mode"):
            print(f"[DEBUG] {msg}")
    
    def find_and_read_device(self, device_list, device_type):
        """Generic function to find and read battery from HID device"""
        devices = hid.enumerate(STEELSERIES_VID)
        
        for device_info in devices:
            pid = device_info['product_id']
            interface = device_info['interface_number']
            
            for known_device in device_list:
                if pid == known_device['pid'] and interface == known_device.get('interface', interface):
                    self.debug(f"Found {device_type}: {known_device['name']} (PID: {hex(pid)}, Interface: {interface})")
                    
                    try:
                        dev = hid.device()
                        dev.open_path(device_info['path'])
                        dev.set_nonblocking(1)  # Non-blocking mode!
                        
                        # Send command
                        dev.write(known_device['cmd'])
                        time.sleep(0.1)  # Wait for response
                        
                        # Try to read with timeout
                        data = None
                        for attempt in range(5):  # Try 5 times
                            data = dev.read(known_device['buf_size'])
                            if data:
                                break
                            time.sleep(0.05)
                        
                        dev.close()
                        
                        if not data:
                            self.debug(f"{device_type} read timeout")
                            continue
                        
                        self.debug(f"{device_type} raw data: {list(data)[:10]}")
                        
                        # Check connection status if available
                        if 'status_idx' in known_device and known_device['status_idx'] is not None:
                            if known_device['status_idx'] < len(data):
                                if data[known_device['status_idx']] == 0:
                                    self.debug(f"{device_type} dongle found but device not connected")
                                    continue
                        
                        # Read battery
                        if known_device['battery_idx'] < len(data):
                            battery = data[known_device['battery_idx']]
                            
                            # Handle different battery ranges
                            # Some devices report 0-4, others 0-100, others 100-165
                            if battery <= 4:
                                battery = int((battery / 4.0) * 100)
                            elif battery > 100 and battery <= 165:
                                # Arctis 9 range: 100-165
                                battery = int(((battery - 100) / 65.0) * 100)
                            
                            battery = max(0, min(100, battery))
                            
                            self.debug(f"✓ {device_type}: {known_device['name']} at {battery}%")
                            return DeviceStatus(known_device['name'], battery, False, True)
                        else:
                            self.debug(f"{device_type} battery index out of range")
                        
                    except Exception as e:
                        self.debug(f"{device_type} read error: {e}")
                        import traceback
                        if self.config.get("debug_mode"):
                            traceback.print_exc()
                        continue
        
        return None
    
    def update_devices(self):
        self.debug("Updating device status...")
        
        # Update mouse - use rivalcfg (more reliable for mice)
        mouse_result = self.try_rivalcfg_mouse()
        if mouse_result:
            self.mouse = mouse_result
        else:
            self.mouse = DeviceStatus("Mouse", connected=False)
        
        # Update headset - use HID (works perfectly)
        headset_result = self.find_and_read_device(KNOWN_HEADSETS, "Headset")
        if headset_result:
            self.headset = headset_result
        else:
            self.headset = DeviceStatus("Headset", connected=False)
        
        self.debug(f"Final: Mouse {self.mouse.battery}%, Headset {self.headset.battery}%")
    
    def try_rivalcfg_mouse(self):
        """Get ANY SteelSeries mouse battery using rivalcfg library"""
        try:
            import rivalcfg
            
            # Try multiple times (mouse may be in sleep mode)
            for attempt in range(5):
                try:
                    self.debug(f"Trying rivalcfg (attempt {attempt + 1}/5)...")
                    mouse = rivalcfg.get_first_mouse()
                    
                    if mouse and mouse.name:
                        self.debug(f"Found: {mouse.name}")
                        
                        # Try to get battery info multiple times with delays
                        for battery_attempt in range(6):
                            try:
                                battery_info = mouse.battery
                                self.debug(f"Battery attempt {battery_attempt + 1}: {battery_info}")
                                
                                if battery_info and battery_info.get("level") is not None:
                                    level = battery_info["level"]
                                    charging = battery_info.get("is_charging", False)
                                    mouse.close()
                                    self.debug(f"✓ {mouse.name}: {level}% (charging: {charging})")
                                    return DeviceStatus(mouse.name, level, charging, True)
                                    
                            except Exception as e:
                                self.debug(f"Battery read error: {e}")
                            
                            time.sleep(0.25)
                        
                        mouse.close()
                    
                except Exception as e:
                    self.debug(f"Attempt {attempt + 1} error: {e}")
                
                if attempt < 4:
                    time.sleep(0.3)
                
        except ImportError:
            self.debug("⚠ rivalcfg not installed (pip install rivalcfg)")
        except Exception as e:
            self.debug(f"rivalcfg fatal error: {e}")
        
        return None
    
    def get_color(self, battery):
        if battery is None:
            return self.config["colors"]["error"]
        if battery < 20:
            return self.config["colors"]["low"]
        if battery < 50:
            return self.config["colors"]["medium"]
        return self.config["colors"]["high"]
    
    def create_icon_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Mouse (left half)
        if self.mouse.connected and self.mouse.battery is not None:
            color = self.get_color(self.mouse.battery)
            height = int((self.mouse.battery / 100.0) * 60)
            draw.rectangle([2, 62 - height, 30, 62], fill=color)
            # Show charging indicator
            if self.mouse.charging:
                draw.rectangle([10, 5, 22, 15], fill=self.config["colors"]["charging"])
        else:
            draw.rectangle([2, 2, 30, 62], fill=self.config["colors"]["error"])
        
        # Headset (right half)
        if self.headset.connected and self.headset.battery is not None:
            color = self.get_color(self.headset.battery)
            height = int((self.headset.battery / 100.0) * 60)
            draw.rectangle([34, 62 - height, 62, 62], fill=color)
        else:
            draw.rectangle([34, 2, 62, 62], fill=self.config["colors"]["error"])
        
        # Divider
        draw.line([32, 2, 32, 62], fill="white", width=2)
        draw.rectangle([0, 0, 63, 63], outline="white", width=2)
        
        return img
    
    def create_menu(self):
        items = []
        
        # Mouse info
        if self.mouse.connected:
            items.append(pystray.MenuItem(f"🖱️ {self.mouse.name}: {self.mouse.battery}%", None, enabled=False))
        else:
            items.append(pystray.MenuItem("🖱️ Mouse: Not Connected", None, enabled=False))
        
        # Headset info
        if self.headset.connected:
            items.append(pystray.MenuItem(f"🎧 {self.headset.name}: {self.headset.battery}%", None, enabled=False))
        else:
            items.append(pystray.MenuItem("🎧 Headset: Not Connected", None, enabled=False))
        
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("🔄 Refresh", self.force_refresh))
        items.append(pystray.MenuItem("⚙️ Settings", self.open_settings))
        items.append(pystray.MenuItem("❌ Quit", self.quit))
        
        return pystray.Menu(*items)
    
    def force_refresh(self):
        self.debug("Force refresh triggered")
        self.update_devices()
        self.update_tray()
    
    def open_settings(self):
        """Open settings window"""
        def _open():
            if self.settings_window is None or not self.settings_window.window.winfo_exists():
                self.settings_window = SettingsWindow(self)
        self.root.after(0, _open)
    
    def update_tray(self):
        if self.icon:
            self.icon.icon = self.create_icon_image()
            self.icon.menu = self.create_menu()
            
            # Update tooltip
            tooltip = []
            if self.mouse.connected:
                tooltip.append(f"Mouse: {self.mouse.battery}%")
            if self.headset.connected:
                tooltip.append(f"Headset: {self.headset.battery}%")
            self.icon.title = " | ".join(tooltip) if tooltip else "No devices"
    
    def quit(self):
        self.debug("Quitting...")
        self.running = False
        if self.icon:
            self.icon.stop()
        self.root.quit()
        self.root.destroy()
    
    def monitor_loop(self):
        """Background thread that updates device status periodically"""
        while self.running:
            try:
                interval = self.config.get("update_interval", 300)
                self.debug(f"Waiting {interval}s until next update...")
                time.sleep(interval)
                
                if not self.running:
                    break
                
                self.debug("Auto-updating device status...")
                self.update_devices()
                self.update_tray()
                
            except Exception as e:
                self.debug(f"Monitor loop error: {e}")
    
    def run(self):
        self.debug("Starting Battery Monitor...")
        
        # Initial update
        self.update_devices()
        
        self.debug(f"Creating tray icon...")
        self.icon = pystray.Icon(
            "SteelSeriesBattery",
            self.create_icon_image(),
            "SteelSeries Battery",
            self.create_menu()
        )
        
        # Start monitor thread
        monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        monitor_thread.start()
        
        # Start tray icon in separate thread
        icon_thread = threading.Thread(target=self.icon.run, daemon=True)
        icon_thread.start()
        
        self.debug("Tray icon running, starting tkinter mainloop...")
        
        # Run tkinter mainloop in main thread
        self.root.mainloop()

class SettingsWindow:
    def __init__(self, monitor: BatteryMonitor):
        self.monitor = monitor
        self.window = tk.Toplevel(monitor.root)
        self.window.title("Battery Monitor Settings")
        self.window.geometry("500x550")
        self.window.resizable(False, False)
        self.window.attributes('-topmost', True)
        
        self.setup_ui()
        self.load_current_settings()
        
        # Center window
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() - self.window.winfo_width()) // 2
        y = (self.window.winfo_screenheight() - self.window.winfo_height()) // 2
        self.window.geometry(f"+{x}+{y}")
        
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def setup_ui(self):
        main_frame = ttk.Frame(self.window, padding="15")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Update interval
        ttk.Label(main_frame, text="Update Interval:", font=("", 10, "bold")).grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        self.interval_var = tk.StringVar()
        interval_combo = ttk.Combobox(main_frame, textvariable=self.interval_var, width=20, state="readonly")
        interval_combo['values'] = ('1 minute', '5 minutes', '10 minutes', '30 minutes', '1 hour')
        interval_combo.grid(row=1, column=0, sticky=(tk.W, tk.E), padx=(0, 0), pady=(0, 15))
        
        # Autostart
        self.autostart_var = tk.BooleanVar()
        ttk.Checkbutton(main_frame, text="Start with Windows", variable=self.autostart_var).grid(
            row=2, column=0, sticky=tk.W, pady=(0, 5))
        
        # Debug mode
        self.debug_var = tk.BooleanVar()
        ttk.Checkbutton(main_frame, text="Debug Mode (show console output)", variable=self.debug_var).grid(
            row=3, column=0, sticky=tk.W, pady=(0, 20))
        
        # Battery colors
        colors_frame = ttk.LabelFrame(main_frame, text="Battery Colors", padding="10")
        colors_frame.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        
        self.color_vars = {}
        color_labels = [
            ("High Battery (≥50%)", "high"),
            ("Medium Battery (20-50%)", "medium"),
            ("Low Battery (<20%)", "low"),
            ("Charging", "charging"),
            ("Disconnected/Error", "error")
        ]
        
        for i, (label, key) in enumerate(color_labels):
            ttk.Label(colors_frame, text=f"{label}:").grid(row=i, column=0, sticky=tk.W, pady=5)
            color_button = tk.Button(colors_frame, width=12, relief=tk.RAISED, 
                                     command=lambda k=key: self.choose_color(k))
            color_button.grid(row=i, column=1, sticky=tk.E, padx=(10, 0), pady=5)
            self.color_vars[key] = color_button
        
        # Device status
        status_frame = ttk.LabelFrame(main_frame, text="Current Device Status", padding="10")
        status_frame.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(0, 15))
        
        self.mouse_status_label = ttk.Label(status_frame, text="Mouse: Checking...")
        self.mouse_status_label.grid(row=0, column=0, sticky=tk.W, pady=2)
        
        self.headset_status_label = ttk.Label(status_frame, text="Headset: Checking...")
        self.headset_status_label.grid(row=1, column=0, sticky=tk.W, pady=2)
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=6, column=0, pady=(10, 0))
        
        ttk.Button(button_frame, text="Cancel", command=self.on_close, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Apply", command=self.apply_settings, width=15).pack(side=tk.LEFT, padx=5)
        
        # Start status updates
        self.update_device_status()
    
    def load_current_settings(self):
        config = self.monitor.config
        
        # Update interval
        interval = config.get("update_interval", 300)
        interval_map = {60: "1 minute", 300: "5 minutes", 600: "10 minutes", 
                       1800: "30 minutes", 3600: "1 hour"}
        self.interval_var.set(interval_map.get(interval, "5 minutes"))
        
        # Checkboxes
        self.autostart_var.set(config.get("autostart", False))
        self.debug_var.set(config.get("debug_mode", False))
        
        # Colors - use monitor's config colors
        colors = config.get("colors", {
            "high": "#00FF00",
            "medium": "#FFFF00",
            "low": "#FF0000",
            "charging": "#FFA500",
            "error": "#808080"
        })
        
        for key, button in self.color_vars.items():
            color = colors.get(key, "#000000")
            button.configure(bg=color)
    
    def choose_color(self, color_key):
        current_color = self.monitor.config.get("colors", {}).get(color_key, "#000000")
        color = colorchooser.askcolor(initialcolor=current_color, parent=self.window)[1]
        if color:
            self.color_vars[color_key].configure(bg=color)
    
    def apply_settings(self):
        try:
            # Update interval
            interval_map = {"1 minute": 60, "5 minutes": 300, "10 minutes": 600, 
                           "30 minutes": 1800, "1 hour": 3600}
            self.monitor.config["update_interval"] = interval_map.get(self.interval_var.get(), 300)
            
            # Checkboxes
            self.monitor.config["autostart"] = self.autostart_var.get()
            self.monitor.config["debug_mode"] = self.debug_var.get()
            
            # Ensure colors dict exists
            if "colors" not in self.monitor.config:
                self.monitor.config["colors"] = {}
            
            # Colors
            for key, button in self.color_vars.items():
                self.monitor.config["colors"][key] = button.cget("bg")
            
            # Apply autostart
            self.monitor.setup_autostart()
            
            # Save config
            self.monitor.save_config()
            
            # Update tray icon
            self.monitor.update_tray()
            
            messagebox.showinfo("Settings", "Settings saved successfully!", parent=self.window)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {e}", parent=self.window)
    
    def update_device_status(self):
        try:
            if not self.window.winfo_exists():
                return
            
            # Mouse status
            if self.monitor.mouse.connected and self.monitor.mouse.battery is not None:
                mouse_text = f"Mouse: {self.monitor.mouse.name} - {self.monitor.mouse.battery}%"
                if self.monitor.mouse.charging:
                    mouse_text += " (Charging)"
            else:
                mouse_text = "Mouse: Not Connected"
            self.mouse_status_label.config(text=mouse_text)
            
            # Headset status
            if self.monitor.headset.connected and self.monitor.headset.battery is not None:
                headset_text = f"Headset: {self.monitor.headset.name} - {self.monitor.headset.battery}%"
            else:
                headset_text = "Headset: Not Connected"
            self.headset_status_label.config(text=headset_text)
            
            # Schedule next update
            self.window.after(2000, self.update_device_status)
        except:
            pass
    
    def on_close(self):
        if self.window:
            self.window.destroy()
        self.monitor.settings_window = None

if __name__ == "__main__":
    print("=" * 60)
    print("SteelSeries Battery Monitor v2.0")
    print("Universal support for all SteelSeries mice and headsets")
    print("=" * 60)
    print("• Mice: Auto-detected via rivalcfg")
    print("• Headsets: HID protocol (13 models supported)")
    print("• Update interval: 5 minutes (configurable)")
    print("=" * 60)
    print("Starting...\n")
    
    monitor = BatteryMonitor()
    monitor.run()
