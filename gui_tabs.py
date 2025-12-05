import tkinter as tk
from tkinter import ttk
import threading
import time
import random
from pynput.mouse import Button, Controller
from pynput import keyboard
import ctypes
from ctypes import wintypes

# Windows SendInput helper for relative mouse movement (better for games)
def _win_send_mouse_move(dx, dy):
    try:
        user32 = ctypes.windll.user32
        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

        MOUSEEVENTF_MOVE = 0x0001

        mi = MOUSEINPUT(int(dx), int(dy), 0, MOUSEEVENTF_MOVE, 0, 0)
        inp = INPUT(0, mi)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        return True
    except Exception:
        return False

def _get_pixel_color(x, y):
    """Return (r,g,b) for screen pixel at (x,y) on Windows using GDI GetPixel."""
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        pixel = gdi32.GetPixel(hdc, int(x), int(y))
        user32.ReleaseDC(0, hdc)
        if pixel == -1:
            return None
        # pixel is a COLORREF (0x00bbggrr)
        r = pixel & 0x0000ff
        g = (pixel & 0x00ff00) >> 8
        b = (pixel & 0xff0000) >> 16
        try:
            logging.getLogger(__name__).debug("GetPixel at (%s,%s) -> raw=0x%08x r=%d g=%d b=%d", x, y, pixel, r, g, b)
        except Exception:
            pass
        return (r, g, b)
    except Exception:
        return None

def _get_cursor_pos():
    """Return current cursor position as (x,y) using Win32 GetCursorPos (avoids scaling mismatch)."""
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return (pt.x, pt.y)
        return None
    except Exception:
        return None
