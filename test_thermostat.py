import importlib.util
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch

from gpiozero import Device, PWMLED
from gpiozero.pins.mock import MockFactory, MockPWMPin

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('thermostat', ROOT / 'Thermostat.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ThermostatTests(TestCase):
    def setUp(self):
        self.sensor = SimpleNamespace(temperature=20.0)
        self.red, self.blue, self.screen = Mock(), Mock(), Mock()
        self.uart = Mock()
        self.uart.write.side_effect = len
        self.tsm = m.TemperatureMachine(self.sensor, self.red, self.blue, self.screen, self.uart, clock=lambda: 0.0)

    def mode(self, mode):
        while self.tsm.current_state.id != mode:
            self.tsm.send('cycle')

    def test_starts_off_at_72(self):
        self.assertEqual(self.tsm.current_state.id, 'off')
        self.assertEqual(self.tsm.setPoint, 72)
        self.assertEqual(self.tsm.currentTemp, 68)
        self.red.off.assert_called()
        self.blue.off.assert_called()
        self.red.on.assert_not_called()
        self.blue.on.assert_not_called()

    def test_three_state_cycle_repeats(self):
        seen = []
        for _ in range(6):
            self.tsm.handleButtonEvent('cycle')
            seen.append(self.tsm.current_state.id)
        self.assertEqual(seen, ['heat', 'cool', 'off'] * 2)

    def test_all_nine_mode_temperature_combinations(self):
        expected = {'off': ['off'] * 3,
                    'heat': ['heat_pulse', 'heat_solid', 'heat_solid'],
                    'cool': ['cool_solid', 'cool_solid', 'cool_pulse']}
        for mode in expected:
            self.mode(mode)
            for temp, result in zip((71, 72, 73), expected[mode]):
                with self.subTest(mode=mode, temp=temp):
                    self.tsm.currentTemp = temp
                    self.tsm.updateLights()
                    self.assertEqual(self.tsm._light_pattern, result)

    def test_pulse_is_not_restarted_while_temperature_drifts(self):
        self.mode('heat')
        for temp in (68, 69, 70, 71):
            self.tsm.currentTemp = temp
            self.tsm.updateLights()
        self.assertEqual(self.red.pulse.call_count, 1)
        self.red.pulse.assert_called_with(fade_in_time=1, fade_out_time=1, background=True)

    def test_setpoint_buttons_in_each_mode(self):
        for mode in ('off', 'heat', 'cool'):
            self.mode(mode)
            self.tsm.handleButtonEvent('increase')
            self.assertEqual(self.tsm.setPoint, 73)
            self.tsm.handleButtonEvent('decrease')
            self.assertEqual(self.tsm.setPoint, 72)
            self.assertEqual(self.tsm.current_state.id, mode)

    def test_button_change_immediately_reevaluates_demand(self):
        self.mode('heat')
        self.tsm.currentTemp = 72
        self.tsm.updateLights()
        self.assertEqual(self.tsm._light_pattern, 'heat_solid')
        self.tsm.handleButtonEvent('increase')
        self.assertEqual(self.tsm._light_pattern, 'heat_pulse')
        self.tsm.handleButtonEvent('decrease')
        self.assertEqual(self.tsm._light_pattern, 'heat_solid')

    def test_gpio_callbacks_only_queue_events_in_order(self):
        self.tsm.processTempStateButton()
        self.tsm.processTempIncButton()
        self.tsm.processTempDecButton()
        self.assertEqual(self.tsm.current_state.id, 'off')
        self.assertEqual(self.tsm.setPoint, 72)
        self.assertEqual([self.tsm.pending_presses.get_nowait() for _ in range(3)], ['cycle', 'increase', 'decrease'])

    def test_real_gpiozero_edges_trigger_each_callback_once(self):
        Device.pin_factory = MockFactory(pin_class=MockPWMPin)
        try:
            with ExitStack() as resources:
                buttons = m.configureButtons(resources, self.tsm)
                for pin in (24, 25, 12):
                    Device.pin_factory.pin(pin).drive_low()
                    Device.pin_factory.pin(pin).drive_low()  # Holding is not another edge.
                    Device.pin_factory.pin(pin).drive_high()
                self.assertEqual([self.tsm.pending_presses.get_nowait() for _ in range(3)], ['cycle', 'increase', 'decrease'])
                self.assertTrue(self.tsm.pending_presses.empty())
            self.assertTrue(all(button.closed for button in buttons))
        finally:
            Device.pin_factory.close()

    def test_real_pwmled_transitions_never_leave_both_colors_on(self):
        Device.pin_factory = MockFactory(pin_class=MockPWMPin)
        try:
            with PWMLED(18) as red, PWMLED(23) as blue:
                machine = m.TemperatureMachine(self.sensor, red, blue, self.screen, self.uart, clock=lambda: 0)
                machine.send('cycle')
                self.assertEqual(blue.value, 0)
                machine.currentTemp = 72
                machine.updateLights()
                self.assertEqual((red.value, blue.value), (1, 0))
                machine.send('cycle')
                self.assertEqual((red.value, blue.value), (0, 1))
                machine.currentTemp = 74
                machine.updateLights()
                self.assertEqual(red.value, 0)
                machine.send('cycle')
                self.assertEqual((red.value, blue.value), (0, 0))
        finally:
            Device.pin_factory.close()

    def test_fahrenheit_and_floor_match_template(self):
        for c, expected in ((0, 32), (20, 68), (21.4, 70.52), (-40, -40)):
            self.sensor.temperature = c
            self.assertAlmostEqual(self.tsm.getFahrenheit(), expected)
        self.sensor.temperature = 21.4
        self.tsm.serviceTimers(1)
        self.assertEqual(self.tsm.currentTemp, 70)

    def test_sensor_change_updates_led_without_button(self):
        self.mode('heat')
        self.sensor.temperature = 23
        self.tsm.serviceTimers(1)
        self.assertEqual(self.tsm.currentTemp, 73)
        self.assertEqual(self.tsm._light_pattern, 'heat_solid')

    def test_lcd_pages_switch_at_five_seconds(self):
        wall = datetime(2026, 9, 25, 17, 10, 3)
        for t, text in ((0, 'Temp: 68F'), (4.99, 'Temp: 68F'), (5, 'OFF Set:72F'), (9.99, 'OFF Set:72F'), (10, 'Temp: 68F')):
            self.assertEqual(self.tsm.displayLines(t, wall), ('09/25 17:10:03', text))
        for temp in (-40, 0, 99, 185):
            self.tsm.currentTemp = temp
            for t in (0, 5):
                self.assertTrue(all(len(row) <= 16 for row in self.tsm.displayLines(t, wall)))

    def test_lcd_rows_overwrite_old_characters(self):
        screen = m.ManagedDisplay.__new__(m.ManagedDisplay)
        screen.lcd = Mock()
        screen.updateScreen('09/25 17:10:03', 'Temp: 68F')
        self.assertEqual([len(row) for row in screen.lcd.message.split('\n')], [16, 16])
        with self.assertRaises(ValueError):
            screen.updateScreen('X' * 17, '')

    def test_uart_exact_records_at_thirty_second_deadlines(self):
        with patch('builtins.print'):
            for t in (0, 1, 5, 29.99):
                self.tsm.serviceTimers(t)
            self.uart.write.assert_not_called()
            self.tsm.serviceTimers(30)
            self.uart.write.assert_called_once_with(b'off,68,72\n')
            self.mode('heat')
            self.tsm.handleButtonEvent('increase')
            self.tsm.serviceTimers(59.99)
            self.assertEqual(self.uart.write.call_count, 1)
            self.tsm.serviceTimers(60)
            self.assertEqual(self.uart.write.call_args.args[0], b'heat,68,73\n')
            self.assertEqual(self.tsm.next_serial, 90)

    def test_button_activity_does_not_postpone_serial_or_sampling(self):
        for i in range(20):
            self.tsm.handleButtonEvent('cycle')
            self.tsm.serviceTimers(i)
        self.assertEqual(self.tsm.next_serial, 30)
        self.assertEqual(self.tsm.next_sample, 20)

    def test_late_service_skips_missed_records_without_timing_drift(self):
        with patch('builtins.print'):
            self.tsm.serviceTimers(95.2)
        self.assertEqual(self.uart.write.call_count, 1)
        self.assertEqual(self.tsm.next_serial, 120)
        self.assertEqual(self.tsm.next_sample, 96)

    def test_invalid_sensor_and_partial_uart_write_raise_errors(self):
        self.sensor.temperature = float('nan')
        with self.assertRaises(ValueError):
            self.tsm.serviceTimers(1)
        self.sensor.temperature = 20
        self.uart.write.side_effect = lambda data: len(data) - 1
        with self.assertRaises(OSError):
            self.tsm.serviceTimers(30)

    def test_main_serial_configuration_and_cleanup_on_interrupt(self):
        import sys
        red, blue, i2c, uart, screen = [Mock() for _ in range(5)]
        for device in (red, blue, uart):
            device.__enter__ = Mock(return_value=device)
            device.__exit__ = Mock(return_value=False)
        buttons = []
        def make_button(*args, **kwargs):
            button = Mock()
            button.__enter__ = Mock(return_value=button)
            button.__exit__ = Mock(return_value=False)
            buttons.append(button)
            return button
        uart_type = Mock(return_value=uart)
        fake_serial = SimpleNamespace(Serial=uart_type, EIGHTBITS=8, PARITY_NONE='N', STOPBITS_ONE=1)
        modules = {'board': SimpleNamespace(I2C=lambda: i2c),
                   'adafruit_ahtx0': SimpleNamespace(AHTx0=lambda bus: self.sensor),
                   'serial': fake_serial,
                   'gpiozero': SimpleNamespace(PWMLED=Mock(side_effect=[red, blue]), Button=make_button)}
        with patch.dict(sys.modules, modules), patch.object(m, 'ManagedDisplay', return_value=screen), patch.object(m.TemperatureMachine, 'run', side_effect=KeyboardInterrupt), patch.object(sys, 'argv', ['Thermostat.py']), patch('builtins.print'):
            with self.assertRaises(KeyboardInterrupt):
                m.main()
        uart_type.assert_called_once_with(port='/dev/ttyS0', baudrate=115200, bytesize=8, parity='N', stopbits=1, timeout=1, write_timeout=1, xonxoff=False, rtscts=False, dsrdtr=False)
        for device in (red, blue, uart):
            device.__exit__.assert_called_once()
        red.off.assert_called()
        blue.off.assert_called()
        screen.cleanupDisplay.assert_called_once()
        i2c.deinit.assert_called_once()
        self.assertTrue(all(button.when_pressed is None for button in buttons))

if __name__ == '__main__':
    main(verbosity=2)
