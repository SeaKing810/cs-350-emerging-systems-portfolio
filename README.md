# CS 350 Emerging Systems Architectures and Technologies

**Sean McKenna | Southern New Hampshire University**

This portfolio contains my Raspberry Pi thermostat implementation and architecture recommendation from CS 350. The project combines embedded software, hardware interfaces, state-machine design, and evaluation of platforms for a connected thermostat.

## Selected artifacts

| Artifact | What it demonstrates |
| --- | --- |
| [Thermostat source code](Thermostat.py) | Python interface software integrating an I2C temperature sensor, GPIO buttons, PWM LEDs, a character LCD, and UART communication. |
| [SysTec architecture recommendation](CS350_Final_Project_Report.pdf) · [Word version](CS350_Final_Project_Report.docx) | Comparison of Raspberry Pi, Microchip, and NXP platforms against peripheral, memory, connectivity, and maintenance requirements, with a justified recommendation for a production design. |

Supporting files include the [state-machine diagram and specification](CS350_Final_Project_State_Machine.pdf), its [editable draw.io source](CS350_Final_Project_State_Machine.drawio), [dependencies](requirements.txt), and [software tests](test_thermostat.py).

## Module Eight reflection

### Summarize the project and what problem it was solving.

I developed a thermostat prototype for SysTec that reads room temperature, lets a user select a mode and target temperature, and provides local feedback and serial status updates. The Raspberry Pi 4B reads an AHT20 sensor over I2C, accepts three buttons, displays information on a 16-by-2 LCD, and uses red and blue LEDs to represent heating and cooling. A state machine coordinates off, heat, and cool modes. I also evaluated hardware options for moving the prototype toward a connected product, considering available memory, peripheral support, networking, and the effort required to migrate the software.

### What did you do particularly well?

I did well at combining the individual hardware interfaces into one organized application. Button callbacks place events in a queue, and the main loop applies them, keeping state changes and hardware access coordinated. Separate timer deadlines allow temperature sampling and UART reporting to continue while the user changes settings. I also kept the displayed temperature, control decisions, and transmitted values consistent, and used software checks alongside hands-on testing to verify the behavior.

### Where could you improve?

I could improve the prototype's fault recovery and validation. I would add sensible limits to the set point, retain settings between restarts, and provide clearer recovery behavior for sensor or serial errors. A production version would also need hysteresis, equipment timing protection, and an appropriate actuator interface. I would measure sensor accuracy and physical timing more thoroughly and verify peak memory use with networking enabled before finalizing the production hardware choice.

### What tools and/or resources are you adding to your support network?

I am adding Raspberry Pi documentation, Adafruit's sensor and LCD guides, GPIO Zero, python-statemachine, and pySerial documentation to my regular references. Manufacturer datasheets help me compare architecture choices against specific requirements. I will also keep editable state diagrams and mock hardware tests available so I can understand a design and check its logic before connecting physical components.

### What skills from this project will be particularly transferable to other projects and/or course work?

State-machine design, modular Python programming, event handling, and debugging will transfer directly to other projects. Working with GPIO, I2C, and UART taught me to trace problems across both software and wiring instead of assuming that every failure comes from the code. Comparing hardware platforms also strengthened my ability to turn business requirements into technical criteria and explain the tradeoffs behind a recommendation.

### How did you make this project maintainable, readable, and adaptable?

I used named constants for pins and timing intervals, descriptive methods, and docstrings that explain important behavior. The sensor, LEDs, display, serial connection, and clock are passed into the state-machine class, which makes the logic easier to test with substitute interfaces. Centralized serial formatting, explicit state transitions, and resource cleanup keep responsibilities clear. The dependency list and editable state diagram provide context for future changes, while the software tests help check that those changes preserve expected behavior.

## Running the prototype

Use a Raspberry Pi 4B with Raspberry Pi OS, an AHT20, a 16-by-2 character LCD, three buttons, red and blue LEDs with series resistors, and a compatible 3.3 V UART adapter. Disconnect power before changing wiring.

| Interface | BCM GPIO assignments |
| --- | --- |
| Red / blue LEDs | 18 / 23 |
| Mode / increase / decrease buttons | 24 / 25 / 12; active-low inputs with pull-ups |
| LCD RS / EN / D4 / D5 / D6 / D7 | 17 / 27 / 5 / 6 / 13 / 26 |
| AHT20 SDA / SCL | 2 / 3 |
| UART TX / RX | 14 / 15; connect TX to adapter RX, RX to adapter TX, and common ground |

Enable I2C and UART hardware on the Pi and disable the serial login console. From this repository's directory, use an environment with access to the Pi's GPIO support:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python Thermostat.py --port /dev/ttyS0
```

Use the UART device configured on your Pi; `/dev/serial0` may be the appropriate alias. The program starts off with a 72°F target. The mode button cycles off → heat → cool → off, and the other buttons adjust the target by 1°F per press. A pulsing LED indicates active heating or cooling demand; a solid LED means the selected mode is satisfied. The LCD alternates its second row every five seconds. UART emits newline-terminated `mode,temperature,setpoint` records at 115200 baud, 8N1, on 30-second deadlines. Ctrl+C stops the program and releases its resources.

## Verification and scope

The included suite contains 18 automated tests covering mode transitions, temperature comparisons, button events, PWM behavior, LCD formatting, timer deadlines, UART output, and cleanup. To run it without physical hardware, install the two test dependencies in a Python environment and run:

```bash
python -m pip install gpiozero==2.0.1 python-statemachine==2.3.6
python -m unittest -v test_thermostat.py
```

Course hardware checks confirmed the buttons, LCD, indicator behavior, set-point adjustment, and receipt of serial records. Automated timing checks use a controlled clock; the hardware receiver evidence did not independently measure the reporting interval.

This prototype represents heating and cooling with LEDs. The architecture report's MQTT/TLS connectivity and production-platform migration are proposed extensions. The source retains attribution to the SNHU Module Seven thermostat template from which it was adapted.
