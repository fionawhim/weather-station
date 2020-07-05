import sys

import time
import os.path
import urllib.request, json, socket
import logging
import traceback
import math
from datetime import datetime, timedelta
from dateutil.tz import *

import board
import busio
import digitalio

from adafruit_bus_device.i2c_device import I2CDevice

from dotenv import load_dotenv

load_dotenv(verbose=True)

# Keep us from hanging when loading JSON
socket.setdefaulttimeout(120)
logging.basicConfig(level=logging.INFO)

SLIDING_TIME_SCALE = True

EASTERN = tzfile("/usr/share/zoneinfo/EST5EDT")

# Set volume to high
os.system("amixer cset numid=3 100%")

# I2C address for the Trinket we’re using as our Neopixel / servo contorller
TRINKET_ADDRESS = 0x08

SET_PIXEL_CMD = 0x2B
SHOW_PIXELS_CMD = 0x2C
SET_SERVO_CMD = 0x3C

TIDE_SERVO_NUM = 0
TEMP_SERVO_NUM = 1

NUM_PIXELS = 48

API_URL = os.getenv("API_URL")

STEPPER_DELAY_IN_SECONDS = 2 / 1000.0
STEPS_PER_ICON = 384
ICONS = [
    "clear-day",
    "cloudy",
    "partly-cloudy-day",
    "rain",
    "clear-night",
    "wind",
    "partly-cloudy-night",
    "snow",
]

ICON_COLORS = {
    "clear-day": [40, 35, 0],
    "cloudy": [10, 10, 5],
    "partly-cloudy-day": [15, 15, 4],
    "rain": [0, 0, 200],
    "clear-night": [5, 5, 10],
    "wind": [5, 30, 5],
    "partly-cloudy-night": [5, 5, 8],
    "snow": [75, 0, 60],
}

HOUR_LIGHTS_START = 23
HOUR_LIGHTS_END = 0
HOUR_LIGHTS_OFFSET = -1

TEMP_LIGHTS_START = 24
TEMP_LIGHTS_END = 36
TEMP_LIGHTS_OFFSET = 1

TEMP_ANGLE_START = -10
TEMP_ANGLE_END = 170

LOW_TEMP = 30.0
HIGH_TEMP = 90.0

RAIN_LIGHTS_START = 44
RAIN_LIGHTS_END = 40
RAIN_LIGHTS_OFFSET = -1

TIDE_ANGLE_START = 0.0
TIDE_ANGLE_END = 120.0
TIDE_MINUTES_START = 0
TIDE_MINUTES_END = 720

TEMP_LIGHTS_COLORS = []
light_mid = (TEMP_LIGHTS_END - TEMP_LIGHTS_START) // 2

