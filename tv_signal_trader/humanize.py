import random
import time


def pause(min_s=0.4, max_s=0.9):
    time.sleep(random.uniform(min_s, max_s))


def long_pause(min_s=1.0, max_s=2.5):
    time.sleep(random.uniform(min_s, max_s))


def type_humanlike(element, text):
    for char in str(text):
        element.send_keys(char)
        time.sleep(random.uniform(0.08, 0.22))
