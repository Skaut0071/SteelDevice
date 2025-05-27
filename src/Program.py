import os
import sys
import time
import json
import threading
import winreg
import hid
import rivalcfg
import pystray
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
from PIL import Image, ImageDraw
from dataclasses import dataclass
from typing import Optional, Tuple

root = tk.Tk()
root.withdraw()

CONFIG_FILE = "battery_monitor_config.json"
DEFAULT_CONFIG = {
    "update_interval": 300,
    "autostart": True,
    "icon_style": "split",
    "colors": {
        "high": "#00FF00",
        "medium": "#FFFF00",
        "low": "#FF0000",
        "charging": "#FFA500",
        "error": "#808080"
    },
    "show_percentages": True,
    "debug_mode": False
}

STEELSERIES_VID = 0x1038
STEELSERIES_HEADPHONES = [
    {"name": "Arctis Pro Wireless", "product_id": 0x1290, "write_bytes": [0x40, 0xaa], "battery_percent_idx": 0, "read_buf_size": 2, "battery_range": (0x00, 0x04)},
    {"name": "Arctis 7 2017", "product_id": 0x1260, "write_bytes": [0x06, 0x18], "battery_percent_idx": 2, "read_buf_size": 8, "battery_range": (0x00, 0x04)},
    {"name": "Arctis 7 2019", "product_id": 0x12ad, "write_bytes": [0x06, 0x18], "battery_percent_idx": 2, "read_buf_size": 8, "battery_range": (0x00, 0x04)},
    {"name": "Arctis Pro 2019", "product_id": 0x1252, "write_bytes": [0x06, 0x18], "battery_percent_idx": 2, "read_buf_size": 8, "battery_range": (0x00, 0x04)},
    {"name": "Arctis Pro GameDAC", "product_id": 0x1280, "write_bytes": [0x06, 0x18], "battery_percent_idx": 2, "read_buf_size": 8, "battery_range": (0x00, 0x04)},
    {"name": "Arctis 9", "product_id": 0x12c2, "write_bytes": [0x00, 0x20], "battery_percent_idx": 3, "read_buf_size": 12, "battery_range": (0x64, 0xa5), "connected_status_idx": 4},
    {"name": "Arctis 1 Wireless", "product_id": 0x12b3, "write_bytes": [0x06, 0x12], "battery_percent_idx": 3, "read_buf_size": 8, "battery_range": (0x00, 0x04), "connected_status_idx": 4},
    {"name": "Arctis 7X", "product_id": 0x12d7, "write_bytes": [0x06, 0x12], "battery_percent_idx": 3, "read_buf_size": 8, "battery_range": (0x00, 0x04), "connected_status_idx": 4},
    {"name": "Arctis 7 Plus", "product_id": 0x220e, "write_bytes": [0x00, 0xb0], "battery_percent_idx": 2, "read_buf_size": 8, "battery_range": (0x00, 0x04), "connected_status_idx": 3},
    {"name": "Arctis Nova 7", "product_id": 0x2202, "write_bytes": [0x00, 0xb0], "battery_percent_idx": 2, "read_buf_size": 8, "battery_range": (0x00, 0x04), "connected_status_idx": 3},
    {"name": "Arctis Nova 7X", "product_id": 0x2206, "write_bytes": [0x00, 0xb0], "battery_percent_idx": 2, "read_buf_size": 8, "battery_range": (0x00, 0x04), "connected_status_idx": 3},
    {"name": "Arctis Nova 7P", "product_id": 0x220a, "write_bytes": [0x00, 0xb0], "battery_percent_idx": 2, "read_buf_size": 8, "battery_range": (0x00, 0x04), "connected_status_idx": 3},
    {"name": "Arctis Nova 5", "product_id": 0x2232, "write_bytes": [0x00, 0xb0], "battery_percent_idx": 3, "read_buf_size": 64, "battery_range": (0x00, 0x64), "connected_status_idx": 4},
]

@dataclass
class DeviceStatus:
    name: str
    battery_level: Optional[int] = None
    is_charging: Optional[bool] = None
    is_connected: bool = False

