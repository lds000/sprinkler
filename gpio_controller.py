### gpio_controller.py

# LED pinout (BCM numbering):
# LED 1: R=4,  G=17, B=18   # Near GPIO4
# LED 2: R=23, G=24, B=12   # Near GPIO23
# LED 3: R=25, G=8,  B=7    # Near GPIO25
# Status: R=21, G=20, B=16  # Near GPIO21
#
# Example:
# RGB_LEDS = [
#     {'R': 21, 'G': 20, 'B': 16},   # Status LED (near GPIO21)
#     {'R': 4,  'G': 17, 'B': 18},   # LED 1 (near GPIO4)
#     {'R': 23, 'G': 24, 'B': 12},   # LED 2 (near GPIO23)
#     {'R': 25, 'G': 8,  'B': 7},    # LED 3 (near GPIO25)
# ]

import RPi.GPIO as GPIO
import time
from datetime import datetime
from logger import log
import os
import threading
from config import RELAYS  # <-- Add this import

STATUS_LOG = "/home/lds00/sprinkler/status_test_mode.log"
TEST_MODE_FILE = "/home/lds00/sprinkler/test_mode.txt"

_last_states = {}  # Track relay states to suppress duplicate logs

# Define GPIO pins for 4 RGB LEDs (reassigned for physical layout)
RGB_LEDS = [
    {'R': 21, 'G': 20, 'B': 16},   # Status LED (near GPIO21)
    {'R': 4,  'G': 17, 'B': 18},   # LED 1 (near GPIO4)
    {'R': 23, 'G': 24, 'B': 12},   # LED 2 (near GPIO23)
    {'R': 25, 'G': None,  'B': None},    # LED 3 (near GPIO25, G/B not connected)
]

# LED assignments:
# LED 0: System status (near GPIO21)
# LED 1: Hanging Pots (near GPIO4)
# LED 2: Garden (near GPIO23)
# LED 3: Misters (near GPIO25)
SET_LED_MAP = {
    "Hanging Pots": 1,
    "Garden": 2,
    "Misters": 3
}

# PWM support for brightness (0-100)
PWM_FREQ = 100
_pwm_channels = {}
def setup_pwm():
    for led in RGB_LEDS:
        for color in ['R', 'G', 'B']:
            pin = led[color]
            if not isinstance(pin, int):
                continue  # Skip if pin is None or not an integer
            if pin not in _pwm_channels:
                try:
                    _pwm_channels[pin] = GPIO.PWM(pin, PWM_FREQ)
                    _pwm_channels[pin].start(0)
                except Exception as e:
                    log(f"[WARN] Could not setup PWM for pin {pin} ({color}): {e}")

def set_rgb_pwm(led_idx, r, g, b, brightness=100):
    pins = RGB_LEDS[led_idx]
    for color, val in zip(['R', 'G', 'B'], [r, g, b]):
        duty = brightness if val else 0
        pin = pins[color]
        if not isinstance(pin, int):
            continue  # Skip if pin is None or not an integer
        if pin in _pwm_channels:
            try:
                _pwm_channels[pin].ChangeDutyCycle(duty)
            except Exception as e:
                log(f"[WARN] Could not set PWM duty cycle for pin {pin} ({color}) on LED {led_idx}: {e}")
        else:
            try:
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.HIGH if val else GPIO.LOW)
            except Exception as e:
                log(f"[WARN] Could not set {color} (digital fallback) on pin {pin} for LED {led_idx}: {e}")

def set_rgb(led_idx, r, g, b, brightness=100):
    pins = RGB_LEDS[led_idx]
    for color, val in zip(['R', 'G', 'B'], [r, g, b]):
        pin = pins[color]
        if not isinstance(pin, int):
            continue  # Skip if pin is None or not an integer
        if _pwm_channels:
            set_rgb_pwm(led_idx, r, g, b, brightness)
            break  # set_rgb_pwm handles all channels at once
        else:
            try:
                GPIO.output(pin, GPIO.HIGH if val else GPIO.LOW)
            except Exception as e:
                log(f"[WARN] Could not set {color} on pin {pin} for LED {led_idx}: {e}")

def all_leds_off():
    for i in range(len(RGB_LEDS)):
        try:
            set_rgb(i, 0, 0, 0)
        except Exception as e:
            log(f"[WARN] Could not turn off LED {i}: {e}")

