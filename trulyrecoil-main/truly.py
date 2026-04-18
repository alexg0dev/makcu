import threading
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
import json
import os
import socket
import time
import random



from mouse.makcu import makcu_controller

CONFIG_DIR = os.path.join(os.path.dirname(__file__), 'configs')
os.makedirs(CONFIG_DIR, exist_ok=True)

DEFAULT_CONFIG_FILE = "r6_operators.json"
CONFIG_FILE = os.path.join(CONFIG_DIR, DEFAULT_CONFIG_FILE)
APP_STATE_FILE = os.path.join(os.path.dirname(__file__), 'app_state.json')
INTERNAL_CONFIG_STATE_KEY = "__app_state__"

class GunConfig(BaseModel):
    gun_name: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-zA-Z0-9_\- ]+$')
    pull_down_value: float = Field(..., ge=0, le=300)
    horizontal_value: float = Field(default=0, ge=-300, le=300)
    horizontal_delay_ms: int = Field(default=500, ge=0, le=5000)
    horizontal_duration_ms: int = Field(default=2000, ge=0, le=10000)

class OperatorConfig(BaseModel):
    operator_name: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-zA-Z0-9_\- ]+$')
    primary_gun: str = Field(..., min_length=1, max_length=50)
    primary_config: dict = Field(...)
    secondary_gun: str = Field(..., min_length=1, max_length=50)
    secondary_config: dict = Field(...)

def get_config_path(filename):
    return os.path.join(CONFIG_DIR, filename)

def read_configs(config_file=None):
    if config_file is None:
        config_file = DEFAULT_CONFIG_FILE
    config_path = get_config_path(config_file)
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[CONFIG] Error reading configs: {e}")
        return {}

def write_configs(configs, config_file=None):
    if config_file is None:
        config_file = DEFAULT_CONFIG_FILE
    config_path = get_config_path(config_file)
    try:
        with open(config_path, 'w') as f:
            json.dump(configs, f, indent=4)
    except OSError as e:
        print(f"[CONFIG] Error writing configs: {e}")

