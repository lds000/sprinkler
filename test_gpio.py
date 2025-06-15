import RPi.GPIO as GPIO

try:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(22, GPIO.IN)
    print("GPIO 22 setup successful")
except Exception as e:
    print(f"[ERROR] Could not set up GPIO pin 22: {e}")
finally:
    GPIO.cleanup()