def is_test_mode():
    try:
        with open(TEST_MODE_FILE) as f:
            val = f.read().strip()
        mtime = os.path.getmtime(TEST_MODE_FILE)
        log(f"[DEBUG] is_test_mode read test_mode.txt: '{val}', mtime: {mtime}")
        return val == "1"
    except Exception as e:
        log(f"[DEBUG] is_test_mode failed to read test_mode.txt: {e}")
        return False

def initialize_gpio(RELAYS):
    log("[SYSTEM] Controller starting up...")
    GPIO.setmode(GPIO.BCM)
    for led in RGB_LEDS:
        for color in ['R', 'G', 'B']:
            pin = led[color]
            if isinstance(pin, int) and pin > 0:
                try:
                    GPIO.setup(pin, GPIO.OUT)
                    GPIO.output(pin, GPIO.LOW)
                except Exception as e:
                    log(f"[WARN] Could not setup LED pin {pin} ({color}): {e}")
            else:
                log(f"[WARN] Skipping invalid LED pin: {pin} ({color})")
    setup_pwm()  # Now safe to call multiple times
    for name, pin in RELAYS.items():
        if isinstance(pin, int) and pin > 0:
            try:
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            except Exception as e:
                log(f"[WARN] Could not setup relay pin {pin} ({name}): {e}")
        else:
            log(f"[WARN] Skipping invalid relay pin: {pin} ({name})")
    startup_blink()

def startup_blink():
    # Rainbow sweep
    colors = [(1,0,0),(1,1,0),(0,1,0),(0,1,1),(0,0,1),(1,0,1)]
    for _ in range(2):
        for c in colors:
            for i in range(len(RGB_LEDS)):
                set_rgb(i, *c)
            time.sleep(0.1)
    all_leds_off()
    # All blink red for boot
    for _ in range(3):
        for i in range(len(RGB_LEDS)):
            set_rgb(i, 1, 0, 0)
        time.sleep(0.2)
        all_leds_off()
        time.sleep(0.2)

def turn_on(pin, name=None):
    label = name or f"PIN_{pin}"
    if is_test_mode():
        if _last_states.get(pin) != "ON":
            log(f"[TEST MODE] {label} ON")
        _last_states[pin] = "ON"
    else:
        GPIO.output(pin, GPIO.HIGH)
        _last_states[pin] = "ON"

def turn_off(pin, name=None):
    label = name or f"PIN_{pin}"
    if is_test_mode():
        if _last_states.get(pin) != "OFF":
            log(f"[TEST MODE] {label} OFF")
        _last_states[pin] = "OFF"
    else:
        try:
            GPIO.output(pin, GPIO.LOW)
        except Exception as e:
            log(f"[WARN] Could not turn off relay pin {pin} ({name}): {e}")

# Set status LED (LED 0) color
# color: 'idle', 'running', 'off', 'wifi', 'test', 'maintenance', 'error'
def set_status_led(color):
    if color == 'idle':
        set_rgb(0, 1, 0, 0, 30)  # Dim red
    elif color == 'running':
        set_rgb(0, 0, 1, 0, 100)  # Bright green
    elif color == 'wifi':
        set_rgb(0, 1, 1, 0, 100)  # Yellow
    elif color == 'test':
        set_rgb(0, 0, 0, 1, 100)  # Blue
    elif color == 'maintenance':
        set_rgb(0, 1, 1, 1, 100)  # White
    elif color == 'error':
        set_rgb(0, 1, 1, 0, 100)  # Yellow
    else:
        set_rgb(0, 0, 0, 0)

# Enhanced set LED logic
# Accepts: current_set, running, test_mode, error_zones, manual_set, soon_set, maintenance, brightness
# Unique color per set: Green (Hanging Pots), Blue (Garden), Cyan (Misters)
# Error: yellow, Test: blue, Manual: fast blink, Scheduled soon: orange pulse, Maintenance: white blink

def update_set_leds(current_set, running, test_mode=False, error_zones=None, manual_set=None, soon_set=None, maintenance=False, brightness=100):
    # Consistent color scheme for all sets:
    # Watering: green, Soaking: purple, Idle: dim red, Test: blue, Error: yellow, Maintenance: white, Manual: green (blink), Soon: orange
    from status import CURRENT_RUN
    phase = CURRENT_RUN.get("Phase", "")
    for set_name, led_idx in SET_LED_MAP.items():
        if error_zones and set_name in error_zones:
            set_rgb(led_idx, 1, 1, 0, brightness)  # Yellow for error
        elif test_mode:
            set_rgb(led_idx, 0, 0, 1, brightness)  # Blue for test mode
        elif maintenance:
            set_rgb(led_idx, 1, 1, 1, brightness)  # White for maintenance
        elif manual_set and set_name == manual_set:
            set_rgb(led_idx, 0, 1, 0, brightness)  # Green (fast blink handled in controller)
        elif soon_set and set_name == soon_set:
            set_rgb(led_idx, 1, 0.5, 0, brightness)  # Orange (PWM only)
        elif running and current_set == set_name:
            if phase == "Soaking":
                set_rgb(led_idx, 1, 0, 1, brightness)  # Purple for soaking
            else:
                set_rgb(led_idx, 0, 1, 0, brightness)  # Green for watering
        else:
            set_rgb(led_idx, 1, 0, 0, 30)  # Dim red for idle