for i in range((TEMP_LIGHTS_END - TEMP_LIGHTS_START) // 2):
    ratio = int(30 * float(i) / light_mid)
    TEMP_LIGHTS_COLORS.append([0, ratio, 30 - ratio])

TEMP_LIGHTS_COLORS.append([0, 30, 0])

for i in range((TEMP_LIGHTS_END - TEMP_LIGHTS_START) // 2):
    ratio = int(30 * float(i) / light_mid)
    TEMP_LIGHTS_COLORS.append([ratio, 30 - ratio, 0])

coil_A1 = digitalio.DigitalInOut(board.D4)
coil_A2 = digitalio.DigitalInOut(board.D17)
coil_B1 = digitalio.DigitalInOut(board.D23)
coil_B2 = digitalio.DigitalInOut(board.D24)

coil_A1.direction = digitalio.Direction.OUTPUT
coil_A2.direction = digitalio.Direction.OUTPUT
coil_B1.direction = digitalio.Direction.OUTPUT
coil_B2.direction = digitalio.Direction.OUTPUT

current_position = 0
if os.path.isfile("state"):
    state_file = open("state", "r")
    current_state = state_file.readline()
    current_position = int(current_state)

last_bell = datetime.now(EASTERN)

i2c = busio.I2C(board.SCL, board.SDA)
trinket_device = I2CDevice(i2c, TRINKET_ADDRESS)


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def convert_icon(icon):
    if icon == "sleet":
        return "rain"
    if icon == "fog":
        return "cloudy"
    return icon


def forward(delay, steps):
    for i in range(0, steps):
        setStep(1, 1, 0, 0)
        time.sleep(delay)
        setStep(0, 1, 1, 0)
        time.sleep(delay)
        setStep(0, 0, 1, 1)
        time.sleep(delay)
        setStep(1, 0, 0, 1)
        time.sleep(delay)
    setStep(0, 0, 0, 0)


def backwards(delay, steps):
    for i in range(0, steps):
        setStep(1, 0, 0, 1)
        time.sleep(delay)
        setStep(0, 0, 1, 1)
        time.sleep(delay)
        setStep(0, 1, 1, 0)
        time.sleep(delay)
        setStep(1, 1, 0, 0)
        time.sleep(delay)
    setStep(0, 0, 0, 0)


def setStep(w1, w2, w3, w4):
    coil_A1.value = w1
    coil_A2.value = w2
    coil_B1.value = w3
    coil_B2.value = w4


def move_to(icon):
    global current_position

    icon_num = ICONS.index(icon)
    destination_position = icon_num * STEPS_PER_ICON
    delta = current_position - destination_position

    if delta < 0:
        delta = delta + len(ICONS) * STEPS_PER_ICON

    backwards(STEPPER_DELAY_IN_SECONDS, delta)

    current_position = destination_position
    state_file = open("state", "w")
    state_file.write(str(current_position))
    state_file.close()


def cycle_icons():
    backwards(STEPPER_DELAY_IN_SECONDS, len(ICONS) * STEPS_PER_ICON)


def trinket_call(cmd, args=[]):
    try:
        with trinket_device:
            buf = bytearray([cmd])
            buf += bytes(args)

            trinket_device.write(buf)
    except IOError as e:
        eprint("I/O error({0}): {1}".format(e.errno, e.strerror))


def pixel_for_temp(t, low):
    light_range = TEMP_LIGHTS_END - TEMP_LIGHTS_START + 1
    temp_range = HIGH_TEMP - LOW_TEMP

    before_t = t

    if low:
        t = math.floor(t / 5) * 5
    else:
        t = math.floor(t / 5) * 5

    p = int((t - LOW_TEMP) * (light_range / temp_range) + TEMP_LIGHTS_START)

    return p


def angle_for_temp(t):
    angle_low = LOW_TEMP
    angle_high = HIGH_TEMP + 5

    angle_range = TEMP_ANGLE_END - TEMP_ANGLE_START
    temp_range = angle_high - angle_low

    return TEMP_ANGLE_END - int(
        math.floor((t - angle_low) * (angle_range / temp_range) + TEMP_ANGLE_START)
    )


def show_temperature(data):
    low = max(data["lowTemp"], LOW_TEMP)
    high = min(data["highTemp"], HIGH_TEMP)
    temp = min(max(data["currentTemp"], LOW_TEMP), HIGH_TEMP)

    logging.info("Setting temps: %d–%d %d", low, high, temp)

    low_light = pixel_for_temp(low, True)
    high_light = pixel_for_temp(high, False)

    for i in range(TEMP_LIGHTS_START, TEMP_LIGHTS_END + 1):
        if (i < low_light) or (i > high_light):
            c = [0, 0, 0]
        else:
            c = TEMP_LIGHTS_COLORS[i - TEMP_LIGHTS_START]

        trinket_call(SET_PIXEL_CMD, [i, c[0], c[1], c[2]])

    trinket_call(SHOW_PIXELS_CMD)

    # small sleep to avoid confusing the trinket
    time.sleep(0.1)
    angle = angle_for_temp(data["currentTemp"])
    angle = min(angle, 180, max(angle, 0))
    trinket_call(SET_SERVO_CMD, [TEMP_SERVO_NUM, angle])


def show_rain(d):
    rain_pixels = [[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]]

    cur_time = datetime.now(EASTERN)
    first_rain = cur_time + timedelta(minutes=120)

    for minute in d["minutes"]:
        if minute["precipProbability"] >= 0.5:
            first_rain = (
                datetime.utcfromtimestamp(minute["time"])
                .replace(tzinfo=tzutc())
                .astimezone(EASTERN)
            )
            break

    rain_color = [0, 25, 100]

    if first_rain <= cur_time + timedelta(minutes=60):
        rain_pixels[4] = rain_color
    if first_rain <= cur_time + timedelta(minutes=45):
        rain_pixels[3] = rain_color
    if first_rain <= cur_time + timedelta(minutes=30):
        rain_pixels[2] = rain_color
    if first_rain <= cur_time + timedelta(minutes=15):
        rain_pixels[1] = rain_color
    if first_rain <= cur_time + timedelta(minutes=5):
        rain_pixels[0] = rain_color

    for i in range(5):
        color = rain_pixels[i]
        trinket_call(SET_PIXEL_CMD, [i + RAIN_LIGHTS_END, color[0], color[1], color[2]])

    trinket_call(SHOW_PIXELS_CMD)


def show_conditions(d):
    conditions_icon = d["currentIcon"]
    logging.info("Changing to %s", conditions_icon)
    move_to(convert_icon(conditions_icon))


def show_tides(d):
    global last_bell

    cur_time = datetime.now(EASTERN)

    low_tides = map(
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=EASTERN),
        d["lowTides"],
    )

    upcoming_tides = (t for t in low_tides if t > cur_time)
    tide = next(upcoming_tides)

    if SLIDING_TIME_SCALE:
        minutes = (tide - cur_time).total_seconds() // 60
        logging.info("Next tide at %s, %d minutes away", tide, minutes)
    else:
        minutes = tide.hour * 60 + tide.minute

    minutes = min(minutes, TIDE_MINUTES_END)

    angle = int(
        (minutes - TIDE_MINUTES_START)
        * (
            (TIDE_ANGLE_END - TIDE_ANGLE_START)
            / (TIDE_MINUTES_END - TIDE_MINUTES_START)
            + TIDE_ANGLE_START
        )
    )

    trinket_call(SET_SERVO_CMD, [TIDE_SERVO_NUM, angle])

    # TODO(finh): Move ringing the tide outside of the "show_tides" so that
    # it happens in the outer loop, not when the response comes from the server
    for tide in d["lowTides"] + d["highTides"]:
        tide_date = datetime.strptime(tide, "%Y-%m-%d %H:%M").replace(tzinfo=EASTERN)
        if cur_time >= tide_date and last_bell < tide_date:
            os.system("aplay ./assets/bell.wav")
            os.system("aplay ./assets/bell.wav")
            last_bell = cur_time


def show_forecast(d):
    for i in range(HOUR_LIGHTS_START + 1):
        trinket_call(SET_PIXEL_CMD, [i, 0, 0, 0])

    cur_time = datetime.now(EASTERN)
    start = datetime(cur_time.year, cur_time.month, cur_time.day, tzinfo=EASTERN)
    curr_hour = datetime.now(EASTERN).hour

    if SLIDING_TIME_SCALE:
        found_now = False
        pixel_pos = 23
        count = 0

        for forecast in d["hours"]:
            hour_date = (
                datetime.utcfromtimestamp(forecast["time"])
                .replace(tzinfo=tzutc())
                .astimezone(EASTERN)
            )
            hour = hour_date.hour

            if hour < curr_hour and not found_now:
                continue

            found_now = True

            color = ICON_COLORS[convert_icon(forecast["icon"])]
            if hour != 0:
                trinket_call(SET_PIXEL_CMD, [pixel_pos, color[0], color[1], color[2]])
            pixel_pos = pixel_pos - 1
            count = count + 1

            if count == 24:
                break
    else:
        pixel_pos = 23 - curr_hour
        count = 0

        found_now = False

        for forecast in d["hours"]:
            hour_date = (
                datetime.utcfromtimestamp(forecast["time"])
                .replace(tzinfo=tzutc())
                .astimezone(EASTERN)
            )
            hour = hour_date.hour

            if hour < curr_hour and not found_now:
                continue

            found_now = True

            color = ICON_COLORS[convert_icon(forecast["icon"])]
            trinket_call(SET_PIXEL_CMD, [pixel_pos, color[0], color[1], color[2]])
            pixel_pos = pixel_pos - 1
            if pixel_pos < 0:
                pixel_pos = pixel_pos + 24
            count = count + 1

            if count == 21:
                break

    trinket_call(SHOW_PIXELS_CMD)


last_sync = datetime.now(EASTERN) - timedelta(minutes=60)
last_hour_signal = datetime.now(EASTERN)

# RESET LIGHTS
for i in range(NUM_PIXELS):
    trinket_call(SET_PIXEL_CMD, [i, 0, 0, 0])
trinket_call(SHOW_PIXELS_CMD)

started = False

while True:
    try:
        cur_time = datetime.now(EASTERN)

        # Load new data every 3 minutes
        if cur_time > last_sync + timedelta(minutes=3):
            # Keeps us from requesting in a tight loop if we’re getting errors
            last_sync = cur_time

            logging.info("Loading remote data...")
            response_bytes = urllib.request.urlopen(API_URL).read()
            d = json.loads(response_bytes.decode("utf-8"))
            logging.info("...success")

            show_temperature(d)
            show_conditions(d)
            show_tides(d)
            show_forecast(d)
            show_rain(d)

        if cur_time.hour > last_hour_signal.hour or not started:
            logging.info("Top of the hour or startup. Cycling icons.")
            cycle_icons()
            last_hour_signal = cur_time
            started = True

    except Exception as e:
        logging.error(traceback.format_exc())

    # We wake up every second to check for whether it’s a new hour
    # or if it’s time to load data.
    time.sleep(1)

