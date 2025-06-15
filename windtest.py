    import RPi.GPIO as GPIO
    import time

    # GPIO pin connected to the wind speed transmitter's pulse output (green wire)
    PULSE_PIN = 5  # Change this to your actual wiring

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PULSE_PIN, GPIO.IN)


    pulse_count = 0

    def pulse_callback(channel):
        global pulse_count
        pulse_count += 1

    GPIO.add_event_detect(PULSE_PIN, GPIO.FALLING, callback=pulse_callback)

    print("Wind speed test started. Press Ctrl+C to stop.")
    try:
        while True:
            start_count = pulse_count
            pin_state = GPIO.input(PULSE_PIN)
            time.sleep(1)  # Count for 1 second
            pulses = pulse_count - start_count
            # 20 pulses = 1 rotation = 1.75 m/s
            wind_speed = (pulses / 20) * 1.75
            print(f"Pulses: {pulses}, Wind Speed: {wind_speed:.2f} m/s, GPIO {PULSE_PIN} state: {pin_state}")
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
    finally:
        GPIO.cleanup()