# Example: error_zones=["Garden"], test_mode=True, manual_set="Misters", soon_set="Hanging Pots", maintenance=True

# Blinking logic for status LED and set LEDs
# Call in a thread

def status_led_controller(CURRENT_RUN, test_mode=False, error_zones=None, manual_set=None, soon_set=None, maintenance=False):
    blink_state = False
    while True:
        running = CURRENT_RUN["Running"]
        current_set = CURRENT_RUN["Set"]
        # Maintenance blink
        if maintenance:
            set_status_led('maintenance')
            update_set_leds(current_set, running, maintenance=True)
            time.sleep(0.5)
            all_leds_off()
            time.sleep(0.5)
            continue
        # Error blink
        if error_zones:
            set_status_led('error')
            update_set_leds(current_set, running, error_zones=error_zones)
            time.sleep(0.5)
            all_leds_off()
            time.sleep(0.5)
            continue
        # Test mode pulse
        if test_mode:
            set_status_led('test')
            update_set_leds(current_set, running, test_mode=True)
            time.sleep(0.5)
            all_leds_off()
            time.sleep(0.5)
            continue
        # Manual run fast blink
        if manual_set:
            set_status_led('running')
            update_set_leds(current_set, running, manual_set=manual_set)
            time.sleep(0.1)
            all_leds_off()
            time.sleep(0.1)
            continue
        # Scheduled soon pulse
        if soon_set:
            set_status_led('idle')
            update_set_leds(current_set, running, soon_set=soon_set)
            time.sleep(0.3)
            all_leds_off()
            time.sleep(0.3)
            continue
        # Normal running/idle
        if running:
            set_status_led('running')
            update_set_leds(current_set, running)
            time.sleep(0.2)
            set_status_led('off')
            update_set_leds(current_set, running)
            time.sleep(0.2)
        else:
            set_status_led('idle')
            update_set_leds(current_set, running)
            time.sleep(1)

def get_led_colors(current_set, running, test_mode=False, error_zones=None, manual_set=None, soon_set=None, maintenance=False):
    from status import CURRENT_RUN
    phase = CURRENT_RUN.get("Phase", "")
    colors = {}
    # System LED
    if maintenance:
        colors['system'] = 'white'
    elif error_zones:
        colors['system'] = 'yellow'
    elif test_mode:
        colors['system'] = 'blue'  # Test mode takes precedence for system
    elif running:
        colors['system'] = 'green'
    else:
        colors['system'] = 'red'
    # Set LEDs (all sets use same color for each phase)
    for set_name, led_idx in SET_LED_MAP.items():
        if error_zones and set_name in error_zones:
            colors[set_name] = 'yellow'
        elif test_mode:
            colors[set_name] = 'blue'
        elif maintenance:
            colors[set_name] = 'white'
        elif manual_set and set_name == manual_set:
            colors[set_name] = 'green'  # Fast blink in UI
        elif soon_set and set_name == soon_set:
            colors[set_name] = 'orange'
        elif running and current_set == set_name:
            if phase == "Soaking":
                colors[set_name] = 'purple'
            else:
                colors[set_name] = 'green'
        else:
            colors[set_name] = 'red'
    return colors

def ensure_all_relays_off():
    log("[DEBUG] ensure_all_relays_off called at startup.")
    for name, pin in RELAYS.items():
        try:
            # Only turn off if not already off
            import RPi.GPIO as GPIO
            state = GPIO.input(pin)
            if state == GPIO.HIGH:
                log(f"[DEBUG] Turning OFF relay {name} (pin {pin}) in ensure_all_relays_off.")
                turn_off(pin, name=name)
            else:
                log(f"[DEBUG] Relay {name} (pin {pin}) already OFF in ensure_all_relays_off.")
        except Exception as e:
            log(f"[WARN] Could not turn off pin {pin} at startup: {e}")