class BatteryMonitor:
    def __init__(self):
        self.config = self.load_config()
        self.mouse_status = DeviceStatus("Mouse")
        self.headphone_status = DeviceStatus("Headphones")
        self.last_headphone_battery = None
        self.tray_icon = None
        self.settings_window = None
        self.running = True
        self.update_event = threading.Event()
        self.mouse_fail_count = 0
        self.headphone_fail_count = 0
        if self.config.get("autostart", True):
            self.setup_autostart()

    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                merged = DEFAULT_CONFIG.copy()
                merged.update(config)
                return merged
        except Exception as e:
            print(f"Error loading config: {e}")
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    def setup_autostart(self):
        try:
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
            app_name = "BatteryMonitor"
            app_path = os.path.abspath(sys.argv[0])
            if self.config.get("autostart", True):
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{app_path}"')
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
        except Exception as e:
            print(f"Error setting up autostart: {e}")

    def find_steelseries_headphones(self) -> Optional[dict]:
        try:
            devices = hid.enumerate(STEELSERIES_VID)
            if self.config.get("debug_mode"):
                print(f"[DEBUG] HID enumerate found {len(devices)} SteelSeries devices")
            for device in devices:
                for model in STEELSERIES_HEADPHONES:
                    if device['product_id'] == model['product_id']:
                        if self.config.get("debug_mode"):
                            print(f"[DEBUG] Checking headphone: {model['name']} (PID: {hex(model['product_id'])})")
                        idx = model.get('connected_status_idx')
                        if idx is not None:
                            try:
                                d = hid.device()
                                d.open(STEELSERIES_VID, model['product_id'])
                                d.write(model['write_bytes'])
                                resp = d.read(model['read_buf_size'])
                                d.close()
                                if not resp or resp[idx] == 0:
                                    continue
                            except Exception:
                                continue
                        if self.config.get("debug_mode"):
                            print(f"Found headphone: {model['name']}")
                        return model
        except Exception as e:
            if self.config.get("debug_mode"):
                print(f"Error finding headphones: {e}")
        return None

    def get_headphone_battery(self, model: dict) -> Tuple[Optional[int], None]:
        try:
            d = hid.device()
            d.open(STEELSERIES_VID, model['product_id'])
            d.write(model['write_bytes'])
            resp = d.read(model['read_buf_size'])
            d.close()
            if self.config.get("debug_mode"):
                print(f"[DEBUG] Headphone raw response: {list(resp) if resp else resp}")
            if not resp:
                return None, None
            idx = model['battery_percent_idx']
            raw = resp[idx]
            rng = model['battery_range']
            if rng == (0x00, 0x04):
                percent = int((raw / 4.0) * 100)
            elif rng == (0x64, 0xa5):
                percent = int(((raw - 100) / 65.0) * 100)
            elif rng == (0x00, 0x64):
                percent = raw
            else:
                percent = raw
            percent = max(0, min(100, percent))
            return percent, None
        except Exception as e:
            if self.config.get("debug_mode"):
                print(f"Error getting headphone battery: {e}")
            return None, None

    def find_steelseries_mouse(self):
        for attempt in range(3):
            try:
                mouse = rivalcfg.get_first_mouse()
                if mouse and mouse.name:
                    name = mouse.name.lower()
                    if any(s in name for s in ['aerox', 'prime']):
                        if self.config.get("debug_mode"):
                            print(f"Found mouse: {mouse.name} (attempt {attempt+1})")
                        return mouse
                    elif self.config.get("debug_mode"):
                        print(f"Mouse found but not Aerox/Prime: {mouse.name}")
                elif self.config.get("debug_mode"):
                    print(f"No mouse found by rivalcfg (attempt {attempt+1})")
            except Exception as e:
                if self.config.get("debug_mode"):
                    print(f"Error finding mouse (attempt {attempt+1}): {e}")
            time.sleep(0.2)
        return None

    def get_mouse_battery(self, mouse, retries=6) -> Tuple[Optional[int], Optional[bool]]:
        for attempt in range(retries):
            try:
                battery = mouse.battery
                if self.config.get("debug_mode"):
                    print(f"Mouse battery info (attempt {attempt+1}): {battery}")
                if battery and battery.get("level") is not None:
                    level = max(0, min(100, battery["level"]))
                    charging = battery.get("is_charging", False)
                    return level, charging
            except Exception as e:
                if self.config.get("debug_mode"):
                    print(f"Error getting mouse battery (attempt {attempt+1}): {e}")
            time.sleep(0.2)
        return None, None

    def update_device_status(self):
        mouse = self.find_steelseries_mouse()
        if mouse:
            level, charging = self.get_mouse_battery(mouse)
            if level is not None:
                self.mouse_status = DeviceStatus(mouse.name, level, charging, True)
                self.mouse_fail_count = 0
            else:
                self.mouse_fail_count += 1
            mouse.close()
        else:
            self.mouse_fail_count += 1
        if self.mouse_fail_count >= 3:
            self.mouse_status = DeviceStatus("Mouse", is_connected=False)

        headphone_model = self.find_steelseries_headphones()
        if headphone_model:
            level, _ = self.get_headphone_battery(headphone_model)
            if level is not None:
                self.headphone_status = DeviceStatus(headphone_model['name'], level, None, True)
                self.last_headphone_battery = level
                self.headphone_fail_count = 0
            else:
                self.headphone_fail_count += 1
        else:
            self.headphone_fail_count += 1
        if self.headphone_fail_count >= 3:
            self.headphone_status = DeviceStatus("Headphones", is_connected=False)

    def get_battery_color(self, level, is_charging):
        if level is None:
            return self.config["colors"]["error"]
        if is_charging:
            return self.config["colors"]["charging"]
        if level < 20:
            return self.config["colors"]["low"]
        elif level < 50:
            return self.config["colors"]["medium"]
        return self.config["colors"]["high"]

    def create_icon(self):
        icon_style = self.config.get("icon_style", "split")
        image = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        if icon_style == "split":
            if self.mouse_status.is_connected and self.mouse_status.battery_level is not None:
                mouse_color = self.get_battery_color(self.mouse_status.battery_level, self.mouse_status.is_charging)
                mouse_height = int((self.mouse_status.battery_level / 100.0) * 60)
                draw.rectangle([2, 62 - mouse_height, 30, 62], fill=mouse_color)
            else:
                draw.rectangle([2, 2, 30, 62], fill=self.config["colors"]["error"])
            if self.headphone_status.is_connected and self.headphone_status.battery_level is not None:
                hp_color = self.get_battery_color(self.headphone_status.battery_level, self.headphone_status.is_charging)
                hp_height = int((self.headphone_status.battery_level / 100.0) * 60)
                draw.rectangle([34, 62 - hp_height, 62, 62], fill=hp_color)
            else:
                draw.rectangle([34, 2, 62, 62], fill=self.config["colors"]["error"])
            draw.line([32, 2, 32, 62], fill="white", width=2)
        elif icon_style == "mouse_only":
            if self.mouse_status.is_connected and self.mouse_status.battery_level is not None:
                color = self.get_battery_color(self.mouse_status.battery_level, self.mouse_status.is_charging)
                height = int((self.mouse_status.battery_level / 100.0) * 60)
                draw.rectangle([2, 62 - height, 62, 62], fill=color)
            else:
                draw.rectangle([2, 2, 62, 62], fill=self.config["colors"]["error"])
        elif icon_style == "headphone_only":
            if self.headphone_status.is_connected and self.headphone_status.battery_level is not None:
                color = self.get_battery_color(self.headphone_status.battery_level, self.headphone_status.is_charging)
                height = int((self.headphone_status.battery_level / 100.0) * 60)
                draw.rectangle([2, 62 - height, 62, 62], fill=color)
            else:
                draw.rectangle([2, 2, 62, 62], fill=self.config["colors"]["error"])
        draw.rectangle([0, 0, 63, 63], outline="white", width=2)
        return image

    def create_menu(self):
        menu_items = []
        if self.mouse_status.is_connected:
            mouse_text = f"🖱️ {self.mouse_status.name}"
            if self.mouse_status.battery_level is not None:
                mouse_text += f" - {self.mouse_status.battery_level}%"
                if self.mouse_status.is_charging:
                    mouse_text += " (Charging)"
            menu_items.append(pystray.MenuItem(mouse_text, lambda: None, enabled=False))
        else:
            menu_items.append(pystray.MenuItem("🖱️ Mouse - Not Connected", lambda: None, enabled=False))
        if self.headphone_status.is_connected:
            hp_text = f"🎧 {self.headphone_status.name}"
            if self.headphone_status.battery_level is not None:
                hp_text += f" - {self.headphone_status.battery_level}%"
            menu_items.append(pystray.MenuItem(hp_text, lambda: None, enabled=False))
        else:
            menu_items.append(pystray.MenuItem("🎧 Headphones - Not Connected", lambda: None, enabled=False))
        menu_items.append(pystray.MenuItem("", lambda: None))
        menu_items.extend([
            pystray.MenuItem("🔄 Refresh", self.force_update),
            pystray.MenuItem("⚙️ Settings", self.open_settings),
            pystray.MenuItem("❌ Quit", self.quit_app)
        ])
        return pystray.Menu(*menu_items)

    def force_update(self, icon=None, item=None):
        for _ in range(6):
            self.update_device_status()
            time.sleep(0.1)
        self.update_tray()

    def open_settings(self, icon=None, item=None):
        def _show():
            if self.settings_window is None or not self.settings_window.window.winfo_exists():
                self.settings_window = SettingsWindow(self)
        root.after(0, _show)

    def quit_app(self, icon=None, item=None):
        self.running = False
        if self.tray_icon:
            self.tray_icon.stop()

    def update_tray(self):
        if self.tray_icon:
            self.tray_icon.icon = self.create_icon()
            self.tray_icon.menu = self.create_menu()
            tooltip = []
            if self.mouse_status.is_connected and self.mouse_status.battery_level is not None:
                tooltip.append(f"Mouse: {self.mouse_status.battery_level}%")
            if self.headphone_status.is_connected and self.headphone_status.battery_level is not None:
                tooltip.append(f"Headphones: {self.headphone_status.battery_level}%")
            self.tray_icon.title = " | ".join(tooltip) if tooltip else "Battery Monitor - No devices"

    def monitor_loop(self):
        while self.running:
            try:
                self.update_device_status()
                self.update_tray()
                interval = self.config.get("update_interval", 300)
                if self.update_event.wait(timeout=interval):
                    self.update_event.clear()
            except Exception as e:
                if self.config.get("debug_mode"):
                    print(f"Error in monitor loop: {e}")
                time.sleep(5)

    def run(self):
        time.sleep(1)
        self.update_device_status()
        icon_image = self.create_icon()
        menu = self.create_menu()
        self.tray_icon = pystray.Icon(
            "BatteryMonitor",
            icon=icon_image,
            title="Battery Monitor",
            menu=menu
        )
        monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        monitor_thread.start()
        self.tray_icon.run()