import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class TabbedGUI:
    """
    A GUI application with tabs, including a customizable autoclicker,
    button selection, hotkey, and a random click offset system.
    """
    def __init__(self, master):
        self.master = master
        self.logger = logging.getLogger(__name__)
        self.logger.info("Initializing TabbedGUI")
        master.title("Customizable Autoclicker")
        # Ensure the window is large enough for all controls
        master.geometry("550x400")
        
        # Register window close handler
        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # --- Autoclicker State Variables ---
        self.is_clicking = False
        self.click_thread = None
        self.mouse = Controller() 
        
        # --- Hotkey and Button Variables ---
        self.current_button = tk.StringVar(value="left")
        self.current_hotkey = tk.StringVar(value="f6")
        # Autoclicker keyboard substitution
        self.autoclick_use_keyboard_var = tk.IntVar(value=0)
        self.autoclick_key_var = tk.StringVar(value='')
        # Macro hotkeys
        self.macro_start_hotkey = tk.StringVar(value="f7")
        self.macro_stop_hotkey = tk.StringVar(value="f8")
        # Reaction Test hotkeys and settings
        self.reaction_start_hotkey = tk.StringVar(value="f9")
        self.reaction_stop_hotkey = tk.StringVar(value="f10")
        self.reaction_x_var = tk.StringVar(value="0")
        self.reaction_y_var = tk.StringVar(value="0")
        self.reaction_color_var = tk.StringVar(value="#000000")
        self.reaction_color_name_var = tk.StringVar(value='Green')
        self.reaction_use_exact_var = tk.IntVar(value=0)
        self.reaction_custom_hex = tk.StringVar(value='#00b962')
        self.reaction_tolerance_var = tk.IntVar(value=20)
        self.reaction_delay_var = tk.StringVar(value="0")
        self.reaction_cooldown_var = tk.StringVar(value="200")
        self.reaction_button = tk.StringVar(value="left")
        # Whether the reaction watcher should follow the current cursor position
        self.reaction_watch_cursor_var = tk.IntVar(value=0)
        # Reaction test keyboard substitution
        self.reaction_use_keyboard_var = tk.IntVar(value=0)
        self.reaction_key_var = tk.StringVar(value='')
        self._reaction_thread = None
        self._reaction_running = False
        self.is_setting_hotkey = False
        self.keyboard_listener = None
        
        # --- Click Duration and Max Clicks ---
        self.click_duration_var = tk.StringVar(value="50")
        self.max_clicks_var = tk.StringVar(value="0")
        self.click_count = 0
        
        # --- Offset Variables ---
        self.offset_max_var = tk.StringVar(value="50") 
        self.offset_enabled_var = tk.IntVar(value=0) 
        # --- Snapshot config for worker thread (plain Python types) ---
        self.cfg_interval = 0.1
        self.cfg_duration = 0.05
        self.cfg_max_clicks = 0
        self.cfg_button = 'left'
        self.cfg_offset_enabled = False
        self.cfg_offset_max = 0.05
        # Event to control worker thread sleep/wakeup
        self.stop_event = threading.Event()
        # Timing strategy: 'event' (background worker) or 'after' (tk.after mainloop)
        self.timing_strategy_var = tk.StringVar(value='after')
        # Logging level selection
        self.logging_level_var = tk.StringVar(value='DEBUG')
        # Dark mode toggle
        self.dark_mode_var = tk.IntVar(value=0)
        # Window behavior options
        self.always_on_top_var = tk.IntVar(value=1)
        self.toolwindow_var = tk.IntVar(value=0)
        # after() scheduled id for canceling when using after-mode
        self._after_id = None
        # Timestamp for CPS calculations
        self._click_start_time = None
        # Keep StringVars in sync with plain attributes to avoid tkinter access from worker
        try:
            self.interval_var.trace_add('write', lambda *args: self._update_interval())
            self.click_duration_var.trace_add('write', lambda *args: self._update_duration())
            self.max_clicks_var.trace_add('write', lambda *args: self._update_max_clicks())
            self.current_button.trace_add('write', lambda *args: self._update_button())
            self.offset_max_var.trace_add('write', lambda *args: self._update_offset_max())
            self.offset_enabled_var.trace_add('write', lambda *args: self._update_offset_enabled())
        except Exception:
            # Older tkinter versions may not support trace_add; fall back to trace
            try:
                self.interval_var.trace('w', lambda *args: self._update_interval())
                self.click_duration_var.trace('w', lambda *args: self._update_duration())
                self.max_clicks_var.trace('w', lambda *args: self._update_max_clicks())
                self.current_button.trace('w', lambda *args: self._update_button())
                self.offset_max_var.trace('w', lambda *args: self._update_offset_max())
                self.offset_enabled_var.trace('w', lambda *args: self._update_offset_enabled())
            except Exception:
                pass

        # Attach traces for new settings (with trace_add fallback)
        try:
            self.timing_strategy_var.trace_add('write', lambda *args: self._on_timing_strategy_changed())
            self.logging_level_var.trace_add('write', lambda *args: self._update_logging_level())
            self.always_on_top_var.trace_add('write', lambda *args: self._update_always_on_top())
            self.toolwindow_var.trace_add('write', lambda *args: self._update_toolwindow())
        except Exception:
            try:
                self.timing_strategy_var.trace('w', lambda *args: self._on_timing_strategy_changed())
                self.logging_level_var.trace('w', lambda *args: self._update_logging_level())
                try:
                    self.always_on_top_var.trace('w', lambda *args: self._update_always_on_top())
                    self.toolwindow_var.trace('w', lambda *args: self._update_toolwindow())
                except Exception:
                    pass
            except Exception:
                pass
        
        # --- Main GUI Setup ---
        # Top bar with pin toggle
        self.top_bar = ttk.Frame(master)
        self.top_bar.pack(fill='x', padx=10, pady=(10, 0))
        self.pin_button = ttk.Button(self.top_bar, text='📌' if self.always_on_top_var.get() else '📍', width=3, command=self._toggle_pin)
        self.pin_button.pack(side='right')

        self.notebook = ttk.Notebook(master)
        self.notebook.pack(expand=True, fill='both', padx=10, pady=10)

        self.create_autoclicker_tab()
        self.create_macro_tab()
        self.create_reaction_tab()
        self.create_settings_tab()
        # Apply theme (dark mode) to canvases and styles based on setting
        try:
            self._apply_dark_mode()
        except Exception:
            pass
        # Start the keyboard listener immediately
        try:
            self._start_hotkey_listener()
            self.logger.info("Hotkey listener started")
        except Exception as e:
            self.logger.exception("Failed to start hotkey listener: %s", e)
        # Try to make the process DPI aware so GetCursorPos/GetPixel map to physical pixels
        try:
            if hasattr(ctypes.windll.user32, 'SetProcessDPIAware'):
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    # --- GUI Setup Methods ---
    
    def create_autoclicker_tab(self):
        """
        Creates and populates the 'Autoclicker Tab' frame with controls.
        Uses grid layout management with scrolling support.
        """
        # Create main frame for the tab
        tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(tab_frame, text='Autoclicker Tab')
        
        # Create Canvas and Scrollbar for scrolling content
        canvas = tk.Canvas(tab_frame, highlightthickness=0, bg="white")
        # expose for theming
        self.autoclicker_canvas = canvas
        scrollbar = ttk.Scrollbar(tab_frame, orient='vertical', command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Enable mousewheel scrolling only when cursor is over this canvas
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        self.autoclicker_frame = scrollable_frame
        self.autoclicker_frame.padding = "20"
        
        # Configure grid column weights for better spacing
        self.autoclicker_frame.columnconfigure(0, weight=1)
        self.autoclicker_frame.columnconfigure(1, weight=1)
        self.autoclicker_frame.columnconfigure(2, weight=1)
        
        # Padding added programmatically
        for child in self.autoclicker_frame.winfo_children():
            child.grid_configure(padx=20, pady=5)
        
        r = 0 # row counter - start at 0

        # 1. Interval Input
        ttk.Label(self.autoclicker_frame, text="Click Interval (ms):").grid(row=r, column=0, padx=5, pady=5, sticky='w')
        self.interval_var = tk.StringVar(value="100")
        ttk.Entry(self.autoclicker_frame, textvariable=self.interval_var, width=10).grid(row=r, column=1, padx=5, pady=5, sticky='w')
        r += 1 

        # 2. Mouse Button Selection
        ttk.Label(self.autoclicker_frame, text="Mouse Button:").grid(row=r, column=0, padx=5, pady=5, sticky='w')
        self.button_combo = ttk.Combobox(self.autoclicker_frame,
                 textvariable=self.current_button,
                 values=("left", "right", "middle"),
                 width=10,
                 state="readonly")
        self.button_combo.current(0)  # Set to first item (left) by default
        self.button_combo.bind("<<ComboboxSelected>>", self._on_button_changed)
        self.button_combo.grid(row=r, column=1, padx=5, pady=5, sticky='w')
        r += 1

        # 3. Click Duration
        ttk.Label(self.autoclicker_frame, text="Click Duration (ms):").grid(row=r, column=0, padx=5, pady=5, sticky='w')
        ttk.Entry(self.autoclicker_frame, textvariable=self.click_duration_var, width=10).grid(row=r, column=1, padx=5, pady=5, sticky='w')
        r += 1
        
        # 4. Max Clicks (0 = infinite)
        ttk.Label(self.autoclicker_frame, text="Max Clicks (0=inf):").grid(row=r, column=0, padx=5, pady=5, sticky='w')
        ttk.Entry(self.autoclicker_frame, textvariable=self.max_clicks_var, width=10).grid(row=r, column=1, padx=5, pady=5, sticky='w')
        r += 1

        # 4b. Interval Offset (randomization)
        ttk.Label(self.autoclicker_frame, text="Interval Offset (±ms):").grid(row=r, column=0, padx=5, pady=5, sticky='w')
        offset_frame = ttk.Frame(self.autoclicker_frame)
        offset_frame.grid(row=r, column=1, padx=5, pady=5, sticky='w')
        self.offset_check = ttk.Checkbutton(offset_frame, text="Enabled", variable=self.offset_enabled_var)
        self.offset_check.pack(side='left', padx=2)
        ttk.Entry(offset_frame, textvariable=self.offset_max_var, width=8).pack(side='left', padx=2)
        r += 1
        
        # Offset description
        ttk.Label(self.autoclicker_frame, text="(e.g., 30ms offset on 100ms interval = 70–130ms clicks)", 
                  font=("TkDefaultFont", 8), foreground="gray").grid(row=r, column=0, columnspan=2, padx=5, pady=2, sticky='w')
        r += 1

        # 5. Start / Stop buttons
        self.start_button = ttk.Button(self.autoclicker_frame, text="Start", command=self.start_clicking)
        self.start_button.grid(row=r, column=0, padx=5, pady=10, sticky='w')
        self.stop_button = ttk.Button(self.autoclicker_frame, text="Stop", command=self.stop_clicking, state='disabled')
        self.stop_button.grid(row=r, column=1, padx=5, pady=10, sticky='w')
        r += 1

        # Status label: Clicks, CPS and Elapsed
        self.status_var = tk.StringVar(value="Clicks: 0 | CPS: 0.00 | Elapsed: 0ms")
        ttk.Label(self.autoclicker_frame, textvariable=self.status_var).grid(row=r, column=0, columnspan=2, padx=5, pady=5, sticky='w')
        # Reset stats button on the same logical group
        self.reset_stats_button = ttk.Button(self.autoclicker_frame, text="Reset Stats", command=self.reset_stats)
        self.reset_stats_button.grid(row=r, column=2, padx=5, pady=5, sticky='e')
        r += 1

        # Keyboard substitution: allow sending a keyboard key instead of/more than mouse click
        ttk.Checkbutton(self.autoclicker_frame, text='Use Keyboard Keys', variable=self.autoclick_use_keyboard_var).grid(row=r, column=0, sticky='w')
        kb_frame = ttk.Frame(self.autoclicker_frame)
        kb_frame.grid(row=r, column=1, columnspan=2, sticky='w')
        self.autoclick_key_label = ttk.Label(kb_frame, text=self.autoclick_key_var.get() or 'None', foreground='blue')
        self.autoclick_key_label.pack(side='left', padx=(0,6))
        ttk.Button(kb_frame, text='Set Key', command=self._bind_autoclick_key).pack(side='left')
        r += 1

    def _on_button_changed(self, event):
        """Callback when mouse button selection changes."""
        new_button = self.button_combo.get()
        self.current_button.set(new_button)  # Sync StringVar with combobox
        self.logger.info("Mouse button changed to: %s", new_button)

    # --- StringVar -> plain config update callbacks ---
    def _update_interval(self):
        try:
            v = float(self.interval_var.get())
            if v < 0:
                v = 1
            # Convert from milliseconds to seconds
            v = v / 1000.0
        except Exception:
            v = 0.1
        self.cfg_interval = v

    def _update_duration(self):
        try:
            v = float(self.click_duration_var.get())
            if v < 0:
                v = 0.0
            # Convert from milliseconds to seconds
            v = v / 1000.0
        except Exception:
            v = 0.05
        self.cfg_duration = v

    def _update_max_clicks(self):
        try:
            v = int(self.max_clicks_var.get())
            if v < 0:
                v = 0
        except Exception:
            v = 0
        self.cfg_max_clicks = v

    def _update_button(self):
        try:
            v = (self.current_button.get() or 'left').lower()
        except Exception:
            v = 'left'
        self.cfg_button = v

    def _update_offset_enabled(self):
        try:
            self.cfg_offset_enabled = bool(self.offset_enabled_var.get())
        except Exception:
            self.cfg_offset_enabled = False

    def _update_offset_max(self):
        try:
            v = float(self.offset_max_var.get())
            if v < 0:
                v = 0.0
            # Convert from milliseconds to seconds
            v = v / 1000.0
        except Exception:
            v = 0.0
        self.cfg_offset_max = v

    def on_closing(self):
        """Handle graceful shutdown when window is closed."""
        self.logger.info("Window close requested")
        if self.is_clicking:
            self.logger.info("Stopping autoclicker")
            self.is_clicking = False
        self.master.destroy()

    def create_settings_tab(self):
        """
        Settings tab with hotkey configuration and scrolling support.
        """
        # Create main frame for the tab
        tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(tab_frame, text='Settings')
        
        # Create Canvas and Scrollbar for scrolling content
        canvas = tk.Canvas(tab_frame, highlightthickness=0, bg="white")
        # expose for theming
        self.settings_canvas = canvas
        scrollbar = ttk.Scrollbar(tab_frame, orient='vertical', command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Enable mousewheel scrolling only when cursor is over this canvas
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        self.settings_frame = scrollable_frame
        
        # Hotkey configuration
        ttk.Label(self.settings_frame, text="Toggle Hotkey:").pack(anchor='w', padx=10, pady=5)
        
        hotkey_frame = ttk.Frame(self.settings_frame)
        hotkey_frame.pack(anchor='w', padx=10, pady=5)
        
        self.hotkey_label = ttk.Label(hotkey_frame, text=self.current_hotkey.get(), foreground="blue")
        self.hotkey_label.pack(side='left', padx=5)
        
        self.bind_button = ttk.Button(hotkey_frame, text="Set Hotkey", command=self._bind_hotkey)
        self.bind_button.pack(side='left', padx=5)
        
        ttk.Label(self.settings_frame, text="Press any key to bind, or close to cancel.", font=("TkDefaultFont", 9)).pack(anchor='w', padx=10, pady=5)

        # Timing strategy chooser
        ttk.Label(self.settings_frame, text="Timing Strategy:").pack(anchor='w', padx=10, pady=(10,2))
        strategy_frame = ttk.Frame(self.settings_frame)
        strategy_frame.pack(anchor='w', padx=10, pady=2)
        self.timing_combo = ttk.Combobox(strategy_frame, textvariable=self.timing_strategy_var,
                         values=("event", "after"), state='readonly', width=10)
        self.timing_combo.pack(side='left')

        # Logging level chooser
        ttk.Label(self.settings_frame, text="Logging Level:").pack(anchor='w', padx=10, pady=(8,2))
        log_frame = ttk.Frame(self.settings_frame)
        log_frame.pack(anchor='w', padx=10, pady=2)
        self.logging_combo = ttk.Combobox(log_frame, textvariable=self.logging_level_var,
                          values=("DEBUG", "INFO", "WARNING", "ERROR"), state='readonly', width=10)
        self.logging_combo.pack(side='left')
        # Dark mode toggle
        ttk.Checkbutton(self.settings_frame, text='Dark Mode', variable=self.dark_mode_var, command=self._apply_dark_mode).pack(anchor='w', padx=10, pady=(8,2))

        # Macro hotkey bindings
        ttk.Label(self.settings_frame, text="Macro Hotkeys:").pack(anchor='w', padx=10, pady=(10,2))
        mh_frame = ttk.Frame(self.settings_frame)
        mh_frame.pack(anchor='w', padx=10, pady=2)
        ttk.Label(mh_frame, text="Start Macro:").grid(row=0, column=0, sticky='w')
        self.macro_start_label = ttk.Label(mh_frame, text=self.macro_start_hotkey.get(), foreground='blue')
        self.macro_start_label.grid(row=0, column=1, padx=6)
        self.macro_start_bind_btn = ttk.Button(mh_frame, text='Set Start Hotkey', command=self._bind_macro_start_hotkey)
        self.macro_start_bind_btn.grid(row=0, column=2, padx=6)

        ttk.Label(mh_frame, text="Stop Macro:").grid(row=1, column=0, sticky='w')
        self.macro_stop_label = ttk.Label(mh_frame, text=self.macro_stop_hotkey.get(), foreground='blue')
        self.macro_stop_label.grid(row=1, column=1, padx=6)
        self.macro_stop_bind_btn = ttk.Button(mh_frame, text='Set Stop Hotkey', command=self._bind_macro_stop_hotkey)
        self.macro_stop_bind_btn.grid(row=1, column=2, padx=6)

        # Window behavior options (Always on Top / Tool Window)
        ttk.Label(self.settings_frame, text="Window Behavior:").pack(anchor='w', padx=10, pady=(10,2))
        wb_frame = ttk.Frame(self.settings_frame)
        wb_frame.pack(anchor='w', padx=10, pady=2)
        self.always_on_top_cb = ttk.Checkbutton(wb_frame, text='Always on Top', variable=self.always_on_top_var, command=self._update_always_on_top)
        self.always_on_top_cb.grid(row=0, column=0, sticky='w')
        self.toolwindow_cb = ttk.Checkbutton(wb_frame, text='Tool Window (compact frame)', variable=self.toolwindow_var, command=self._update_toolwindow)
        self.toolwindow_cb.grid(row=0, column=1, sticky='w', padx=8)


    def _start_hotkey_listener(self):
        """Start a single global hotkey listener that toggles autoclicker reliably."""
        if hasattr(self, 'keyboard_listener') and self.keyboard_listener is not None:
            return  # Already running
        from pynput import keyboard
        self._hotkey_last_time = 0
        def on_press(key):
            try:
                configured_key = (self.current_hotkey.get() or "f6").lower()
                # Extract key name
                key_name = ""
                if hasattr(key, 'name'):
                    key_name = key.name.lower()
                elif hasattr(key, 'char'):
                    key_name = key.char.lower() if key.char else ""
                else:
                    key_name = str(key).lower()
                # Debounce: ignore if pressed within 0.3s
                import time
                now = time.time()
                if now - self._hotkey_last_time < 0.3:
                    return
                if key_name == configured_key:
                    self._hotkey_last_time = now
                    try:
                        # Prefer invoking the Start/Stop buttons so GUI path is used
                        if not getattr(self, 'is_clicking', False):
                            if hasattr(self, 'start_button'):
                                self.start_button.invoke()
                            else:
                                self.start_clicking()
                        else:
                            if hasattr(self, 'stop_button'):
                                self.stop_button.invoke()
                            else:
                                self.stop_clicking()
                    except Exception:
                        # Fallback to direct calls
                        if self.is_clicking:
                            self.stop_clicking()
                        else:
                            self.start_clicking()
                    self.logger.info(f"Hotkey '{configured_key}' pressed: toggled autoclicker")
                    return
                # Macro start/stop handling
                macro_start = (self.macro_start_hotkey.get() or 'f7').lower()
                macro_stop = (self.macro_stop_hotkey.get() or 'f8').lower()
                # Reaction test start/stop handling
                reaction_start = (self.reaction_start_hotkey.get() or 'f9').lower()
                reaction_stop = (self.reaction_stop_hotkey.get() or 'f10').lower()
                if key_name == macro_start:
                    self._hotkey_last_time = now
                    if not getattr(self, '_macro_playing', False):
                        name = getattr(self, 'selected_macro_name', None)
                        if not name and len(self.macros) > 0:
                            name = next(iter(self.macros))
                        if name:
                            # start macro playback
                            self.play_macro(name)
                            self.logger.info(f"Macro start hotkey pressed: starting macro '{name}'")
                    return
                if key_name == reaction_start:
                    self._hotkey_last_time = now
                    try:
                        if not getattr(self, '_reaction_running', False):
                            self.start_reaction_test()
                            self.logger.info(f"Reaction test start hotkey pressed: starting")
                    except Exception:
                        pass
                    return
                if key_name == reaction_stop:
                    self._hotkey_last_time = now
                    try:
                        if getattr(self, '_reaction_running', False):
                            self.stop_reaction_test()
                            self.logger.info("Reaction test stop hotkey pressed: stopping")
                    except Exception:
                        pass
                    return
                if key_name == macro_stop:
                    self._hotkey_last_time = now
                    if getattr(self, '_macro_playing', False):
                        self.stop_macro_play()
                        self.logger.info("Macro stop hotkey pressed: stopping macro")
                    return
            except Exception as e:
                self.logger.exception("Error in hotkey handler: %s", e)
        self.keyboard_listener = keyboard.Listener(on_press=on_press)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()
        self.logger.info(f"Hotkey listener started (hotkey={self.current_hotkey.get()})")

    def _update_always_on_top(self):
        try:
            val = bool(self.always_on_top_var.get())
            try:
                self.master.attributes('-topmost', val)
            except Exception:
                pass
            self.logger.info('Always on Top set to %s', val)
        except Exception:
            pass

    def _toggle_pin(self):
        """Toggle the pin (always-on-top) from the pin button."""
        try:
            current = bool(self.always_on_top_var.get())
            new = not current
            self.always_on_top_var.set(1 if new else 0)
            self._update_always_on_top()
            # update button icon
            try:
                if new:
                    self.pin_button.config(text='📌')
                else:
                    self.pin_button.config(text='📍')
            except Exception:
                pass
        except Exception:
            pass

    def _update_toolwindow(self):
        try:
            val = bool(self.toolwindow_var.get())
            try:
                # On Windows, -toolwindow makes the window a tool window (smaller titlebar)
                self.master.attributes('-toolwindow', val)
            except Exception:
                pass
            self.logger.info('Toolwindow set to %s', val)
        except Exception:
            pass

    def _stop_hotkey_listener(self):
        """Stop the global hotkey listener."""
        if hasattr(self, 'keyboard_listener') and self.keyboard_listener is not None:
            try:
                self.keyboard_listener.stop()
            except Exception:
                pass
            self.keyboard_listener = None

    def _bind_hotkey(self):
        """Bind a new hotkey by listening for a single key press."""
        self.is_setting_hotkey = True
        self.bind_button.config(state='disabled', text='Listening...')
        from pynput import keyboard
        def on_press(key):
            if not self.is_setting_hotkey:
                return False
            try:
                key_name = ""
                if hasattr(key, 'name'):
                    key_name = key.name.lower()
                elif hasattr(key, 'char'):
                    key_name = key.char.lower() if key.char else ""
                else:
                    key_name = str(key).lower()
                self.current_hotkey.set(key_name)
                self.hotkey_label.config(text=key_name)
                self.logger.info(f"Hotkey changed to: {key_name}")
                self.is_setting_hotkey = False
                self.bind_button.config(state='normal', text='Set Hotkey')
                # Restart listener to use new hotkey
                self._stop_hotkey_listener()
                self._start_hotkey_listener()
                return False
            except Exception as e:
                self.logger.exception("Error capturing hotkey: %s", e)
                return False
        bind_listener = keyboard.Listener(on_press=on_press)
        bind_listener.daemon = True
        bind_listener.start()

    def _bind_macro_start_hotkey(self):
        """Bind the macro start hotkey by capturing one key press."""
        self.is_setting_hotkey = True
        self.macro_start_bind_btn.config(state='disabled', text='Listening...')
        from pynput import keyboard
        def on_press(key):
            if not self.is_setting_hotkey:
                return False
            try:
                key_name = ''
                if hasattr(key, 'name'):
                    key_name = key.name.lower()
                elif hasattr(key, 'char'):
                    key_name = key.char.lower() if key.char else ''
                else:
                    key_name = str(key).lower()
                self.macro_start_hotkey.set(key_name)
                self.macro_start_label.config(text=key_name)
                self.logger.info(f"Macro start hotkey changed to: {key_name}")
                self.is_setting_hotkey = False
                self.macro_start_bind_btn.config(state='normal', text='Set Start Hotkey')
                # restart listener
                self._stop_hotkey_listener()
                self._start_hotkey_listener()
                return False
            except Exception as e:
                self.logger.exception('Error capturing macro start hotkey: %s', e)
                return False
        bind_listener = keyboard.Listener(on_press=on_press)
        bind_listener.daemon = True
        bind_listener.start()

    def _bind_macro_stop_hotkey(self):
        """Bind the macro stop hotkey by capturing one key press."""
        self.is_setting_hotkey = True
        self.macro_stop_bind_btn.config(state='disabled', text='Listening...')
        from pynput import keyboard
        def on_press(key):
            if not self.is_setting_hotkey:
                return False
            try:
                key_name = ''
                if hasattr(key, 'name'):
                    key_name = key.name.lower()
                elif hasattr(key, 'char'):
                    key_name = key.char.lower() if key.char else ''
                else:
                    key_name = str(key).lower()
                self.macro_stop_hotkey.set(key_name)
                self.macro_stop_label.config(text=key_name)
                self.logger.info(f"Macro stop hotkey changed to: {key_name}")
                self.is_setting_hotkey = False
                self.macro_stop_bind_btn.config(state='normal', text='Set Stop Hotkey')
                # restart listener
                self._stop_hotkey_listener()
                self._start_hotkey_listener()
                return False
            except Exception as e:
                self.logger.exception('Error capturing macro stop hotkey: %s', e)
                return False
        bind_listener = keyboard.Listener(on_press=on_press)
        bind_listener.daemon = True
        bind_listener.start()

    def start_clicking(self):
        """Start the autoclicker using the selected timing strategy."""
        if self.is_clicking:
            return

        # Ensure plain-configs are up-to-date
        self._update_interval()
        self._update_duration()
        self._update_max_clicks()
        self._update_button()
        self._update_offset_enabled()
        self._update_offset_max()

        strategy = (self.timing_strategy_var.get() or 'event').lower()
        self.is_clicking = True
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        self.click_count = 0

        if strategy == 'after':
            self.logger.info("Autoclicker started (after-mode)")
            self._start_clicking_after_mode()
        else:
            # event worker
            self.stop_event.clear()
            self.logger.info("Autoclicker started (worker thread)")
            self.click_thread = threading.Thread(target=self._click_worker_event, daemon=True)
            self.click_thread.start()
        # record start time for CPS calculations
        try:
            self._click_start_time = time.time()
        except Exception:
            self._click_start_time = None

    def stop_clicking(self):
        """Stop the autoclicking worker thread."""
        if not self.is_clicking:
            return
        # If after-mode is active, cancel scheduled callbacks
        strategy = (self.timing_strategy_var.get() or 'event').lower()
        if strategy == 'after':
            self.is_clicking = False
            try:
                if self._after_id is not None:
                    self.master.after_cancel(self._after_id)
                    self._after_id = None
            except Exception:
                pass
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            # update final status
            try:
                if getattr(self, '_click_start_time', None):
                    elapsed = time.time() - self._click_start_time
                    cps = (self.click_count / elapsed) if elapsed > 0 else 0.0
                else:
                    elapsed = 0.0
                    cps = 0.0
                self.status_var.set(f"Clicks: {self.click_count} | CPS: {cps:.2f} | Elapsed: {elapsed*1000:.0f}ms")
            except Exception:
                pass
            self.logger.info("Autoclicker stopped (after-mode) clicks=%d", self.click_count)
            return

        # Otherwise stop worker thread
        self.is_clicking = False
        self.stop_event.set()
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        # update final status
        try:
            if getattr(self, '_click_start_time', None):
                elapsed = time.time() - self._click_start_time
                cps = (self.click_count / elapsed) if elapsed > 0 else 0.0
            else:
                elapsed = 0.0
                cps = 0.0
            # schedule update on main thread
            try:
                self.master.after(0, lambda c=self.click_count, r=cps, e=elapsed: self.status_var.set(f"Clicks: {c} | CPS: {r:.2f} | Elapsed: {e*1000:.0f}ms"))
            except Exception:
                self.status_var.set(f"Clicks: {self.click_count} | CPS: {cps:.2f} | Elapsed: {elapsed*1000:.0f}ms")
        except Exception:
            pass
        self.logger.info("Autoclicker stop requested (worker) clicks=%d", self.click_count)

    def _schedule_next_click(self):
        # This method was replaced by the event-based worker. Kept for compatibility if referenced.
        return

    def _release_and_schedule(self, btn, interval):
        # Deprecated: release handled in worker thread now.
        try:
            self.mouse.release(btn)
        except Exception as e:
            self.logger.exception("Error releasing button: %s", e)

    def _click_worker_event(self):
        """Worker thread that performs clicks using threading.Event.wait for timing.

        Reads configuration from plain Python attributes updated by StringVar traces.
        """
        self.logger.debug("Worker thread started")
        start_time = time.time()

        while not self.stop_event.is_set():
            # Snapshot config for this click
            interval = max(0.001, float(self.cfg_interval))
            duration = max(0.0, float(self.cfg_duration))
            max_clicks = int(self.cfg_max_clicks)
            btn_name = (self.cfg_button or 'left').lower()
            offset_enabled = bool(self.cfg_offset_enabled)
            offset_max = float(self.cfg_offset_max)

            # Apply random offset if enabled
            if offset_enabled and offset_max > 0:
                interval = max(0.001, interval + random.uniform(-offset_max, offset_max))

            # Map button
            if btn_name == 'right':
                btn = Button.right
            elif btn_name == 'middle':
                btn = Button.middle
            else:
                btn = Button.left

            # Check max clicks
            if max_clicks > 0 and self.click_count >= max_clicks:
                self.logger.info("Max clicks reached in worker (%d)", max_clicks)
                break

            try:
                # Press
                if self.click_count < 3:
                    self.logger.info("Worker click %d: btn=%s dur=%.3f int=%.3f", self.click_count+1, btn_name, duration, interval)
                # If configured, send a keyboard key instead of mouse click
                use_kb = getattr(self, 'autoclick_use_keyboard_var', None) and self.autoclick_use_keyboard_var.get()
                kb_key = (self.autoclick_key_var.get() or '').strip() if use_kb else ''
                if use_kb and kb_key:
                    try:
                        from pynput.keyboard import Controller as KController, Key
                        kctl = KController()
                        if hasattr(Key, kb_key):
                            kctl.press(getattr(Key, kb_key))
                        else:
                            kctl.press(kb_key)
                    except Exception:
                        try:
                            kctl.press(kb_key)
                        except Exception:
                            pass
                else:
                    self.mouse.press(btn)

                # Wait for duration or until stop
                self.stop_event.wait(duration)

                # Release (keyboard or mouse)
                if use_kb and kb_key:
                    try:
                        from pynput.keyboard import Controller as KController, Key
                        kctl = KController()
                        if hasattr(Key, kb_key):
                            kctl.release(getattr(Key, kb_key))
                        else:
                            kctl.release(kb_key)
                    except Exception:
                        try:
                            kctl.release(kb_key)
                        except Exception:
                            pass
                else:
                    try:
                        self.mouse.release(btn)
                    except Exception:
                        pass

                self.click_count += 1

                # Update status label on GUI thread (compute elapsed now)
                try:
                    if getattr(self, '_click_start_time', None):
                        elapsed = time.time() - self._click_start_time
                        cps = (self.click_count / elapsed) if elapsed > 0 else 0.0
                    else:
                        elapsed = 0.0
                        cps = 0.0
                    # schedule update with computed values (convert elapsed to ms)
                    self.master.after(0, lambda c=self.click_count, r=cps, e=elapsed: self.status_var.set(f"Clicks: {c} | CPS: {r:.2f} | Elapsed: {e*1000:.0f}ms"))
                except Exception:
                    pass

                # After click hook: update GUI occasionally (guarded)
                if self.click_count % 10 == 0:
                    try:
                        self.master.after(0, lambda: None)
                    except Exception:
                        # mainloop may not be running in tests; ignore
                        pass

                # Wait for interval or until stop
                self.stop_event.wait(interval)

            except Exception as e:
                self.logger.exception("Error in worker click loop: %s", e)
                break

        elapsed = time.time() - start_time
        actual_cps = (self.click_count / elapsed) if elapsed > 0 else 0
        self.logger.info("Worker exiting: clicks=%d elapsed=%.2f actual_cps=%.2f", self.click_count, elapsed, actual_cps)
        # Ensure buttons reset on GUI thread
        try:
            self.master.after(0, lambda: self.start_button.config(state='normal'))
            self.master.after(0, lambda: self.stop_button.config(state='disabled'))
        except Exception:
            pass

    # --- after() based clicking (mainloop) ---
    def _start_clicking_after_mode(self):
        """Begin clicking using tkinter's after() scheduling in the main thread."""
        # Snapshot config
        self._update_interval()
        self._update_duration()
        self._update_max_clicks()
        self._update_button()
        self._update_offset_enabled()
        self._update_offset_max()

        # Start immediately
        try:
            # Use after(0) to start without blocking
            self._after_id = self.master.after(0, self._after_click_cycle)
        except Exception as e:
            self.logger.exception("Failed to start after-mode clicking: %s", e)
            self.is_clicking = False
        # set start time for CPS
        try:
            self._click_start_time = time.time()
        except Exception:
            self._click_start_time = None

    def _after_click_cycle(self):
        """Unified click cycle using pynput click() method."""
        if not self.is_clicking:
            return

        # Snapshot configs
        interval = max(0.001, float(self.cfg_interval))
        duration = max(0.0, float(self.cfg_duration))
        max_clicks = int(self.cfg_max_clicks)
        btn_name = (self.cfg_button or 'left').lower()
        offset_enabled = bool(self.cfg_offset_enabled)
        offset_max = float(self.cfg_offset_max)

        # Apply random offset if enabled
        if offset_enabled and offset_max > 0:
            interval = max(0.001, interval + random.uniform(-offset_max, offset_max))

        # Map button
        if btn_name == 'right':
            btn = Button.right
        elif btn_name == 'middle':
            btn = Button.middle
        else:
            btn = Button.left

        # Check max clicks before clicking
        if max_clicks > 0 and self.click_count >= max_clicks:
            self.is_clicking = False
            self.logger.info("Max clicks reached (after-mode) %d", max_clicks)
            return

        try:
            # Perform the click
            if duration > 0:
                # Manual press/release with duration
                use_kb = getattr(self, 'autoclick_use_keyboard_var', None) and self.autoclick_use_keyboard_var.get()
                kb_key = (self.autoclick_key_var.get() or '').strip() if use_kb else ''
                if use_kb and kb_key:
                    try:
                        from pynput.keyboard import Controller as KController, Key
                        kctl = KController()
                        if hasattr(Key, kb_key):
                            kctl.press(getattr(Key, kb_key))
                        else:
                            kctl.press(kb_key)
                    except Exception:
                        try:
                            kctl.press(kb_key)
                        except Exception:
                            pass
                else:
                    self.mouse.press(btn)
                if self.click_count < 3:
                    self.logger.debug("After-mode press: btn=%s count=%d interval=%.3fs duration=%.3fs", btn_name, self.click_count + 1, interval, duration)
                duration_ms = int(round(duration * 1000))
                self._after_id = self.master.after(duration_ms, lambda b=btn, i=interval: self._after_release_and_schedule(b, i))
            else:
                # Instant click using click() method or keyboard substitute
                use_kb = getattr(self, 'autoclick_use_keyboard_var', None) and self.autoclick_use_keyboard_var.get()
                kb_key = (self.autoclick_key_var.get() or '').strip() if use_kb else ''
                if use_kb and kb_key:
                    try:
                        from pynput.keyboard import Controller as KController, Key
                        kctl = KController()
                        if hasattr(Key, kb_key):
                            kctl.press(getattr(Key, kb_key))
                            kctl.release(getattr(Key, kb_key))
                        else:
                            kctl.press(kb_key)
                            kctl.release(kb_key)
                    except Exception:
                        try:
                            kctl.press(kb_key); kctl.release(kb_key)
                        except Exception:
                            pass
                    self.click_count += 1
                else:
                    # Mouse instant click
                    self.mouse.click(btn)
                    self.click_count += 1
                    if self.click_count < 3:
                        self.logger.debug("After-mode instant click: btn=%s count=%d interval=%.3fs", btn_name, self.click_count, interval)
                
                # Update status
                try:
                    if getattr(self, '_click_start_time', None):
                        elapsed = time.time() - self._click_start_time
                        cps = (self.click_count / elapsed) if elapsed > 0 else 0.0
                    else:
                        elapsed = 0.0
                        cps = 0.0
                    self.status_var.set(f"Clicks: {self.click_count} | CPS: {cps:.2f} | Elapsed: {elapsed*1000:.0f}ms")
                except Exception:
                    pass
                
                # Check max clicks
                if max_clicks > 0 and self.click_count >= max_clicks:
                    self.is_clicking = False
                    self.logger.info("Max clicks reached (after-mode) %d", max_clicks)
                    return
                
                # Schedule next click
                if self.is_clicking:
                    interval_ms = int(round(interval * 1000))
                    self._after_id = self.master.after(interval_ms, self._after_click_cycle)
        except Exception as e:
            self.logger.exception("Error in after-mode click cycle: %s", e)
            self.is_clicking = False

    def _after_release_and_schedule(self, btn, interval):
        """Release button and schedule next click after interval."""
        try:
            # Release (mouse or keyboard substitution)
            use_kb = getattr(self, 'autoclick_use_keyboard_var', None) and self.autoclick_use_keyboard_var.get()
            kb_key = (self.autoclick_key_var.get() or '').strip() if use_kb else ''
            if use_kb and kb_key:
                try:
                    from pynput.keyboard import Controller as KController, Key
                    kctl = KController()
                    if hasattr(Key, kb_key):
                        kctl.release(getattr(Key, kb_key))
                    else:
                        kctl.release(kb_key)
                except Exception:
                    try:
                        kctl.release(kb_key)
                    except Exception:
                        pass
            else:
                self.mouse.release(btn)
            self.click_count += 1
            self.logger.debug("After-mode release: count=%d", self.click_count)

            # Update status label
            try:
                if getattr(self, '_click_start_time', None):
                    elapsed = time.time() - self._click_start_time
                    cps = (self.click_count / elapsed) if elapsed > 0 else 0.0
                else:
                    elapsed = 0.0
                    cps = 0.0
                self.status_var.set(f"Clicks: {self.click_count} | CPS: {cps:.2f} | Elapsed: {elapsed*1000:.0f}ms")
            except Exception:
                pass

            # Check max clicks
            max_clicks = int(self.cfg_max_clicks)
            if max_clicks > 0 and self.click_count >= max_clicks:
                self.is_clicking = False
                self.logger.info("Max clicks reached (after-mode) %d", max_clicks)
                return

            # Schedule next click after interval
            if self.is_clicking:
                interval_ms = int(round(interval * 1000))
                self._after_id = self.master.after(interval_ms, self._after_click_cycle)
        except Exception as e:
            self.logger.exception("Error in after-mode release/schedule: %s", e)
            self.is_clicking = False

    def _on_timing_strategy_changed(self):
        new = (self.timing_strategy_var.get() or 'event').lower()
        self.logger.info("Timing strategy changed to: %s", new)
        # If currently clicking, restart using new strategy
        if self.is_clicking:
            # Stop current clicking and restart under new mode
            self.stop_clicking()
            # small delay to ensure stop
            time.sleep(0.05)
            self.start_clicking()

    def reset_stats(self):
        """Reset click statistics: zero click count and restart start time."""
        try:
            self.click_count = 0
            self._click_start_time = time.time()
            # update status immediately
            try:
                self.status_var.set("Clicks: 0 | CPS: 0.00 | Elapsed: 0ms")
            except Exception:
                pass
            self.logger.info("Stats reset by user")
        except Exception as e:
            self.logger.exception("Failed to reset stats: %s", e)

    def _update_logging_level(self):
        lvl = (self.logging_level_var.get() or 'DEBUG').upper()
        mapping = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
        }
        level = mapping.get(lvl, logging.DEBUG)
        try:
            logging.getLogger().setLevel(level)
            self.logger.info("Logging level set to %s", lvl)
        except Exception:
            pass

    def _apply_dark_mode(self):
        """Apply a simple dark/light theme across the app."""
        try:
            enabled = bool(self.dark_mode_var.get())
            style = ttk.Style()
            try:
                style.theme_use('clam')
            except Exception:
                pass
            if enabled:
                # Dark mode: dark background, white text, gray fields
                bg = '#2b2b2b'
                fg = '#ffffff'
                field = '#454545'
                field_fg = '#ffffff'
                active_bg = '#505357'
                tab_bg = '#1e1e1e'
            else:
                # Light mode: white background, black text, white fields
                bg = 'white'
                fg = 'black'
                field = 'white'
                field_fg = 'black'
                active_bg = '#e6e6e6'
                tab_bg = 'white'
            # Generic styling
            style.configure('.', background=bg, foreground=fg)
            style.configure('TLabel', background=bg, foreground=fg)
            style.configure('TFrame', background=bg, foreground=fg)
            style.configure('TButton', background=field, foreground=field_fg)
            style.configure('TEntry', fieldbackground=field, foreground=field_fg, background=bg)
            style.configure('TCombobox', fieldbackground=field, foreground=field_fg, background=bg)
            style.configure('TCheckbutton', background=bg, foreground=fg)
            style.configure('TScale', background=bg)
            style.configure('TListbox', background=field, foreground=field_fg)
            # Configure notebook tabs with darker background for selected tab
            style.configure('TNotebook', background=bg, borderwidth=0)
            style.configure('TNotebook.Tab', background=field, foreground=field_fg, padding=[10, 5])
            style.map('TNotebook.Tab', background=[('selected', tab_bg)], foreground=[('selected', fg)])
            style.map('TButton', background=[('active', active_bg)])
            style.map('TEntry', fieldbackground=[('readonly', field)])
            style.map('TCombobox', fieldbackground=[('readonly', field)])
            # Update canvas and frame backgrounds if available
            for attr in ('autoclicker_canvas', 'macro_canvas', 'reaction_canvas', 'settings_canvas'):
                try:
                    c = getattr(self, attr, None)
                    if c:
                        c.configure(bg=bg)
                except Exception:
                    pass
            # Update scrollable frames (which are nested in canvases)
            for attr in ('autoclicker_frame', 'macro_frame', 'reaction_frame', 'settings_frame'):
                try:
                    f = getattr(self, attr, None)
                    if f:
                        f.configure(background=bg)
                except Exception:
                    pass
            try:
                self.master.configure(bg=bg)
            except Exception:
                pass
            # Update top bar
            try:
                self.top_bar.configure(background=bg)
            except Exception:
                pass
        except Exception:
            pass

    def create_macro_tab(self):
        """Creates and populates the 'Macro Tab' for recording and running macros with scrolling support."""
        # Create main frame for the tab
        tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(tab_frame, text='Macro Tab')
        
        # Create Canvas and Scrollbar for scrolling content
        canvas = tk.Canvas(tab_frame, highlightthickness=0, bg="white")
        # expose for theming
        self.macro_canvas = canvas
        scrollbar = ttk.Scrollbar(tab_frame, orient='vertical', command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Enable mousewheel scrolling only when cursor is over this canvas
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        
        self.macro_frame = scrollable_frame

        # Macro controls
        control_frame = ttk.Frame(self.macro_frame)
        control_frame.pack(fill='x', pady=5, padx=20)

        self.record_button = ttk.Button(control_frame, text="Record", command=self.start_macro_record)
        self.record_button.pack(side='left', padx=5)
        self.macro_record_stop_button = ttk.Button(control_frame, text="Stop", command=self.stop_macro_record, state='disabled')
        self.macro_record_stop_button.pack(side='left', padx=5)
        self.play_button = ttk.Button(control_frame, text="Play", command=self.play_macro, state='disabled')
        self.play_button.pack(side='left', padx=5)
        self.delete_button = ttk.Button(control_frame, text="Delete", command=self.delete_macro, state='disabled')
        self.delete_button.pack(side='left', padx=5)

        # Macro list
        ttk.Label(self.macro_frame, text="Saved Macros:").pack(anchor='w', pady=(10,0))
        self.macro_listbox = tk.Listbox(self.macro_frame, height=8)
        self.macro_listbox.pack(fill='x', pady=5)
        self.macro_listbox.bind('<<ListboxSelect>>', self.on_macro_select)

        # Loop toggle for selected macro
        self.macro_loop_var = tk.IntVar(value=0)
        self.macro_loop_cb = ttk.Checkbutton(self.macro_frame, text='Loop macro', variable=self.macro_loop_var, command=self._update_macro_loop, state='disabled')
        self.macro_loop_cb.pack(anchor='w', pady=5)

        # Macro storage
        self.macros = {}  # name -> {events: [...], looped: bool}
        self.current_macro = []
        self.selected_macro_name = None

    def create_reaction_tab(self):
        """Create the Reaction Test Tab: choose pixel, color, tolerance, delays, and keybinds."""
        tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(tab_frame, text='Reaction Test')

        canvas = tk.Canvas(tab_frame, highlightthickness=0, bg='white')
        # expose for theming
        self.reaction_canvas = canvas
        scrollbar = ttk.Scrollbar(tab_frame, orient='vertical', command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # Enable mousewheel scrolling only when cursor over this canvas
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        self.reaction_frame = scrollable_frame

        # Location controls
        loc_frame = ttk.Frame(self.reaction_frame)
        loc_frame.pack(fill='x', padx=10, pady=6)
        ttk.Label(loc_frame, text='Watch X:').grid(row=0, column=0, sticky='w')
        ttk.Entry(loc_frame, textvariable=self.reaction_x_var, width=8).grid(row=0, column=1, padx=4)
        ttk.Label(loc_frame, text='Y:').grid(row=0, column=2, sticky='w')
        ttk.Entry(loc_frame, textvariable=self.reaction_y_var, width=8).grid(row=0, column=3, padx=4)
        ttk.Button(loc_frame, text='Sample Cursor', command=self._sample_pixel_at_cursor).grid(row=0, column=4, padx=6)
        # Watch Cursor toggle: when enabled, the reaction loop will sample the current cursor position
        ttk.Checkbutton(loc_frame, text='Watch Cursor', variable=self.reaction_watch_cursor_var).grid(row=0, column=5, padx=6)

        # Color and tolerance
        color_frame = ttk.Frame(self.reaction_frame)
        color_frame.pack(fill='x', padx=10, pady=6)
        ttk.Label(color_frame, text='Target Color:').grid(row=0, column=0, sticky='w')
        self.color_preview = ttk.Label(color_frame, text='      ', background=self.reaction_color_var.get())
        self.color_preview.grid(row=0, column=1, padx=6)
        # Base color selection (friendly names)
        base_colors = ('Green','Red','Blue','Purple','Yellow','Orange','Black','White')
        self.color_combo = ttk.Combobox(color_frame, textvariable=self.reaction_color_name_var, values=base_colors, state='readonly', width=12)
        self.color_combo.grid(row=0, column=2, padx=4)
        ttk.Button(color_frame, text='Sample Color', command=self._sample_color_at_coords).grid(row=0, column=3, padx=6)

        # Exact color toggle and hex entry
        self.exact_cb = ttk.Checkbutton(color_frame, text='Use Exact Color', variable=self.reaction_use_exact_var)
        self.exact_cb.grid(row=1, column=0, sticky='w', pady=6)
        ttk.Entry(color_frame, textvariable=self.reaction_custom_hex, width=12).grid(row=1, column=1, padx=4)

        ttk.Label(color_frame, text='Tolerance:').grid(row=2, column=0, sticky='w', pady=6)
        ttk.Scale(color_frame, from_=0, to=255, orient='horizontal', variable=self.reaction_tolerance_var).grid(row=2, column=1, columnspan=3, sticky='we', padx=4)

        # Action options
        action_frame = ttk.Frame(self.reaction_frame)
        action_frame.pack(fill='x', padx=10, pady=6)
        ttk.Label(action_frame, text='Delay (ms):').grid(row=0, column=0, sticky='w')
        ttk.Entry(action_frame, textvariable=self.reaction_delay_var, width=8).grid(row=0, column=1, padx=4)
        ttk.Label(action_frame, text='Cooldown (ms):').grid(row=0, column=2, sticky='w')
        ttk.Entry(action_frame, textvariable=self.reaction_cooldown_var, width=8).grid(row=0, column=3, padx=4)
        ttk.Label(action_frame, text='Button:').grid(row=1, column=0, sticky='w', pady=6)
        ttk.Combobox(action_frame, textvariable=self.reaction_button, values=('left','right','middle'), state='readonly', width=8).grid(row=1, column=1, padx=4)
        # Reaction keyboard substitution controls
        ttk.Checkbutton(action_frame, text='Use Keyboard Keys', variable=self.reaction_use_keyboard_var).grid(row=2, column=0, sticky='w', pady=6)
        kb2 = ttk.Frame(action_frame)
        kb2.grid(row=2, column=1, columnspan=3, sticky='w')
        self.reaction_key_label = ttk.Label(kb2, text=self.reaction_key_var.get() or 'None', foreground='blue')
        self.reaction_key_label.pack(side='left', padx=(0,6))
        ttk.Button(kb2, text='Set Key', command=self._bind_reaction_key).pack(side='left')

        # Start/Stop and keybinds
        ctrl_frame = ttk.Frame(self.reaction_frame)
        ctrl_frame.pack(fill='x', padx=10, pady=8)
        # Top row: Start / Stop buttons
        btn_row = ttk.Frame(ctrl_frame)
        btn_row.pack(fill='x')
        self.reaction_start_btn = ttk.Button(btn_row, text='Start', command=self.start_reaction_test)
        self.reaction_start_btn.pack(side='left', padx=6)
        self.reaction_stop_btn = ttk.Button(btn_row, text='Stop', command=self.stop_reaction_test, state='disabled')
        self.reaction_stop_btn.pack(side='left', padx=6)

        # Bottom row: hotkey controls placed under Start and Stop respectively
        hk_row = ttk.Frame(ctrl_frame)
        hk_row.pack(fill='x', pady=(6,0))
        # Left column: Start hotkey
        hk_left = ttk.Frame(hk_row)
        hk_left.pack(side='left', anchor='nw')
        ttk.Label(hk_left, text='Start Hotkey:').pack(anchor='w')
        hk_start_sub = ttk.Frame(hk_left)
        hk_start_sub.pack(anchor='w', pady=2)
        self.reaction_start_label = ttk.Label(hk_start_sub, text=self.reaction_start_hotkey.get(), foreground='blue')
        self.reaction_start_label.pack(side='left', padx=(0,6))
        ttk.Button(hk_start_sub, text='Set', command=self._bind_reaction_start_hotkey).pack(side='left')

        # Right column: Stop hotkey
        hk_right = ttk.Frame(hk_row)
        hk_right.pack(side='left', anchor='nw', padx=40)
        ttk.Label(hk_right, text='Stop Hotkey:').pack(anchor='w')
        hk_stop_sub = ttk.Frame(hk_right)
        hk_stop_sub.pack(anchor='w', pady=2)
        self.reaction_stop_label = ttk.Label(hk_stop_sub, text=self.reaction_stop_hotkey.get(), foreground='blue')
        self.reaction_stop_label.pack(side='left', padx=(0,6))
        ttk.Button(hk_stop_sub, text='Set', command=self._bind_reaction_stop_hotkey).pack(side='left')

        # Update color preview when var changes
        # Map base color names to hex values
        self._base_color_map = {
            'Green': '#00ff00',
            'Red': '#ff0000',
            'Blue': '#0000ff',
            'Purple': '#800080',
            'Yellow': '#ffff00',
            'Orange': '#ffa500',
            'Black': '#000000',
            'White': '#ffffff',
        }

        def _update_preview(*a):
            try:
                # If using exact/custom mode, use that hex, otherwise map from selected base name
                if self.reaction_use_exact_var.get():
                    hexv = self.reaction_custom_hex.get() or '#000000'
                else:
                    name = self.reaction_color_name_var.get()
                    hexv = self._base_color_map.get(name, '#000000')
                # ensure valid format
                if not hexv.startswith('#'):
                    hexv = '#' + hexv
                self.reaction_color_var.set(hexv)
                self.color_preview.config(background=hexv)
            except Exception:
                pass

        try:
            # Watch both name changes and exact-hex changes
            self.reaction_color_name_var.trace_add('write', lambda *args: _update_preview())
            self.reaction_use_exact_var.trace_add('write', lambda *args: _update_preview())
            self.reaction_custom_hex.trace_add('write', lambda *args: _update_preview())
        except Exception:
            try:
                self.reaction_color_name_var.trace('w', lambda *args: _update_preview())
                self.reaction_use_exact_var.trace('w', lambda *args: _update_preview())
                self.reaction_custom_hex.trace('w', lambda *args: _update_preview())
            except Exception:
                pass
        # initialize preview
        try:
            _update_preview()
        except Exception:
            pass

    def delete_macro(self):
        selection = self.macro_listbox.curselection()
        if selection:
            name = self.macro_listbox.get(selection[0])
            if name in self.macros:
                del self.macros[name]
            self.macro_listbox.delete(selection[0])
            self.delete_button.config(state='disabled')
            self.play_button.config(state='disabled')
            self.selected_macro_name = None

    def start_macro_record(self):
        # Start recording mouse and keyboard events
        from pynput import mouse, keyboard
        import time
        self.current_macro = []
        self._macro_record_start = time.time()
        self._macro_last_time = self._macro_record_start
        self._macro_record_last_pos = None
        self._macro_mouse_listener = mouse.Listener(
            on_move=self._on_macro_mouse_move,
            on_click=self._on_macro_mouse_click,
            on_scroll=self._on_macro_mouse_scroll)
        self._macro_keyboard_listener = keyboard.Listener(
            on_press=self._on_macro_key_press,
            on_release=self._on_macro_key_release)
        self._macro_mouse_listener.start()
        self._macro_keyboard_listener.start()
        self.record_button.config(state='disabled')
        self.macro_record_stop_button.config(state='normal')
        self.play_button.config(state='disabled')
        self.delete_button.config(state='disabled')

    def stop_macro_record(self):
        # Stop listeners and save macro
        try:
            self._macro_mouse_listener.stop()
        except Exception:
            pass
        try:
            self._macro_keyboard_listener.stop()
        except Exception:
            pass
        self.record_button.config(state='normal')
        self.macro_record_stop_button.config(state='disabled')
        self.play_button.config(state='normal')
        self.delete_button.config(state='normal')
        import tkinter.simpledialog
        name = tkinter.simpledialog.askstring("Save Macro", "Enter macro name:")
        if name:
            self.macros[name] = {'events': list(self.current_macro), 'looped': False}
            self.macro_listbox.insert('end', name)
        self.current_macro = []

    def _macro_event(self, event_type, *args):
        import time
        now = time.time()
        delay = now - self._macro_last_time
        self._macro_last_time = now
        self.current_macro.append((delay, event_type, args))

    def _on_macro_mouse_move(self, x, y):
        # Record exact pointer position (absolute), which is the original behavior
        self._macro_event('mouse_move', x, y)
    def _on_macro_mouse_click(self, x, y, button, pressed):
        self._macro_event('mouse_click', x, y, str(button), pressed)
    def _on_macro_mouse_scroll(self, x, y, dx, dy):
        self._macro_event('mouse_scroll', x, y, dx, dy)
    def _on_macro_key_press(self, key):
        self._macro_event('key_press', str(key))
    def _on_macro_key_release(self, key):
        self._macro_event('key_release', str(key))

    def play_macro(self, name=None):
        """Replay the named macro (or selected macro). This sets a playing flag so it can be stopped."""
        import threading, time
        from pynput.mouse import Controller as MouseController, Button
        from pynput.keyboard import Controller as KeyboardController, Key
        if name is None:
            selection = self.macro_listbox.curselection()
            if not selection:
                return
            name = self.macro_listbox.get(selection[0])
        macro_data = self.macros.get(name)
        if not macro_data:
            return
        
        # Handle both old format (list) and new format (dict)
        if isinstance(macro_data, dict):
            events = macro_data.get('events', [])
            looped = macro_data.get('looped', False)
        else:
            events = macro_data
            looped = False
        
        if not events:
            return

        def run_macro():
            # Diagnostics: log start and event counts
            try:
                self.logger.info("Starting macro playback: %s", name)
            except Exception:
                pass
            self._macro_playing = True
            mouse = MouseController()
            keyboard = KeyboardController()
            self._macro_last_playback_pos = None
            try:
                # Diagnostic: how many events
                try:
                    ev_count = len(events)
                except Exception:
                    ev_count = 0
                self.logger.info("Macro '%s' has %d events (looped=%s)", name, ev_count, looped)

                # Loop if macro is marked as looped
                while self._macro_playing:
                    for i, (delay, event_type, args) in enumerate(events):
                        if not self._macro_playing:
                            break
                        # sleep in small chunks to be responsive to stop requests
                        slept = 0.0
                        while slept < delay:
                            if not self._macro_playing:
                                break
                            t0 = min(0.05, delay - slept)
                            time.sleep(t0)
                            slept += t0
                        if not self._macro_playing:
                            break

                        # Diagnostic log for each event
                        try:
                            self.logger.debug("Macro '%s' executing event %d: %s %s", name, i, event_type, args)
                        except Exception:
                            pass

                        try:
                            if event_type == 'mouse_move':
                                # Original behavior: set exact pointer position
                                try:
                                    x, y = args
                                    mouse.position = (x, y)
                                    self.logger.debug("mouse.position set to %s", (x, y))
                                except Exception:
                                    pass
                            elif event_type == 'mouse_move_rel':
                                # Fallback for any relative-recorded macros: apply a simple relative move
                                try:
                                    dx, dy = args
                                    try:
                                        ok = _win_send_mouse_move(int(dx), int(dy))
                                        if ok:
                                            self.logger.debug("SendInput relative move dx=%s dy=%s", dx, dy)
                                        else:
                                            mouse.move(int(dx), int(dy))
                                            self.logger.debug("mouse.move relative dx=%s dy=%s", dx, dy)
                                    except Exception:
                                        mouse.move(int(dx), int(dy))
                                except Exception:
                                    pass
                            elif event_type == 'mouse_click':
                                x, y, button, pressed = args
                                try:
                                    mouse.position = (x, y)
                                except Exception:
                                    pass
                                btn = Button.left if 'left' in button else Button.right if 'right' in button else Button.middle
                                if pressed:
                                    mouse.press(btn)
                                else:
                                    mouse.release(btn)
                            elif event_type == 'mouse_scroll':
                                x, y, sdx, sdy = args
                                try:
                                    mouse.position = (x, y)
                                except Exception:
                                    pass
                                mouse.scroll(sdx, sdy)
                            elif event_type == 'key_press':
                                k = args[0]
                                try:
                                    if hasattr(Key, k):
                                        keyboard.press(getattr(Key, k))
                                    else:
                                        keyboard.press(eval(k))
                                except Exception:
                                    try:
                                        keyboard.press(k)
                                    except Exception:
                                        pass
                            elif event_type == 'key_release':
                                k = args[0]
                                try:
                                    if hasattr(Key, k):
                                        keyboard.release(getattr(Key, k))
                                    else:
                                        keyboard.release(eval(k))
                                except Exception:
                                    try:
                                        keyboard.release(k)
                                    except Exception:
                                        pass
                        except Exception as e:
                            self.logger.exception("Error executing macro event: %s", e)
                    # Exit loop if not looped
                    if not looped or not self._macro_playing:
                        break
            except Exception as e:
                self.logger.exception("Unhandled error during macro playback: %s", e)
            finally:
                self._macro_playing = False

        threading.Thread(target=run_macro, daemon=True).start()

    def stop_macro_play(self):
        """Stop any running macro playback."""
        try:
            self._macro_playing = False
        except Exception:
            pass

    def _sample_pixel_at_cursor(self):
        """Sample current cursor position and set X,Y and color fields."""
        try:
            pos = _get_cursor_pos()
            if pos:
                x, y = pos
            else:
                # fallback to pynput if GetCursorPos failed
                x, y = self.mouse.position
            self.reaction_x_var.set(str(int(x)))
            self.reaction_y_var.set(str(int(y)))
            col = _get_pixel_color(int(x), int(y))
            if col:
                # ensure we always use #rrggbb format
                self.reaction_color_var.set('#%02x%02x%02x' % (col[0], col[1], col[2]))
        except Exception:
            pass

    def _sample_color_at_coords(self):
        try:
            x = int(self.reaction_x_var.get())
            y = int(self.reaction_y_var.get())
            col = _get_pixel_color(x, y)
            if col:
                # If exact mode enabled, set custom hex to sampled color
                if self.reaction_use_exact_var.get():
                    self.reaction_custom_hex.set('#%02x%02x%02x' % (col[0], col[1], col[2]))
                else:
                    # find nearest base color by Euclidean distance
                    best_name = None
                    best_dist = None
                    for name, hexv in getattr(self, '_base_color_map', {}).items():
                        r = int(hexv[1:3], 16)
                        g = int(hexv[3:5], 16)
                        b = int(hexv[5:7], 16)
                        dist = ((r-col[0])**2 + (g-col[1])**2 + (b-col[2])**2) ** 0.5
                        if best_dist is None or dist < best_dist:
                            best_dist = dist
                            best_name = name
                    if best_name:
                        self.reaction_color_name_var.set(best_name)
        except Exception:
            pass

    def start_reaction_test(self):
        if getattr(self, '_reaction_running', False):
            return
        self._reaction_running = True
        self.reaction_start_btn.config(state='disabled')
        self.reaction_stop_btn.config(state='normal')
        self._reaction_thread = threading.Thread(target=self._reaction_loop, daemon=True)
        self._reaction_thread.start()

    def stop_reaction_test(self):
        try:
            self._reaction_running = False
        except Exception:
            pass
        try:
            self.reaction_start_btn.config(state='normal')
            self.reaction_stop_btn.config(state='disabled')
        except Exception:
            pass

    def _color_distance(self, a, b):
        return ((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2) ** 0.5

    def _reaction_loop(self):
        mouse = Controller()
        btn_name = (self.reaction_button.get() or 'left').lower()
        if btn_name == 'right':
            btn = Button.right
        elif btn_name == 'middle':
            btn = Button.middle
        else:
            btn = Button.left

        try:
            # Determine target RGB depending on exact mode or base color
            if self.reaction_use_exact_var.get():
                target_hex = self.reaction_custom_hex.get()
            else:
                name = self.reaction_color_name_var.get()
                target_hex = self._base_color_map.get(name, '#000000')
            if target_hex and target_hex.startswith('#'):
                target_rgb = tuple(int(target_hex[i:i+2], 16) for i in (1,3,5))
            else:
                target_rgb = (0,0,0)
        except Exception:
            target_rgb = (0,0,0)

        tolerance = int(self.reaction_tolerance_var.get() or 0)
        cooldown_ms = int(self.reaction_cooldown_var.get() or 200)
        delay_ms = int(self.reaction_delay_var.get() or 0)

        last_fire = 0
        logger = logging.getLogger(__name__)
        while getattr(self, '_reaction_running', False):
            try:
                # If Watch Cursor is enabled, sample the live cursor position; otherwise use configured X/Y
                if getattr(self, 'reaction_watch_cursor_var', None) and self.reaction_watch_cursor_var.get():
                    pos = _get_cursor_pos()
                    if pos:
                        x, y = pos
                    else:
                        # fallback to pynput Controller position
                        try:
                            x, y = mouse.position
                        except Exception:
                            x, y = int(self.reaction_x_var.get() or 0), int(self.reaction_y_var.get() or 0)
                else:
                    x = int(self.reaction_x_var.get() or 0)
                    y = int(self.reaction_y_var.get() or 0)
                col = _get_pixel_color(x, y)
                if col is None:
                    logger.debug("Reaction: GetPixel returned None at (%s,%s)", x, y)
                    time.sleep(0.01)
                    continue

                dist = self._color_distance(col, target_rgb)
                now = int(time.time()*1000)
                logger.debug("Reaction sample at (%s,%s): sampled=%s target=%s tol=%s dist=%.2f last_fire=%s now=%s", x, y, col, target_rgb, tolerance, dist, last_fire, now)

                fired = False
                if dist <= tolerance:
                    if now - last_fire >= cooldown_ms:
                        # optional delay before click
                        if delay_ms > 0:
                            time.sleep(delay_ms/1000.0)
                        try:
                            # If configured, send a keyboard key instead of a mouse click
                            use_kb = getattr(self, 'reaction_use_keyboard_var', None) and self.reaction_use_keyboard_var.get()
                            kb_key = (self.reaction_key_var.get() or '').strip() if use_kb else ''
                            if use_kb and kb_key:
                                try:
                                    from pynput.keyboard import Controller as KController, Key
                                    kctl = KController()
                                    if hasattr(Key, kb_key):
                                        kctl.press(getattr(Key, kb_key))
                                        kctl.release(getattr(Key, kb_key))
                                    else:
                                        kctl.press(kb_key)
                                        kctl.release(kb_key)
                                except Exception as e:
                                    logger.exception("Reaction key press failed: %s", e)
                            else:
                                mouse.press(btn)
                                mouse.release(btn)
                            fired = True
                        except Exception as e:
                            logger.exception("Reaction click/keypress failed: %s", e)
                        last_fire = int(time.time()*1000)
                if fired:
                    logger.info("Reaction fired click at (%s,%s) sampled=%s target=%s dist=%.2f", x, y, col, target_rgb, dist)
                time.sleep(0.01)
            except Exception as e:
                logger.exception("Error in reaction loop: %s", e)
                time.sleep(0.05)

    def _bind_reaction_start_hotkey(self):
        self.reaction_start_label.config(text='Listening...')
        self.is_setting_hotkey = True
        from pynput import keyboard
        def on_press(key):
            if not self.is_setting_hotkey:
                return False
            try:
                key_name = ''
                if hasattr(key, 'name'):
                    key_name = key.name.lower()
                elif hasattr(key, 'char'):
                    key_name = key.char.lower() if key.char else ''
                else:
                    key_name = str(key).lower()
                self.reaction_start_hotkey.set(key_name)
                self.reaction_start_label.config(text=key_name)
                self.is_setting_hotkey = False
                # restart global listener so it picks up new hotkey
                try:
                    self._stop_hotkey_listener()
                    self._start_hotkey_listener()
                except Exception:
                    pass
                return False
            except Exception:
                return False
        bind_listener = keyboard.Listener(on_press=on_press)
        bind_listener.daemon = True
        bind_listener.start()

    def _bind_reaction_stop_hotkey(self):
        self.reaction_stop_label.config(text='Listening...')
        self.is_setting_hotkey = True
        from pynput import keyboard
        def on_press(key):
            if not self.is_setting_hotkey:
                return False
            try:
                key_name = ''
                if hasattr(key, 'name'):
                    key_name = key.name.lower()
                elif hasattr(key, 'char'):
                    key_name = key.char.lower() if key.char else ''
                else:
                    key_name = str(key).lower()
                self.reaction_stop_hotkey.set(key_name)
                self.reaction_stop_label.config(text=key_name)
                self.is_setting_hotkey = False
                try:
                    self._stop_hotkey_listener()
                    self._start_hotkey_listener()
                except Exception:
                    pass
                return False
            except Exception:
                return False
        bind_listener = keyboard.Listener(on_press=on_press)
        bind_listener.daemon = True
        bind_listener.start()

    def _bind_autoclick_key(self):
        """Bind a key to be sent when the autoclicker fires."""
        try:
            self.autoclick_key_label.config(text='Listening...')
        except Exception:
            pass
        self.is_setting_hotkey = True
        from pynput import keyboard
        def on_press(key):
            if not self.is_setting_hotkey:
                return False
            try:
                key_name = ''
                if hasattr(key, 'name'):
                    key_name = key.name.lower()
                elif hasattr(key, 'char'):
                    key_name = key.char.lower() if key.char else ''
                else:
                    key_name = str(key).lower()
                self.autoclick_key_var.set(key_name)
                try:
                    self.autoclick_key_label.config(text=key_name)
                except Exception:
                    pass
                self.is_setting_hotkey = False
                return False
            except Exception:
                return False
        bind_listener = keyboard.Listener(on_press=on_press)
        bind_listener.daemon = True
        bind_listener.start()

    def _bind_reaction_key(self):
        """Bind a key to be sent when the reaction test fires."""
        try:
            self.reaction_key_label.config(text='Listening...')
        except Exception:
            pass
        self.is_setting_hotkey = True
        from pynput import keyboard
        def on_press(key):
            if not self.is_setting_hotkey:
                return False
            try:
                key_name = ''
                if hasattr(key, 'name'):
                    key_name = key.name.lower()
                elif hasattr(key, 'char'):
                    key_name = key.char.lower() if key.char else ''
                else:
                    key_name = str(key).lower()
                self.reaction_key_var.set(key_name)
                try:
                    self.reaction_key_label.config(text=key_name)
                except Exception:
                    pass
                self.is_setting_hotkey = False
                return False
            except Exception:
                return False
        bind_listener = keyboard.Listener(on_press=on_press)
        bind_listener.daemon = True
        bind_listener.start()

    def _update_macro_loop(self):
        """Update the loop state for the selected macro."""
        if self.selected_macro_name and self.selected_macro_name in self.macros:
            macro_data = self.macros[self.selected_macro_name]
            # Ensure it's in dict format
            if isinstance(macro_data, dict):
                macro_data['looped'] = bool(self.macro_loop_var.get())
            else:
                # Convert old format to new
                self.macros[self.selected_macro_name] = {
                    'events': macro_data,
                    'looped': bool(self.macro_loop_var.get())
                }

    def on_macro_select(self, event):
        selection = self.macro_listbox.curselection()
        if selection:
            self.selected_macro_name = self.macro_listbox.get(selection[0])
            self.delete_button.config(state='normal')
            self.play_button.config(state='normal')
            self.macro_loop_cb.config(state='normal')
            # Load loop state for this macro
            macro_data = self.macros.get(self.selected_macro_name)
            if isinstance(macro_data, dict):
                looped = macro_data.get('looped', False)
            else:
                # Old format - convert to new format
                looped = False
                self.macros[self.selected_macro_name] = {'events': macro_data, 'looped': False}
            self.macro_loop_var.set(1 if looped else 0)
        else:
            self.selected_macro_name = None
            self.delete_button.config(state='disabled')
            self.play_button.config(state='disabled')
            self.macro_loop_cb.config(state='disabled')
            self.macro_loop_var.set(0)

if __name__ == "__main__":
    import tkinter as tk
    root = tk.Tk()
    app = TabbedGUI(root)
    root.mainloop()