def read_app_state_file():
    if not os.path.exists(APP_STATE_FILE):
        return {}
    try:
        with open(APP_STATE_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[CONFIG] Error reading app state: {e}")
        return {}

def write_app_state_file(state):
    try:
        with open(APP_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except OSError as e:
        print(f"[CONFIG] Error writing app state: {e}")

def list_config_files():
    try:
        files = [f for f in os.listdir(CONFIG_DIR) if f.endswith('.json')]
        return sorted(files)
    except OSError:
        return []

def create_config_file(filename):
    if not filename.endswith('.json'):
        filename = filename + '.json'
    filepath = get_config_path(filename)
    if os.path.exists(filepath):
        raise HTTPException(status_code=400, detail="Config file already exists.")
    try:
        with open(filepath, 'w') as f:
            json.dump({}, f)
        return filename
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to create config file: {e}")

def delete_config_file(filename):
    if filename == DEFAULT_CONFIG_FILE:
        raise HTTPException(status_code=400, detail="Cannot delete default config.")
    filepath = get_config_path(filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Config file not found.")
    try:
        os.remove(filepath)
        return True
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete config file: {e}")

VALID_TOGGLE_BUTTONS = ["MMB", "M4", "M5"]

class AppState:
    def __init__(self):
        saved_state = read_app_state_file()
        self.active_pull_down_value = 1.0
        self.active_horizontal_value = 0.0
        self.horizontal_delay_ms = 500
        self.horizontal_duration_ms = 2000
        self.is_enabled = False
        self.toggle_button = "M5"
        self.current_config_file = saved_state.get("current_config_file", DEFAULT_CONFIG_FILE)
        if not os.path.exists(get_config_path(self.current_config_file)):
            self.current_config_file = DEFAULT_CONFIG_FILE
        self.selected_operator_name = ""
        self.current_slot = "primary"  # "primary" or "secondary"
        self.lock = threading.Lock()

    def set_active_value(self, value):
        with self.lock:
            self.active_pull_down_value = value

    def get_active_value(self):
        with self.lock:
            return self.active_pull_down_value

    def set_horizontal_value(self, value):
        with self.lock:
            self.active_horizontal_value = value

    def get_horizontal_value(self):
        with self.lock:
            return self.active_horizontal_value

    def set_horizontal_delay(self, ms):
        with self.lock:
            self.horizontal_delay_ms = max(0, min(5000, ms))

    def get_horizontal_delay(self):
        with self.lock:
            return self.horizontal_delay_ms

    def set_horizontal_duration(self, ms):
        with self.lock:
            self.horizontal_duration_ms = max(0, min(10000, ms))

    def get_horizontal_duration(self):
        with self.lock:
            return self.horizontal_duration_ms

    def get_enabled(self):
        with self.lock:
            return self.is_enabled

    def toggle_enabled(self):
        with self.lock:
            self.is_enabled = not self.is_enabled
            return self.is_enabled

    def set_toggle_button(self, button):
        with self.lock:
            if button in VALID_TOGGLE_BUTTONS:
                self.toggle_button = button
                return self.toggle_button
            return None

    def get_toggle_button(self):
        with self.lock:
            return self.toggle_button

    def set_current_config_file(self, filename):
        with self.lock:
            if not filename.endswith('.json'):
                filename = filename + '.json'
            self.current_config_file = filename
            return filename

    def get_current_config_file(self):
        with self.lock:
            return self.current_config_file

    def set_selected_operator_name(self, operator_name):
        with self.lock:
            self.selected_operator_name = operator_name
            return self.selected_operator_name

    def get_selected_operator_name(self):
        with self.lock:
            return self.selected_operator_name

    def set_current_slot(self, slot):
        with self.lock:
            if slot in ["primary", "secondary"]:
                self.current_slot = slot
                return self.current_slot
            return None

    def get_current_slot(self):
        with self.lock:
            return self.current_slot

    def apply_gun_config(self, config):
        with self.lock:
            self.active_pull_down_value = config["pull_down"]
            self.active_horizontal_value = config["horizontal"]
            self.horizontal_delay_ms = config["horizontal_delay_ms"]
            self.horizontal_duration_ms = config["horizontal_duration_ms"]

    def to_persisted_state(self):
        with self.lock:
            return {
                "current_config_file": self.current_config_file,
            }

    def get_status(self):
        with self.lock:
            return {
                "is_enabled": self.is_enabled,
                "toggle_button": self.toggle_button,
                "pull_down": self.active_pull_down_value,
                "horizontal": self.active_horizontal_value,
                "horizontal_delay_ms": self.horizontal_delay_ms,
                "horizontal_duration_ms": self.horizontal_duration_ms,
                "current_config_file": self.current_config_file,
                "selected_operator_name": self.selected_operator_name,
                "current_slot": self.current_slot,
            }

def normalize_gun_config(config):
    if isinstance(config, dict):
        return {
            "pull_down": float(config.get("pull_down", 0)),
            "horizontal": float(config.get("horizontal", 0)),
            "horizontal_delay_ms": int(config.get("horizontal_delay_ms", 500)),
            "horizontal_duration_ms": int(config.get("horizontal_duration_ms", 2000)),
        }
    return {
        "pull_down": float(config or 0),
        "horizontal": 0.0,
        "horizontal_delay_ms": 500,
        "horizontal_duration_ms": 2000,
    }

def get_user_configs(config_file=None):
    configs = read_configs(config_file)
    if not isinstance(configs, dict):
        return {}
    return {
        operator_name: config
        for operator_name, config in configs.items()
        if operator_name != INTERNAL_CONFIG_STATE_KEY
    }

def persist_runtime_state(config_file=None):
    if config_file is None:
        config_file = app_state.get_current_config_file()

    configs = read_configs(config_file)
    if not isinstance(configs, dict):
        configs = {}

    status = app_state.get_status()
    configs[INTERNAL_CONFIG_STATE_KEY] = {
        "selected_operator_name": status["selected_operator_name"],
        "current_slot": status["current_slot"],
        "pull_down": status["pull_down"],
        "horizontal": status["horizontal"],
        "horizontal_delay_ms": status["horizontal_delay_ms"],
        "horizontal_duration_ms": status["horizontal_duration_ms"],
    }
    write_configs(configs, config_file)

def restore_runtime_state():
    configs = read_configs(app_state.get_current_config_file())
    if not isinstance(configs, dict):
        return

    state = configs.get(INTERNAL_CONFIG_STATE_KEY)
    if not isinstance(state, dict):
        return

    app_state.set_selected_operator_name(state.get("selected_operator_name", ""))
    app_state.set_current_slot(state.get("current_slot", "primary"))
    app_state.apply_gun_config(normalize_gun_config(state))

def save_app_state():
    write_app_state_file(app_state.to_persisted_state())

def restore_selected_config():
    restore_runtime_state()

    selected_operator_name = app_state.get_selected_operator_name()
    if not selected_operator_name:
        return

    configs = get_user_configs(app_state.get_current_config_file())
    saved_config = configs.get(selected_operator_name)
    if saved_config is None:
        app_state.set_selected_operator_name("")
        persist_runtime_state()
        save_app_state()
        return

    # Apply the current slot's config
    current_slot = app_state.get_current_slot()
    slot_config = saved_config.get(current_slot, {}).get("config", {})
    if slot_config:
        app_state.apply_gun_config(normalize_gun_config(slot_config))

app_state = AppState()
restore_selected_config()

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    thread = threading.Thread(target=mouse_control_loop, daemon=True)
    thread.start()
    yield

app = FastAPI(lifespan=lifespan)

def mouse_control_loop():
    makcu_controller.StartButtonListener()
    toggle_was_pressed = False
    lmb_hold_start = None
    toggle_hold_start = None
    while True:
        if not makcu_controller.is_connected():
            time.sleep(0.5)
            makcu_controller.connect()
            continue

        mmb_pressed = makcu_controller.get_button_state("MMB")
        if mmb_pressed:
            if toggle_hold_start is None:
                toggle_hold_start = time.time()
            elif time.time() - toggle_hold_start >= 10:
                app_state.toggle_enabled()
                toggle_hold_start = None
        else:
            toggle_hold_start = None

        lmb_down = makcu_controller.get_button_state("LMB")
        rmb_down = makcu_controller.get_button_state("RMB")

        if app_state.get_enabled() and lmb_down and rmb_down:
            now = time.time()
            if lmb_hold_start is None:
                lmb_hold_start = now

            pull_value = app_state.get_active_value()
            # Add more human-like randomization
            human_factor = random.uniform(0.85, 1.15)  # ±15% variation
            pull_value *= human_factor
            
            y_move = round((pull_value + random.uniform(-0.5, 0.5)) / 5) if pull_value > 0 else 0

            x_move = 0
            hold_ms = (now - lmb_hold_start) * 1000
            delay = app_state.get_horizontal_delay()
            duration = app_state.get_horizontal_duration()
            if hold_ms >= delay and (duration == 0 or hold_ms <= delay + duration):
                h_value = app_state.get_horizontal_value()
                # Add horizontal randomization
                h_human_factor = random.uniform(0.9, 1.1)  # ±10% variation
                h_value *= h_human_factor
                x_move = round((h_value + random.uniform(-0.4, 0.4)) / 5)

            # Add hesitation and micro-pauses for more human feel
            if random.random() < 0.08:  # 8% chance
                time.sleep(random.uniform(0.015, 0.045))
            elif random.random() < 0.03:  # 3% chance of longer pause
                time.sleep(random.uniform(0.08, 0.15))

            if y_move != 0 or x_move != 0:
                makcu_controller.simple_move_mouse(x_move, y_move)
        else:
            lmb_hold_start = None

        time.sleep(random.uniform(0.008, 0.012))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                params = {
                    "pull_down": ("active_value", float),
                    "horizontal": ("horizontal_value", float),
                    "horizontal_delay_ms": ("horizontal_delay", int),
                    "horizontal_duration_ms": ("horizontal_duration", int)
                }
                
                for key, (method, converter) in params.items():
                    if key in msg:
                        value = converter(msg[key])
                        method = getattr(app_state, f"set_{method}")
                        method(value)
                persist_runtime_state()
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
    except WebSocketDisconnect:
        pass

@app.get("/status", response_class=JSONResponse)
async def get_status():
    status = app_state.get_status()
    status["makcu"] = makcu_controller.get_connection_status()
    return status

@app.post("/toggle", response_class=JSONResponse)
async def toggle_status():
    is_enabled = app_state.toggle_enabled()
    return {"is_enabled": is_enabled}

class ToggleButtonConfig(BaseModel):
    button: str

@app.post("/toggle-button", response_class=JSONResponse)
async def set_toggle_button(config: ToggleButtonConfig):
    result = app_state.set_toggle_button(config.button)
    if result is None:
        raise HTTPException(status_code=400, detail=f"Invalid button. Must be one of: {VALID_TOGGLE_BUTTONS}")
    return {"toggle_button": result}

class ConfigFileRequest(BaseModel):
    filename: str

class ActiveConfigRequest(BaseModel):
    operator_name: str

class SwitchSlotRequest(BaseModel):
    slot: str  # "primary" or "secondary"

@app.get("/config-files", response_class=JSONResponse)
async def get_config_files():
    return {
        "files": list_config_files(),
        "current": app_state.get_current_config_file()
    }

@app.post("/config-files", response_class=JSONResponse)
async def create_config_file_action(req: ConfigFileRequest):
    filename = create_config_file(req.filename)
    return {"message": f"Config file '{filename}' created.", "files": list_config_files()}

@app.post("/config-files/switch", response_class=JSONResponse)
async def switch_config_file(req: ConfigFileRequest):
    filename = req.filename
    if not filename.endswith('.json'):
        filename = filename + '.json'
    filepath = get_config_path(filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Config file not found.")
    app_state.set_current_config_file(filename)
    configs = get_user_configs(filename)
    if app_state.get_selected_operator_name() not in configs:
        app_state.set_selected_operator_name("")
    restore_selected_config()
    save_app_state()
    return {
        "current_config_file": filename,
        "operators": configs,
        "selected_operator_name": app_state.get_selected_operator_name(),
    }

@app.delete("/config-files/{filename}", response_class=JSONResponse)
async def delete_config_file_action(filename: str):
    delete_config_file(filename)
    if app_state.get_current_config_file() == filename:
        app_state.set_current_config_file(DEFAULT_CONFIG_FILE)
        app_state.set_selected_operator_name("")
        restore_selected_config()
        persist_runtime_state()
        save_app_state()
    return {"message": "Config file deleted.", "files": list_config_files()}

@app.get("/configs", response_class=JSONResponse)
async def get_configs():
    return get_user_configs(app_state.get_current_config_file())

@app.post("/operators", response_class=JSONResponse)
async def create_operator_config(config: OperatorConfig):
    current_file = app_state.get_current_config_file()
    configs = read_configs(current_file)
    configs[config.operator_name] = {
        "primary": {
            "gun_name": config.primary_gun,
            "config": config.primary_config
        },
        "secondary": {
            "gun_name": config.secondary_gun,
            "config": config.secondary_config
        }
    }
    write_configs(configs, current_file)
    app_state.set_selected_operator_name(config.operator_name)
    app_state.set_current_slot("primary")
    app_state.apply_gun_config(normalize_gun_config(config.primary_config))
    persist_runtime_state(current_file)
    save_app_state()
    return {"message": "Operator config saved successfully."}

@app.post("/operators/activate", response_class=JSONResponse)
async def activate_operator_config(req: ActiveConfigRequest):
    current_file = app_state.get_current_config_file()
    configs = get_user_configs(current_file)
    if req.operator_name not in configs:
        raise HTTPException(status_code=404, detail="Operator config not found.")

    operator_config = configs[req.operator_name]
    current_slot = app_state.get_current_slot()
    slot_config = operator_config.get(current_slot, {}).get("config", {})
    
    if not slot_config:
        raise HTTPException(status_code=404, detail=f"No config found for {current_slot} slot.")

    normalized_config = normalize_gun_config(slot_config)
    app_state.set_selected_operator_name(req.operator_name)
    app_state.apply_gun_config(normalized_config)
    persist_runtime_state(current_file)
    save_app_state()

    return {
        "operator_name": req.operator_name,
        "current_slot": current_slot,
        **normalized_config,
    }

@app.post("/operators/switch-slot", response_class=JSONResponse)
async def switch_slot(req: SwitchSlotRequest):
    if req.slot not in ["primary", "secondary"]:
        raise HTTPException(status_code=400, detail="Slot must be 'primary' or 'secondary'.")
    
    current_file = app_state.get_current_config_file()
    selected_operator = app_state.get_selected_operator_name()
    
    if not selected_operator:
        raise HTTPException(status_code=400, detail="No operator selected.")
    
    configs = get_user_configs(current_file)
    if selected_operator not in configs:
        raise HTTPException(status_code=404, detail="Operator config not found.")
    
    operator_config = configs[selected_operator]
    slot_config = operator_config.get(req.slot, {}).get("config", {})
    
    if not slot_config:
        raise HTTPException(status_code=404, detail=f"No config found for {req.slot} slot.")
    
    app_state.set_current_slot(req.slot)
    app_state.apply_gun_config(normalize_gun_config(slot_config))
    persist_runtime_state(current_file)
    save_app_state()
    
    return {
        "operator_name": selected_operator,
        "current_slot": req.slot,
        "gun_name": operator_config[req.slot]["gun_name"],
        **normalize_gun_config(slot_config),
    }

@app.delete("/operators/{operator_name}", response_class=JSONResponse)
async def delete_operator_config(operator_name: str):
    current_file = app_state.get_current_config_file()
    configs = read_configs(current_file)
    if operator_name not in configs:
        raise HTTPException(status_code=404, detail="Operator config not found.")
    del configs[operator_name]
    write_configs(configs, current_file)
    if app_state.get_selected_operator_name() == operator_name:
        app_state.set_selected_operator_name("")
        persist_runtime_state(current_file)
        save_app_state()
    return {"message": "Operator config deleted successfully."}
    
@app.get("/", response_class=HTMLResponse)
async def get():
    return HTMLResponse(content="""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Truly</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

            * { margin: 0; padding: 0; box-sizing: border-box; }

            body {
                font-family: 'Inter', sans-serif;
                background: #0f0f0f;
                color: #e0e0e0;
                min-height: 100vh;
                display: flex;
                justify-content: center;
                padding: 40px 16px;
            }

            .container {
                max-width: 440px;
                width: 100%;
                animation: fadeInUp 0.5s ease-out;
            }

            @keyframes fadeInUp {
                from { opacity: 0; transform: translateY(20px); }
                to { opacity: 1; transform: translateY(0); }
            }

            @keyframes pulse {
                0%, 100% { box-shadow: 0 0 0 0 rgba(99, 255, 150, 0.4); }
                50% { box-shadow: 0 0 20px 4px rgba(99, 255, 150, 0.15); }
            }

            @keyframes pulseOff {
                0%, 100% { box-shadow: 0 0 0 0 rgba(255, 80, 80, 0.3); }
                50% { box-shadow: 0 0 20px 4px rgba(255, 80, 80, 0.1); }
            }

            h1 {
                font-size: 2.4em;
                font-weight: 700;
                letter-spacing: -1px;
                color: #fff;
                margin-bottom: 32px;
                text-align: center;
            }

            .card {
                background: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 12px;
                padding: 20px;
                margin-bottom: 12px;
                transition: border-color 0.3s ease, transform 0.2s ease;
            }

            .card:hover {
                border-color: #3a3a3a;
                transform: translateY(-1px);
            }

            .card-label {
                font-size: 0.75em;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 1px;
                color: #666;
                margin-bottom: 12px;
            }

            #toggle-btn {
                width: 100%;
                padding: 16px;
                border: 2px solid #2a2a2a;
                border-radius: 10px;
                font-size: 1.1em;
                font-weight: 600;
                font-family: 'Inter', sans-serif;
                cursor: pointer;
                transition: all 0.3s ease;
                background: #1a1a1a;
                color: #888;
                letter-spacing: 0.5px;
            }

            #toggle-btn.enabled {
                background: linear-gradient(135deg, #0a2a12, #0f3318);
                border-color: #2d6b3f;
                color: #63ff96;
                animation: pulse 2s infinite;
            }

            #toggle-btn.disabled {
                background: linear-gradient(135deg, #2a0f0f, #331414);
                border-color: #6b2d2d;
                color: #ff5050;
                animation: pulseOff 2s infinite;
            }

            #toggle-btn:active { transform: scale(0.98); }

            .toggle-row {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                margin-top: 12px;
                font-size: 0.8em;
                color: #555;
            }

            select, input[type="text"] {
                background: #111;
                border: 1px solid #2a2a2a;
                border-radius: 8px;
                color: #e0e0e0;
                padding: 10px 12px;
                font-size: 0.9em;
                font-family: 'Inter', sans-serif;
                outline: none;
                transition: border-color 0.2s ease;
            }

            select:focus, input[type="text"]:focus {
                border-color: #555;
            }

            select { cursor: pointer; }

            #toggle-button-select {
                background: #111;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                color: #999;
                padding: 4px 8px;
                font-size: 1em;
                font-family: 'Inter', sans-serif;
            }

            #configs-dropdown { width: 100%; }

            .slider-hint {
                font-size: 0.75em;
                color: #555;
                margin-top: 8px;
            }

            .delay-unit {
                font-size: 0.45em;
                color: #666;
                font-weight: 400;
                margin-left: 2px;
            }

            .number-input {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                color: #fff;
                font-size: 2em;
                font-weight: 700;
                font-family: 'Inter', sans-serif;
                width: 100%;
                text-align: left;
                padding: 0 4px;
                margin-bottom: 8px;
                outline: none;
                -moz-appearance: textfield;
                transition: border-color 0.2s ease, background 0.2s ease;
            }

            .number-input::-webkit-outer-spin-button,
            .number-input::-webkit-inner-spin-button {
                -webkit-appearance: none;
                margin: 0;
            }

            .number-input:hover { border-color: #333; }
            .number-input:focus { border-color: #555; background: #111; }

            .number-row {
                display: flex;
                align-items: baseline;
                gap: 4px;
            }

            .number-row .number-input { width: auto; flex: 0 0 auto; }

            input[type="range"] {
                -webkit-appearance: none;
                appearance: none;
                width: 100%;
                height: 6px;
                border-radius: 3px;
                background: #2a2a2a;
                outline: none;
                transition: background 0.2s;
            }

            input[type="range"]::-webkit-slider-thumb {
                -webkit-appearance: none;
                appearance: none;
                width: 20px;
                height: 20px;
                border-radius: 50%;
                background: #fff;
                cursor: pointer;
                box-shadow: 0 0 8px rgba(255,255,255,0.2);
                transition: transform 0.15s ease, box-shadow 0.15s ease;
            }

            input[type="range"]::-webkit-slider-thumb:hover {
                transform: scale(1.2);
                box-shadow: 0 0 12px rgba(255,255,255,0.35);
            }

            input[type="range"]::-moz-range-thumb {
                width: 20px;
                height: 20px;
                border-radius: 50%;
                background: #fff;
                cursor: pointer;
                border: none;
            }

            .btn {
                padding: 10px 18px;
                border: 1px solid #2a2a2a;
                border-radius: 8px;
                font-size: 0.85em;
                font-weight: 500;
                font-family: 'Inter', sans-serif;
                cursor: pointer;
                transition: all 0.2s ease;
                background: #1a1a1a;
                color: #ccc;
            }

            .btn:hover {
                background: #222;
                border-color: #444;
                transform: translateY(-1px);
            }

            .btn:active { transform: translateY(0) scale(0.98); }

            .btn-save {
                background: #1a1a2e;
                border-color: #2d2d6b;
                color: #8888ff;
            }
            .btn-save:hover { background: #22224a; border-color: #4444aa; }

            .btn-delete {
                background: #2a1010;
                border-color: #5a2020;
                color: #ff6666;
            }
            .btn-delete:hover { background: #3a1515; border-color: #7a3030; }

            .info-wrap {
                display: flex;
                align-items: center;
                gap: 8px;
            }

            .info-icon {
                position: relative;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 18px;
                height: 18px;
                border-radius: 50%;
                border: 1px solid #444;
                color: #666;
                font-size: 0.7em;
                font-weight: 700;
                cursor: help;
                flex-shrink: 0;
            }

            .info-icon .tooltip {
                display: none;
                position: absolute;
                bottom: calc(100% + 8px);
                left: 50%;
                transform: translateX(-50%);
                background: #222;
                border: 1px solid #3a3a3a;
                border-radius: 8px;
                padding: 8px 12px;
                font-size: 1.1em;
                font-weight: 400;
                color: #bbb;
                white-space: nowrap;
                z-index: 10;
            }

            .info-icon:hover .tooltip { display: block; }

            .save-row {
                display: flex;
                gap: 8px;
            }

            .save-row input[type="text"] { flex: 1; }

            .btn-overwrite {
                background: #1a1e2a;
                border-color: #2d3d6b;
                color: #88aaff;
            }
            .btn.active {
                background: #1a4a1a;
                border-color: #2d8a2d;
                color: #63ff96;
            }

            .card:nth-child(2) { animation: fadeInUp 0.5s ease-out 0.05s both; }
            .card:nth-child(3) { animation: fadeInUp 0.5s ease-out 0.1s both; }
            .card:nth-child(4) { animation: fadeInUp 0.5s ease-out 0.15s both; }
            .card:nth-child(5) { animation: fadeInUp 0.5s ease-out 0.2s both; }
            .card:nth-child(6) { animation: fadeInUp 0.5s ease-out 0.25s both; }
            .card:nth-child(7) { animation: fadeInUp 0.5s ease-out 0.3s both; }
            .card:nth-child(8) { animation: fadeInUp 0.5s ease-out 0.35s both; }
            .card:nth-child(9) { animation: fadeInUp 0.5s ease-out 0.4s both; }

            .tabs { display: flex; gap: 0; margin-bottom: 16px; border-radius: 8px; overflow: hidden; border: 1px solid #2a2a2a; }
            .tab { flex: 1; padding: 12px; background: #1a1a1a; border: none; color: #666; font-size: 0.9em; font-weight: 500; font-family: 'Inter', sans-serif; cursor: pointer; transition: all 0.2s ease; }
            .tab:hover { background: #222; }
            .tab.active { background: #2a2a2a; color: #fff; }

            .tab-content { display: none; }
            .tab-content.active { display: block; }

            .config-file-row { display: flex; gap: 8px; align-items: center; }
            .config-file-row select { flex: 1; }
            .current-badge { 
                background: #2a2a1a; 
                border: 1px solid #4a4a2a; 
                color: #aaaa44; 
                padding: 4px 8px; 
                border-radius: 4px; 
                font-size: 0.75em;
                margin-left: 8px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="info-wrap" style="justify-content:center;margin-bottom:32px;">
                <h1 style="margin-bottom:0;">truly</h1>
                <span class="info-icon">i<span class="tooltip">All values are for flash hider + vertical grip</span></span>
            </div>

            <div class="card">
                <div class="card-label">Status</div>
                <button id="toggle-btn">LOADING</button>
                <div class="toggle-row">
                    <span>Toggle key</span>
                    <select id="toggle-button-select">
                        <option value="MMB">Middle Mouse</option>
                        <option value="M4">M4 (Side Back)</option>
                        <option value="M5" selected>M5 (Side Forward)</option>
                    </select>
                </div>
            </div>

            <div class="tabs">
                <button class="tab active" data-tab="recoil">Recoil</button>
                <button class="tab" data-tab="settings">Settings</button>
            </div>

            <div id="tab-recoil" class="tab-content active">
            <div class="card">
                <div class="card-label">Vertical (Pull-down)</div>
                <input type="number" class="number-input" id="slider-value" value="1" min="0" max="300" step="0.001">
                <input type="range" min="0" max="300" value="1" id="recoil-slider">
            </div>

            <div class="card">
                <div class="card-label">Horizontal (Side-to-side)</div>
                <input type="number" class="number-input" id="horizontal-value" value="0" min="-300" max="300" step="0.001">
                <input type="range" min="-300" max="300" value="0" id="horizontal-slider">
                <div class="slider-hint">Negative = left, Positive = right, 0 = off</div>
            </div>

            <div class="card">
                <div class="card-label">Horizontal Delay</div>
                <div class="number-row"><input type="number" class="number-input" id="delay-value" value="500" min="0" max="5000"><span class="delay-unit">ms</span></div>
                <input type="range" min="0" max="5000" step="1" value="500" id="delay-slider">
                <div class="slider-hint">How long LMB is held before horizontal kicks in</div>
            </div>

            <div class="card">
                <div class="card-label">Horizontal Duration</div>
                <div class="number-row"><input type="number" class="number-input" id="duration-value" value="2000" min="0" max="10000"><span class="delay-unit">ms</span></div>
                <input type="range" min="0" max="10000" step="1" value="2000" id="duration-slider">
                <div class="slider-hint">How long horizontal lasts (0 = forever)</div>
            </div>
            </div>

            <div id="tab-settings" class="tab-content">
            <div class="card">
                <div class="card-label">Operator Slot Config</div>
                <div class="slot-buttons" style="display:flex;gap:8px;margin-bottom:12px;">
                    <button class="btn" id="slot-primary-btn" style="flex:1;">Primary (1)</button>
                    <button class="btn" id="slot-secondary-btn" style="flex:1;">Secondary (2)</button>
                </div>
                <div class="current-slot-display" style="font-size:0.8em;color:#666;margin-bottom:8px;">
                    Current: <span id="current-slot-text">Primary</span> - <span id="current-gun-text">No operator selected</span>
                </div>
            </div>

            <div class="card">
                <div class="card-label">Game Config <span class="current-badge" id="current-config-badge"></span></div>
                <div class="config-file-row">
                    <select id="config-files-dropdown"></select>
                </div>
                <div class="config-file-row" style="margin-top:8px;">
                    <input type="text" id="new-config-name" placeholder="New config name...">
                    <button class="btn btn-save" id="create-config-btn">Create</button>
                    <button class="btn btn-delete" id="delete-config-btn">Delete</button>
                </div>
            </div>

            <div class="card">
                <div class="card-label">Operator Config</div>
                <input type="text" id="operator-search" placeholder="Search operators..." style="width:100%;margin-bottom:8px;">
                <select id="operators-dropdown"></select>
            </div>

            <div class="card">
                <div class="card-label">Save Operator Config</div>
                <div class="save-row">
                    <input type="text" id="operator-name" placeholder="Operator name...">
                    <button class="btn btn-save" id="save-operator-btn">Save</button>
                </div>
                <div style="margin-top:8px;">
                    <div style="display:flex;gap:4px;margin-bottom:4px;">
                        <input type="text" id="primary-gun-name" placeholder="Primary gun..." style="flex:1;">
                        <input type="text" id="secondary-gun-name" placeholder="Secondary gun..." style="flex:1;">
                    </div>
                    <div style="font-size:0.75em;color:#666;">Enter gun names for primary and secondary weapons</div>
                </div>
            </div>

            <div class="card" style="text-align:center;display:flex;gap:8px;justify-content:center;">
                <button class="btn btn-overwrite" id="overwrite-operator-btn">Overwrite Selected</button>
                <button class="btn btn-delete" id="delete-operator-btn">Delete Selected</button>
            </div>
            </div>
        </div>

        <script>
            document.addEventListener('DOMContentLoaded', function() {
                const slider = document.getElementById("recoil-slider");
                const sliderValue = document.getElementById("slider-value");
                const hSlider = document.getElementById("horizontal-slider");
                const hValue = document.getElementById("horizontal-value");
                const delaySlider = document.getElementById("delay-slider");
                const delayValue = document.getElementById("delay-value");
                const durationSlider = document.getElementById("duration-slider");
                const durationValue = document.getElementById("duration-value");
                const operatorNameInput = document.getElementById("operator-name");
                const primaryGunNameInput = document.getElementById("primary-gun-name");
                const secondaryGunNameInput = document.getElementById("secondary-gun-name");
                const saveOperatorBtn = document.getElementById("save-operator-btn");
                const deleteOperatorBtn = document.getElementById("delete-operator-btn");
                const overwriteOperatorBtn = document.getElementById("overwrite-operator-btn");
                const operatorsDropdown = document.getElementById("operators-dropdown");
                const operatorSearch = document.getElementById("operator-search");
                const slotPrimaryBtn = document.getElementById("slot-primary-btn");
                const slotSecondaryBtn = document.getElementById("slot-secondary-btn");
                const currentSlotText = document.getElementById("current-slot-text");
                const currentGunText = document.getElementById("current-gun-text");
                const toggleBtn = document.getElementById("toggle-btn");
                const toggleButtonSelect = document.getElementById("toggle-button-select");
                const configFilesDropdown = document.getElementById("config-files-dropdown");
                const newConfigNameInput = document.getElementById("new-config-name");
                const createConfigBtn = document.getElementById("create-config-btn");
                const deleteConfigBtn = document.getElementById("delete-config-btn");
                const currentConfigBadge = document.getElementById("current-config-badge");
                let preferredOperatorName = '';

                let ws;
                function connectWs() {
                    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
                    ws = new WebSocket(proto + '//' + location.host + '/ws');
                    ws.onopen = () => sendAll();
                    ws.onclose = () => setTimeout(connectWs, 2000);
                }
                connectWs();

                function sendAll() {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({
                            pull_down: parseFloat(sliderValue.value) || 0,
                            horizontal: parseFloat(hValue.value) || 0,
                            horizontal_delay_ms: parseInt(delayValue.value) || 0,
                            horizontal_duration_ms: parseInt(durationValue.value) || 0
                        }));
                    }
                }

                function syncPair(sliderEl, inputEl) {
                    sliderEl.oninput = () => { inputEl.value = sliderEl.value; sendAll(); };
                    inputEl.oninput = () => { sliderEl.value = inputEl.value; sendAll(); };
                }
                syncPair(slider, sliderValue);
                syncPair(hSlider, hValue);
                syncPair(delaySlider, delayValue);
                syncPair(durationSlider, durationValue);
                toggleBtn.onclick = toggleStatus;
                saveOperatorBtn.onclick = saveOperatorConfig;
                deleteOperatorBtn.onclick = deleteOperatorConfig;
                overwriteOperatorBtn.onclick = overwriteOperatorConfig;
                operatorsDropdown.onchange = activateOperatorConfig;
                slotPrimaryBtn.onclick = () => switchSlot('primary');
                slotSecondaryBtn.onclick = () => switchSlot('secondary');
                toggleButtonSelect.onchange = changeToggleButton;

                document.querySelectorAll('.tab').forEach(tab => {
                    tab.onclick = () => {
                        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                        tab.classList.add('active');
                        document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
                    };
                });

                function updateToggleButton(isEnabled) {
                    toggleBtn.textContent = isEnabled ? 'ON' : 'OFF';
                    toggleBtn.className = isEnabled ? 'enabled' : 'disabled';
                }

                function getStatus() {
                    fetch('/status').then(r => r.json()).then(data => {
                        updateToggleButton(data.is_enabled);
                        if (data.toggle_button) toggleButtonSelect.value = data.toggle_button;
                        if (data.current_config_file) currentConfigBadge.textContent = data.current_config_file.replace('.json', '');
                        preferredOperatorName = data.selected_operator_name || preferredOperatorName;
                        updateSlotDisplay(data.current_slot, data.selected_operator_name);
                    }).catch(() => {});
                }

                function changeToggleButton() {
                    fetch('/toggle-button', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ button: toggleButtonSelect.value })
                    }).catch(() => {});
                }

                function toggleStatus() {
                    fetch('/toggle', { method: 'POST' }).then(r => r.json()).then(data => updateToggleButton(data.is_enabled)).catch(() => {});
                }

                let allOperators = [];

                function fetchOperators() {
                    fetch('/configs').then(r => r.json()).then(data => {
                        allOperators = Object.keys(data);
                        filterOperators();
                        if (preferredOperatorName && allOperators.includes(preferredOperatorName)) {
                            operatorsDropdown.value = preferredOperatorName;
                            activateOperatorConfig();
                        }
                    }).catch(() => {});
                }

                function filterOperators() {
                    const q = operatorSearch.value.toLowerCase();
                    const prev = operatorsDropdown.value;
                    operatorsDropdown.innerHTML = '<option value="">-- Select an Operator --</option>';
                    for (const operator of allOperators) {
                        if (!q || operator.toLowerCase().includes(q)) {
                            const option = document.createElement('option');
                            option.value = operator;
                            option.textContent = operator;
                            operatorsDropdown.appendChild(option);
                        }
                    }
                    operatorsDropdown.value = prev;
                }

                function fetchConfigFiles() {
                    fetch('/config-files').then(r => r.json()).then(data => {
                        const prev = configFilesDropdown.value;
                        configFilesDropdown.innerHTML = '';
                        for (const file of data.files) {
                            const option = document.createElement('option');
                            option.value = file;
                            option.textContent = file.replace('.json', '');
                            configFilesDropdown.appendChild(option);
                        }
                        configFilesDropdown.value = data.current;
                        currentConfigBadge.textContent = data.current.replace('.json', '');
                    }).catch(() => {});
                }

                function createConfigFile() {
                    const name = newConfigNameInput.value.trim();
                    if (!name) return alert('Please enter a config name.');
                    fetch('/config-files', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ filename: name })
                    }).then(r => r.json()).then(data => {
                        alert(data.message || data.detail);
                        fetchConfigFiles();
                        newConfigNameInput.value = '';
                    }).catch(() => alert('Failed to create config.'));
                }

                function deleteConfigFile() {
                    const selectedFile = configFilesDropdown.value;
                    if (!selectedFile) return alert('Please select a config file to delete.');
                    if (selectedFile === 'r6_operators.json') return alert('Cannot delete default config.');
                    if (confirm('Delete config file "' + selectedFile + '" and all its operators?')) {
                        fetch('/config-files/' + encodeURIComponent(selectedFile), { method: 'DELETE' }).then(r => r.json()).then(data => {
                            alert(data.message || data.detail);
                            fetchConfigFiles();
                            fetchOperators();
                        }).catch(() => alert('Failed to delete config.'));
                    }
                }

                function switchConfigFile() {
                    const selectedFile = configFilesDropdown.value;
                    if (!selectedFile) return alert('Please select a config file to load.');
                    fetch('/config-files/switch', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ filename: selectedFile })
                    }).then(r => r.json()).then(data => {
                        currentConfigBadge.textContent = data.current_config_file.replace('.json', '');
                        preferredOperatorName = data.selected_operator_name || '';
                        allOperators = Object.keys(data.operators);
                        filterOperators();
                        if (preferredOperatorName && allOperators.includes(preferredOperatorName)) {
                            operatorsDropdown.value = preferredOperatorName;
                            activateOperatorConfig();
                        } else {
                            operatorsDropdown.value = '';
                        }
                    }).catch(() => alert('Failed to switch config.'));
                }

                configFilesDropdown.onchange = switchConfigFile;
                createConfigBtn.onclick = createConfigFile;
                deleteConfigBtn.onclick = deleteConfigFile;

                operatorSearch.oninput = filterOperators;

                function updateSlotDisplay(slot, operatorName) {
                    const slotText = slot === 'primary' ? 'Primary' : 'Secondary';
                    currentSlotText.textContent = slotText;
                    
                    if (operatorName) {
                        fetch('/configs').then(r => r.json()).then(data => {
                            const operatorConfig = data[operatorName];
                            if (operatorConfig && operatorConfig[slot]) {
                                currentGunText.textContent = operatorConfig[slot].gun_name;
                            } else {
                                currentGunText.textContent = 'No config';
                            }
                        });
                    } else {
                        currentGunText.textContent = 'No operator selected';
                    }
                    
                    // Update button styles
                    slotPrimaryBtn.classList.toggle('active', slot === 'primary');
                    slotSecondaryBtn.classList.toggle('active', slot === 'secondary');
                }

                function switchSlot(slot) {
                    fetch('/operators/switch-slot', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ slot: slot })
                    }).then(r => r.json()).then(data => {
                        preferredOperatorName = data.operator_name;
                        sliderValue.value = data.pull_down;
                        hValue.value = data.horizontal;
                        delayValue.value = data.horizontal_delay_ms;
                        durationValue.value = data.horizontal_duration_ms;
                        slider.value = Math.round(data.pull_down);
                        hSlider.value = Math.round(data.horizontal);
                        delaySlider.value = data.horizontal_delay_ms;
                        durationSlider.value = data.horizontal_duration_ms;
                        updateSlotDisplay(data.current_slot, data.operator_name);
                        sendAll();
                    }).catch(() => alert('Failed to switch slot.'));
                }

                function saveOperatorConfig() {
                    const operatorName = operatorNameInput.value.trim();
                    const primaryGun = primaryGunNameInput.value.trim();
                    const secondaryGun = secondaryGunNameInput.value.trim();
                    
                    if (!operatorName) return alert('Please enter an operator name.');
                    if (!primaryGun || !secondaryGun) return alert('Please enter both primary and secondary gun names.');
                    
                    fetch('/operators', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            operator_name: operatorName,
                            primary_gun: primaryGun,
                            primary_config: {
                                pull_down: parseFloat(sliderValue.value) || 0,
                                horizontal: parseFloat(hValue.value) || 0,
                                horizontal_delay_ms: parseInt(delayValue.value) || 0,
                                horizontal_duration_ms: parseInt(durationValue.value) || 0
                            },
                            secondary_gun: secondaryGun,
                            secondary_config: {
                                pull_down: parseFloat(sliderValue.value) || 0,
                                horizontal: parseFloat(hValue.value) || 0,
                                horizontal_delay_ms: parseInt(delayValue.value) || 0,
                                horizontal_duration_ms: parseInt(durationValue.value) || 0
                            }
                        })
                    }).then(r => r.json()).then(data => {
                        alert(data.message || data.detail);
                        preferredOperatorName = operatorName;
                        fetchOperators();
                        operatorsDropdown.value = operatorName;
                        updateSlotDisplay('primary', operatorName);
                    }).catch(() => alert('Failed to save operator config.'));
                }

                function deleteOperatorConfig() {
                    const selectedOperator = operatorsDropdown.value;
                    if (!selectedOperator) return alert('Please select an operator config to delete.');
                    if (confirm('Delete config for ' + selectedOperator + '?')) {
                        fetch('/operators/' + encodeURIComponent(selectedOperator), { method: 'DELETE' }).then(r => r.json()).then(data => {
                            alert(data.message || data.detail);
                            if (preferredOperatorName === selectedOperator) preferredOperatorName = '';
                            fetchOperators();
                            updateSlotDisplay('', '');
                        }).catch(() => alert('Failed to delete operator config.'));
                    }
                }

                function overwriteOperatorConfig() {
                    const selectedOperator = operatorsDropdown.value;
                    if (!selectedOperator) return alert('Please select an operator config to overwrite.');
                    
                    fetch('/configs').then(r => r.json()).then(data => {
                        const existingConfig = data[selectedOperator];
                        if (!existingConfig) return alert('Operator config not found.');
                        
                        const currentSlot = currentSlotText.textContent.toLowerCase();
                        const gunName = currentGunText.textContent;
                        
                        if (confirm('Overwrite ' + currentSlot + ' config for ' + selectedOperator + ' with current values?')) {
                            const newConfig = {
                                operator_name: selectedOperator,
                                primary_gun: existingConfig.primary.gun_name,
                                primary_config: currentSlot === 'primary' ? {
                                    pull_down: parseFloat(sliderValue.value) || 0,
                                    horizontal: parseFloat(hValue.value) || 0,
                                    horizontal_delay_ms: parseInt(delayValue.value) || 0,
                                    horizontal_duration_ms: parseInt(durationValue.value) || 0
                                } : existingConfig.primary.config,
                                secondary_gun: existingConfig.secondary.gun_name,
                                secondary_config: currentSlot === 'secondary' ? {
                                    pull_down: parseFloat(sliderValue.value) || 0,
                                    horizontal: parseFloat(hValue.value) || 0,
                                    horizontal_delay_ms: parseInt(delayValue.value) || 0,
                                    horizontal_duration_ms: parseInt(durationValue.value) || 0
                                } : existingConfig.secondary.config
                            };
                            
                            fetch('/operators', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify(newConfig)
                            }).then(r => r.json()).then(data => {
                                alert(data.message || data.detail);
                            }).catch(() => alert('Failed to overwrite operator config.'));
                        }
                    }).catch(() => alert('Failed to fetch existing config.'));
                }

                function activateOperatorConfig() {
                    const selectedOperator = operatorsDropdown.value;
                    if (!selectedOperator) return;
                    fetch('/operators/activate', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ operator_name: selectedOperator })
                    }).then(r => r.json()).then(data => {
                        preferredOperatorName = data.operator_name;
                        sliderValue.value = data.pull_down;
                        hValue.value = data.horizontal;
                        delayValue.value = data.horizontal_delay_ms;
                        durationValue.value = data.horizontal_duration_ms;
                        slider.value = Math.round(data.pull_down);
                        hSlider.value = Math.round(data.horizontal);
                        delaySlider.value = data.horizontal_delay_ms;
                        durationSlider.value = data.horizontal_duration_ms;
                        updateSlotDisplay(data.current_slot, data.operator_name);
                        sendAll();
                    }).catch(() => {});
                }

                document.addEventListener('keydown', function(e) {
                    if (e.key === '1' && !e.ctrlKey && !e.altKey && !e.metaKey) {
                        e.preventDefault();
                        switchSlot('primary');
                    } else if (e.key === '2' && !e.ctrlKey && !e.altKey && !e.metaKey) {
                        e.preventDefault();
                        switchSlot('secondary');
                    }
                });
            });
        </script>
    </body>
    </html>
    """, headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    local_ip = get_local_ip()
    print(f"\n  Open on this PC:      http://localhost:8000")
    print(f"  Open on local network: http://{local_ip}:8000")
    print(f"  For phone access: Connect your PC to your phone's hotspot, then use the hotspot IP (usually 192.168.43.x or similar) in your phone's browser.")
    print(f"  Alternatively, set up port forwarding on your router for port 8000 to access from anywhere.\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
