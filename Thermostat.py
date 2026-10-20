"""CS 350 final project: Raspberry Pi 4B thermostat.

Adapted from SNHU's Module-7/Thermostat.py course template. Retains its
TemperatureMachine, off/heat/cool states, cycle event, and BCM pin assignments.
GPIO Zero callbacks queue button events; the main loop owns the state machine,
I2C readings, LCD, and UART. PWMLED supplies its own background fading.

Control, LCD, and UART use floor(Fahrenheit), following the template's
whole-degree comparison. A solid LED means the selected mode is satisfied;
only a pulsing LED represents active heating/cooling. No adaptive mode.

Run: python Thermostat.py [--port /dev/ttyS0]
Dependencies: gpiozero, python-statemachine, pyserial, Adafruit-Blinka,
adafruit-circuitpython-ahtx0, adafruit-circuitpython-charlcd.
"""

import argparse
from contextlib import ExitStack
from datetime import datetime
from math import floor, isfinite
from queue import Empty, Queue
from time import monotonic

from statemachine import State, StateMachine


RED_PIN = 18
BLUE_PIN = 23
MODE_PIN = 24
INCREASE_PIN = 25
DECREASE_PIN = 12
DEFAULT_SET_POINT = 72
SAMPLE_SECONDS = 1.0
DISPLAY_PAGE_SECONDS = 5.0
SERIAL_SECONDS = 30.0
DEBOUNCE_SECONDS = 0.05


class ManagedDisplay:
    """Manage the existing 16x2 LCD in four-bit GPIO mode."""

    def __init__(self):
        import board
        import digitalio
        import adafruit_character_lcd.character_lcd as characterlcd

        self._resources = ExitStack()
        try:
            pins = []
            # RS, EN, D4, D5, D6, D7: same wiring as Modules 4-6.
            for pin in (board.D17, board.D27, board.D5, board.D6,
                        board.D13, board.D26):
                output = digitalio.DigitalInOut(pin)
                self._resources.callback(output.deinit)
                pins.append(output)
            self.lcd = characterlcd.Character_LCD_Mono(*pins, 16, 2)
            self.lcd.clear()
        except BaseException:
            self._resources.close()
            raise

    def updateScreen(self, line1, line2):
        """Overwrite both rows without clearing, reducing display flicker."""
        if len(line1) > 16 or len(line2) > 16:
            raise ValueError("LCD rows must fit within 16 characters")
        self.lcd.cursor_position(0, 0)
        self.lcd.message = line1.ljust(16) + "\n" + line2.ljust(16)

    def cleanupDisplay(self):
        try:
            self.lcd.clear()
        finally:
            self._resources.close()


