# These are the remotes themselves. They are hardware devices that are
# controlled by the pi. They use wtforms to create attributes about them,
# such as which pin they are connected to the pi from


import wtforms
from wtforms import TextField, IntegerField, BooleanField
from wtforms import validators
import time
import re
from backend.camera import take_photo, get_newest_photo  # hard coded webcam
from backend.emailer import send_email

if __debug__:  # if not editing from the raspberry pi
    import gpiozero as gpio
    from gpiozero import OutputDevice
    from gpiozero import GPIODevice
    from gpiozero import MotionSensor as Motion
    from gpiozero import Button
else:
    print("DEBUG MODE IS ON, HARDWARE WILL NOT WORK")

MIN_GPIO = 4
MAX_GPIO = 26

# Has min max attributes so javascript can check if a pin is
# valid or not


class MinMaxIntegerField(IntegerField):
    def __init__(self, min=None, max=None, **kwargs):
        super().__init__(**kwargs)
        self.min = min
        self.max = max

# Make sure all subclasses have these methods


class RemoteInterface():
    def close(self):
        raise NotImplementedError

    # Gets information from database
    def input(self, data):
        raise NotImplementedError

    def output(self, database, query):
        raise NotImplementedError

    class Form(wtforms.Form):
        pass

    @classmethod
    def to_dic(cls, form):
        return {
                "pin": form.pin.data,
                "name": form.name.data,
                "type": cls.__name__
                }

# Abstract remote class. All Remotes should have a primary pin
# number and a name


class RemoteAbstract(RemoteInterface):
    def __init__(self, dic, Type=None):
        self.pin = dic["pin"]
        if __debug__:
            if Type is None:
                Type = GPIODevice
            try:
                self.Type = Type
                self.device = self.Type(self.pin)
            except gpio.GPIOZeroError as e:
                raise ValueError(str(e))
            except Exception as e:
                raise e

    def close(self):
        if __debug__:
            self.device.close()

    def _change_pin(self, new_pin):
        self.close()
        if __debug__:
            try:
                self.device = self.Type(self.pin)
            except gpio.GPIOZeroError as e:
                raise ValueError(str(e))
            except Exception as e:
                raise e

    # Gets information from database
    def input(self, data):
        if self.pin != data["pin"]:
            self.pin = data["pin"]
            self._change_pin(self.pin)

    def output(self, database, query, data=None):
        if data is not None:
            database.update(data, query["pin"] == self.pin)

    class Form(wtforms.Form):
        name = TextField("name", [validators.Required(message="name must not" +
                         " be left blank")])

        blank_gpio_message = "gpio pin must not be left blank"
        wrong_pin_message = "gpio pin must be between " +\
                            str(MIN_GPIO) + " - " + str(MAX_GPIO)

        blank_validator = validators.Required(message=blank_gpio_message)
        num_validator = validators.NumberRange(min=MIN_GPIO,
                                               max=MAX_GPIO,
                                               message=wrong_pin_message)

        pin = MinMaxIntegerField(label="gpio pin", min=MIN_GPIO, max=MAX_GPIO,
                                 validators=[blank_validator, num_validator])


# For devices that only have an on/off state


class SimpleOutput(RemoteAbstract):
    def __init__(self, dic):
        if __debug__:
            super().__init__(dic, OutputDevice)
        else:
            super().__init__(dic)

    def input(self, data):
        super().input(data)
        if __debug__ and "keep_on" in data:
            if data["keep_on"]:
                self.on()
            else:
                self.off()

    def on(self):
        self.device.on()

    def off(self):
        self.device.off()

    class Form(RemoteAbstract.Form):
        keep_on = BooleanField("Initial State")

    @classmethod
    def to_dic(self, form):
        dic = super().to_dic(form)
        dic["keep_on"] = form.keep_on.data
        return dic


# Simple Input Device, this class should be subclassed


class SimpleInput(RemoteAbstract):
    def __init__(self, dic, Type=None):
        if __debug__:
            super().__init__(dic, Type)
        else:
            super().__init__(dic)

        self.data = None

    def is_active(self):
        if __debug__:
            return self.device.is_active
        else:
            return True

    def output(self, database, query, data=None):
        if data is None:
            data = {"data": self.data}

        super().output(database, query, data)

    @classmethod
    def to_dic(cls, form):
        dic = super().to_dic(form)
        dic["data"] = None
        return dic

# A motion sensor


class MotionSensor(SimpleInput):
    def __init__(self, dic):
        if __debug__:
            super().__init__(dic, Motion)
        else:
            super().__init__(dic)

    def output(self, database, query):
        if self.is_active():
            self.data = int(time.time())

        super().output(database, query)


class Switch(SimpleInput):
    def __init__(self, dic):
        if __debug__:
            super().__init__(dic, Button)
        else:
            super().__init(dic)

    def output(self, database, query):
        if self.is_active():
            self.data = "ON"
        else:
            self.data = "OFF"
        super().output(database, query)