class SettingsWindow:
    def __init__(self, monitor: BatteryMonitor):
        self.monitor = monitor
        self.window = tk.Toplevel()
        self.window.title("Battery Monitor Settings")
        self.window.geometry("520x500")
        self.window.resizable(False, False)
        self.window.attributes('-topmost', True)
        self.setup_ui()
        self.load_current_settings()
        self.window.update_idletasks()
        x = (self.window.winfo_screenwidth() - self.window.winfo_width()) // 2
        y = (self.window.winfo_screenheight() - self.window.winfo_height()) // 2
        self.window.geometry(f"+{x}+{y}")
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_ui(self):
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        ttk.Label(main_frame, text="Update Interval:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.interval_var = tk.StringVar()
        interval_combo = ttk.Combobox(main_frame, textvariable=self.interval_var, width=15)
        interval_combo['values'] = ('1 minute', '5 minutes', '10 minutes', '30 minutes', '1 hour')
        interval_combo.grid(row=0, column=1, sticky=tk.W, padx=(10, 0), pady=5)
        ttk.Label(main_frame, text="Icon Style:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.icon_style_var = tk.StringVar()
        icon_combo = ttk.Combobox(main_frame, textvariable=self.icon_style_var, width=15)
        icon_combo['values'] = ('Split View', 'Mouse Only', 'Headphones Only')
        icon_combo.grid(row=1, column=1, sticky=tk.W, padx=(10, 0), pady=5)
        self.autostart_var = tk.BooleanVar()
        ttk.Checkbutton(main_frame, text="Start with Windows", variable=self.autostart_var).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=5)
        self.debug_var = tk.BooleanVar()
        ttk.Checkbutton(main_frame, text="Debug Mode", variable=self.debug_var).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=5)
        colors_frame = ttk.LabelFrame(main_frame, text="Battery Colors", padding="5")
        colors_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=10)
        self.color_vars = {}
        color_labels = ["High (>50%)", "Medium (20-50%)", "Low (<20%)", "Charging", "Error/Disconnected"]
        color_keys = ["high", "medium", "low", "charging", "error"]
        for i, (label, key) in enumerate(zip(color_labels, color_keys)):
            ttk.Label(colors_frame, text=f"{label}:").grid(row=i, column=0, sticky=tk.W, pady=2)
            color_button = tk.Button(colors_frame, width=10, command=lambda k=key: self.choose_color(k))
            color_button.grid(row=i, column=1, sticky=tk.W, padx=(10, 0), pady=2)
            self.color_vars[key] = color_button
        status_frame = ttk.LabelFrame(main_frame, text="Device Status", padding="5")
        status_frame.grid(row=5, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=10)
        self.mouse_status_label = ttk.Label(status_frame, text="Mouse: Checking...")
        self.mouse_status_label.grid(row=0, column=0, sticky=tk.W, pady=2)
        self.headphone_status_label = ttk.Label(status_frame, text="Headphones: Checking...")
        self.headphone_status_label.grid(row=1, column=0, sticky=tk.W, pady=2)
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=6, column=0, columnspan=2, pady=20)
        ttk.Button(button_frame, text="Cancel", command=self.on_close).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Apply", command=self.apply_settings).pack(side=tk.LEFT, padx=5)
        self.update_device_status()

    def load_current_settings(self):
        config = self.monitor.config
        interval = config.get("update_interval", 300)
        interval_map = {60: "1 minute", 300: "5 minutes", 600: "10 minutes", 1800: "30 minutes", 3600: "1 hour"}
        self.interval_var.set(interval_map.get(interval, "5 minutes"))
        style = config.get("icon_style", "split")
        style_map = {"split": "Split View", "mouse_only": "Mouse Only", "headphone_only": "Headphones Only"}
        self.icon_style_var.set(style_map.get(style, "Split View"))
        self.autostart_var.set(config.get("autostart", True))
        self.debug_var.set(config.get("debug_mode", False))
        colors = config.get("colors", DEFAULT_CONFIG["colors"])
        for key, button in self.color_vars.items():
            color = colors.get(key, "#000000")
            button.configure(bg=color)

    def choose_color(self, color_key):
        current_color = self.monitor.config["colors"].get(color_key, "#000000")
        color = colorchooser.askcolor(initialcolor=current_color)[1]
        if color:
            self.color_vars[color_key].configure(bg=color)

    def apply_settings(self):
        try:
            interval_map = {"1 minute": 60, "5 minutes": 300, "10 minutes": 600, "30 minutes": 1800, "1 hour": 3600}
            self.monitor.config["update_interval"] = interval_map.get(self.interval_var.get(), 300)
            style_map = {"Split View": "split", "Mouse Only": "mouse_only", "Headphones Only": "headphone_only"}
            self.monitor.config["icon_style"] = style_map.get(self.icon_style_var.get(), "split")
            self.monitor.config["autostart"] = self.autostart_var.get()
            self.monitor.config["debug_mode"] = self.debug_var.get()
            for key, button in self.color_vars.items():
                self.monitor.config["colors"][key] = button.cget("bg")
            self.monitor.setup_autostart()
            self.monitor.force_update()
            self.monitor.save_config()
            messagebox.showinfo("Settings", "Settings applied successfully!", parent=self.window)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply settings: {e}", parent=self.window)

    def save_settings(self):
        self.apply_settings()
        self.monitor.save_config()
        self.on_close()

    def update_device_status(self):
        if self.monitor.mouse_status.is_connected:
            mouse_text = f"Mouse Connected: {self.monitor.mouse_status.name}"
            if self.monitor.mouse_status.battery_level is not None:
                mouse_text += f" ({self.monitor.mouse_status.battery_level}%)"
                if self.monitor.mouse_status.is_charging:
                    mouse_text += " - Charging"
                else:
                    mouse_text += " - Discharging"
        else:
            mouse_text = "Mouse: Not Connected"
        self.mouse_status_label.config(text=mouse_text)
        if self.monitor.headphone_status.is_connected:
            hp_text = f"Headphones Connected: {self.monitor.headphone_status.name}"
            if self.monitor.headphone_status.battery_level is not None:
                hp_text += f" ({self.monitor.headphone_status.battery_level}%)"
        else:
            hp_text = "Headphones: Not Connected"
        self.headphone_status_label.config(text=hp_text)
        self.window.after(2000, self.update_device_status)

    def on_close(self):
        if self.window:
            self.window.destroy()
        self.monitor.settings_window = None

if __name__ == "__main__":
    monitor = BatteryMonitor()
    tray_thread = threading.Thread(target=monitor.run, daemon=True)
    tray_thread.start()
    root.mainloop()