class TemperatureMachine(StateMachine):
    """Three operating modes with temperature-dependent indicator actions."""

    off = State(initial=True)
    heat = State()
    cool = State()
    cycle = off.to(heat) | heat.to(cool) | cool.to(off)

    def __init__(self, sensor, red, blue, screen, uart, clock=monotonic):
        self.thSensor = sensor
        self.redLight = red
        self.blueLight = blue
        self.screen = screen
        self.ser = uart
        self.clock = clock
        self.setPoint = DEFAULT_SET_POINT
        self.pending_presses = Queue()
        self._light_pattern = None
        self.currentTemp = floor(self.getFahrenheit())
        self.started = clock()
        self.next_sample = self.started + SAMPLE_SECONDS
        self.next_serial = self.started + SERIAL_SECONDS
        super().__init__()

    def on_enter_off(self):
        self.updateLights()

    def on_enter_heat(self):
        self.updateLights()

    def on_exit_heat(self):
        self.redLight.off()

    def on_enter_cool(self):
        self.updateLights()

    def on_exit_cool(self):
        self.blueLight.off()

    def processTempStateButton(self):
        """Called on a GPIO Zero event thread; do not access hardware here."""
        self.pending_presses.put("cycle")

    def processTempIncButton(self):
        self.pending_presses.put("increase")

    def processTempDecButton(self):
        self.pending_presses.put("decrease")

    def handleButtonEvent(self, event):
        """Apply each debounced press once, in the main loop."""
        if event == "cycle":
            self.send("cycle")
        elif event == "increase":
            self.setPoint += 1
        elif event == "decrease":
            self.setPoint -= 1
        else:
            raise ValueError(f"Unknown button event: {event}")
        self.updateLights()

    def getFahrenheit(self):
        """Read AHT20 Celsius through I2C and convert to Fahrenheit."""
        celsius = float(self.thSensor.temperature)
        if not isfinite(celsius):
            raise ValueError("The AHT20 returned an invalid temperature")
        return celsius * 9.0 / 5.0 + 32.0

    def updateLights(self):
        """Keep the inactive color off; pulse only while conditioning."""
        mode = self.current_state.id
        if mode == "heat":
            pattern = "heat_pulse" if self.currentTemp < self.setPoint else "heat_solid"
        elif mode == "cool":
            pattern = "cool_pulse" if self.currentTemp > self.setPoint else "cool_solid"
        else:
            pattern = "off"
        if pattern == self._light_pattern:
            return  # Do not restart an ongoing fade on every sample.
        self.redLight.off()
        self.blueLight.off()
        if pattern == "heat_pulse":
            self.redLight.pulse(fade_in_time=1, fade_out_time=1, background=True)
        elif pattern == "heat_solid":
            self.redLight.on()
        elif pattern == "cool_pulse":
            self.blueLight.pulse(fade_in_time=1, fade_out_time=1, background=True)
        elif pattern == "cool_solid":
            self.blueLight.on()
        self._light_pattern = pattern

    def setupSerialOutput(self):
        """One newline-terminated CSV record; both temperatures are in F."""
        return f"{self.current_state.id},{self.currentTemp},{self.setPoint}\n"

    def displayLines(self, elapsed, wall_time):
        # Month/day and a 24-hour clock fit in 14 characters.
        line1 = wall_time.strftime("%m/%d %H:%M:%S")
        if int(elapsed // DISPLAY_PAGE_SECONDS) % 2 == 0:
            line2 = f"Temp: {self.currentTemp}F"
        else:
            line2 = f"{self.current_state.id.upper()} Set:{self.setPoint}F"
        return line1, line2

    @staticmethod
    def advanceDeadline(deadline, interval, now):
        """Maintain elapsed-time cadence and skip missed slots after a delay."""
        return deadline + (floor((now - deadline) / interval) + 1) * interval

    def serviceTimers(self, now=None, wall_time=None):
        """Periodic work uses monotonic time so clock corrections do not matter."""
        now = self.clock() if now is None else now
        if now >= self.next_sample:
            self.currentTemp = floor(self.getFahrenheit())
            self.next_sample = self.advanceDeadline(
                self.next_sample, SAMPLE_SECONDS, now)
            self.updateLights()
        wall_time = datetime.now() if wall_time is None else wall_time
        self.screen.updateScreen(*self.displayLines(now - self.started, wall_time))
        if now >= self.next_serial:
            record = self.setupSerialOutput().encode("utf-8")
            if self.ser.write(record) != len(record):
                raise OSError("Incomplete UART write")
            print(f"UART: {record.decode('utf-8').strip()}", flush=True)
            self.next_serial = self.advanceDeadline(
                self.next_serial, SERIAL_SECONDS, now)

    def run(self):
        """Wake for a press or the next timer; all state changes are serialized."""
        self.serviceTimers()
        while True:
            deadline = min(self.next_sample, self.next_serial)
            try:
                event = self.pending_presses.get(
                    timeout=max(0.0, deadline - self.clock()))
            except Empty:
                pass
            else:
                self.handleButtonEvent(event)
            self.serviceTimers()


def configureButtons(resources, machine):
    """GPIO Zero edge callbacks process active-low inputs with pull-ups."""
    from gpiozero import Button

    buttons = []
    for pin, callback in (
        (MODE_PIN, machine.processTempStateButton),
        (INCREASE_PIN, machine.processTempIncButton),
        (DECREASE_PIN, machine.processTempDecButton),
    ):
        button = resources.enter_context(
            Button(pin, pull_up=True, bounce_time=DEBOUNCE_SECONDS))
        buttons.append(button)
        button.when_pressed = callback
        resources.callback(setattr, button, "when_pressed", None)
    return buttons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyS0", help="Pi UART device")
    args = parser.parse_args()

    import board
    import adafruit_ahtx0
    import serial
    from gpiozero import PWMLED

    with ExitStack() as resources:
        red = resources.enter_context(PWMLED(RED_PIN, initial_value=0))
        blue = resources.enter_context(PWMLED(BLUE_PIN, initial_value=0))
        i2c = board.I2C()
        resources.callback(i2c.deinit)
        sensor = adafruit_ahtx0.AHTx0(i2c)
        screen = ManagedDisplay()
        resources.callback(screen.cleanupDisplay)
        uart = resources.enter_context(serial.Serial(
            port=args.port, baudrate=115200,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=1, write_timeout=1,
            xonxoff=False, rtscts=False, dsrdtr=False,
        ))
        machine = TemperatureMachine(sensor, red, blue, screen, uart)
        buttons = configureButtons(resources, machine)
        print("Thermostat: OFF, set point 72F. GPIO 24=mode, 25=+1F, 12=-1F.")
        print("UART: 115200 baud, 8N1; first record after 30 seconds. Ctrl+C stops.")
        try:
            machine.run()
        finally:
            for button in buttons:
                button.when_pressed = None
            red.off()
            blue.off()
            # ExitStack then closes buttons/UART, clears the LCD, and frees pins.


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nThermostat stopped; outputs released.")
    except Exception as error:
        raise SystemExit(f"Thermostat stopped after an error: {error}") from error