# The "pin" is for the switch. There's also a pin required for:
# The buzzer, and motion sensor. Also, there's a camera


class AlarmSystem(RemoteInterface):
    def __init__(self, dic):
        if __debug__:
            try:
                self.pin = dic["pin"]
                self.switch = Switch({"pin": dic["pin"]})
                self.buzzer = SimpleOutput({"pin": dic["pin_buzzer"]})
                self.motion = MotionSensor({"pin": dic["pin_motion"]})
                self.emails = dic["emails"]
            except gpio.GPIOZeroError as e:
                raise ValueError(str(e))
            except Exception as e:
                raise e

        self.keep_on = dic["keep_on"]
        self.photo_toggle = dic["photo_toggle"]

        self.motion_detected = False
        self.door_open = False

        self.EMAIL_MAX_TIME = 3600  # 1 hour
        self.last_email_sent = None

        self.PHOTO_MAX_TIME = 3  # 3 seconds
        self.last_picture_taken = None

    # For when the door is open but the alarm system is deactivated
    def alert_mode(self):
        self.buzzer.on()

        # send email every hour if door is open
        if self.last_email_sent is None or \
            int(time.time()) - self.last_email_sent > \
                self.EMAIL_MAX_TIME:

            self.send_email()

        # take photo every three seconds if door is open
        if self.last_picture_taken is None or \
            int(time.time()) - self.last_picture_taken > \
                self.PHOTO_MAX_TIME:

            self.take_photo()

    def take_photo(self):
        take_photo()
        self.last_picture_taken = int(time.time())

    def send_email(self):
        send_email(self.emails.replace(" ", "").split(","))
        self.last_email_sent = int(time.time())

    def passive_mode(self):
        # This is so that if home mode is enabled, then disabled, then
        # enabled again in a short timeframe, and email will still be sent.
        if self.last_email_sent is not None:
            self.last_email_sent = None

        self.buzzer.off()

    def input(self, data):
        if __debug__:
            self.switch.input({"pin": data["pin"]})
            self.buzzer.input({"pin": data["pin_buzzer"]})
            self.motion.input({"pin": data["pin_motion"]})

            self.emails = data["emails"]

            self.keep_on = data["keep_on"]
            self.door_open = not self.switch.is_active()
            self.motion_detected = self.motion.is_active()

            if data["photo_toggle"] != self.photo_toggle:
                self.take_photo()
                self.photo_toggle = data["photo_toggle"]

            if self.keep_on and self.door_open:  # Door is open when not home
                self.alert_mode()
            else:
                self.passive_mode()

    def output(self, database, query):
        if __debug__:
            dic = {}
            dic["door_open"] = self.door_open

            if self.motion_detected:
                dic["motion"] = time.strftime("%c")
                self.take_photo()

            dic["photo"] = get_newest_photo()
            database.update(dic, query["pin"] == self.pin)

    def close(self):
        if __debug__:
            self.switch.close()
            self.buzzer.close()
            self.motion.close()

    class Form(wtforms.Form):
        name = TextField("name", [validators.Required(message="name must not" +
                         " be left blank")])

        v_b = RemoteAbstract.Form.blank_validator
        v_n = RemoteAbstract.Form.num_validator

        pin = MinMaxIntegerField(label="gpio pin for Switch",
                                 min=MIN_GPIO, max=MAX_GPIO,
                                 validators=[v_b, v_n])

        pin_buzzer = MinMaxIntegerField(label="gpio pin for Buzzer",
                                        min=MIN_GPIO, max=MAX_GPIO,
                                        validators=[v_b, v_n])

        pin_motion = MinMaxIntegerField(label="gpio pin for Motion Sensor",
                                        min=MIN_GPIO, max=MAX_GPIO,
                                        validators=[v_b, v_n])

        emails = TextField(label="email adresses separated by commas",
                           validators=[])

        keep_on = BooleanField("enable away from home?")

        def validate_emails(form, field):
            if len(field.data) == 0:
                return

            regex = "[^@]+@[^@]+\.[^@]+"
            for email in field.data.split(","):
                if re.search(regex, email.replace(" ", "")) is None:
                    raise validators.ValidationError("unable to validate" +
                                                     " email, maybe a" +
                                                     " typo?")

    @classmethod
    def to_dic(cls, form):
        dic = super().to_dic(form)

        dic["pin_buzzer"] = form.pin_buzzer.data
        dic["pin_motion"] = form.pin_motion.data

        dic["keep_on"] = form.keep_on.data

        dic["door_open"] = None
        dic["motion"] = None
        dic["photo_toggle"] = False
        dic["photo"] = get_newest_photo()

        dic["emails"] = form.emails.data.replace(" ", "")

        return dic
