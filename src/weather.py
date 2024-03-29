from __future__ import annotations

import datetime
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING, Any
from xmlrpc.client import Boolean

from dcs.cloud_presets import Clouds as PydcsClouds
from dcs.weather import CloudPreset, Weather as PydcsWeather, Wind

from utils import Distance, Heading, Speed, meters, interpolate, Pressure, inches_hg

from seasonalconditions import determine_season

if TYPE_CHECKING:
    # from game.theater import ConflictTheater
    from seasonalconditions import SeasonalConditions


class TimeOfDay(Enum):
    Dawn = "dawn"
    Day = "day"
    Dusk = "dusk"
    Night = "night"


@dataclass(frozen=True)
class AtmosphericConditions:
    #: Pressure at sea level.
    qnh: Pressure

    #: Temperature at sea level in Celcius.
    temperature_celsius: float


@dataclass(frozen=True)
class WindConditions:
    at_0m: Wind
    at_2000m: Wind
    at_8000m: Wind


@dataclass(frozen=True)
class Clouds:
    base: int
    density: int
    thickness: int
    precipitation: PydcsWeather.Preceptions
    preset: Optional[CloudPreset] = field(default=None)

    @classmethod
    def random_preset(cls, rain: bool) -> Clouds:
        clouds = (p.value for p in PydcsClouds)
        if rain:
            presets = [p for p in clouds if "Rain" in p.name]
        else:
            presets = [p for p in clouds if "Rain" not in p.name]
        preset = random.choice(presets)
        return Clouds(
            base=random.randint(preset.min_base, preset.max_base),
            density=0,
            thickness=0,
            precipitation=PydcsWeather.Preceptions.None_,
            preset=preset,
        )


@dataclass(frozen=True)
class Fog:
    visibility: Distance
    thickness: int


class Weather:
    def __init__(
        self,
        seasonal_conditions: SeasonalConditions,
        day: datetime.date,
        time_of_day: TimeOfDay,
        min_wind_dir: Heading,
        max_wind_dir: Heading,
        min_wind_speed: Speed,
    ) -> None:
        self.atmospheric = self.generate_atmospheric(
            seasonal_conditions, day, time_of_day
        )
        self.clouds = self.generate_clouds()
        self.fog = self.generate_fog()
        self.wind = self.generate_wind(min_wind_dir, max_wind_dir, min_wind_speed)

    def generate_atmospheric(
        self,
        seasonal_conditions: SeasonalConditions,
        day: datetime.date,
        time_of_day: TimeOfDay,
    ) -> AtmosphericConditions:
        pressure = self.interpolate_summer_winter(
            seasonal_conditions.summer_avg_pressure,
            seasonal_conditions.winter_avg_pressure,
            day,
        )
        temperature = self.interpolate_summer_winter(
            seasonal_conditions.summer_avg_temperature,
            seasonal_conditions.winter_avg_temperature,
            day,
        )

        if time_of_day == TimeOfDay.Day:
            temperature += seasonal_conditions.temperature_day_night_difference / 2
        if time_of_day == TimeOfDay.Night:
            temperature -= seasonal_conditions.temperature_day_night_difference / 2
        pressure += self.pressure_adjustment
        temperature += self.temperature_adjustment
        logging.debug(
            "Weather: Before random: temp {} press {}".format(temperature, pressure)
        )
        conditions = AtmosphericConditions(
            qnh=self.random_pressure(pressure),
            temperature_celsius=self.random_temperature(temperature),
        )
        logging.debug(
            "Weather: After random: temp {} press {}".format(
                conditions.temperature_celsius, conditions.qnh.pressure_in_inches_hg
            )
        )
        return conditions

    @property
    def pressure_adjustment(self) -> float:
        raise NotImplementedError

    @property
    def temperature_adjustment(self) -> float:
        raise NotImplementedError

    def generate_clouds(self) -> Optional[Clouds]:
        raise NotImplementedError

    def generate_fog(self) -> Optional[Fog]:
        if random.randrange(5) != 0:
            return None
        return Fog(
            visibility=meters(random.randint(2500, 5000)),
            thickness=random.randint(100, 500),
        )

    def generate_wind(
        self, min_wind_dir: Heading, max_wind_dir: Heading, min_wind_speed: Speed
    ) -> WindConditions:
        raise NotImplementedError

    @staticmethod
    def random_wind(
        minimum_speed: Speed,
        maximum_speed: Speed,
        min_direction: Heading,
        max_direction: Heading,
    ) -> WindConditions:
        wind_direction = Heading.random(min_direction.degrees, max_direction.degrees)
        wind_direction_2000m = wind_direction + Heading.random(-90, 90)
        wind_direction_8000m = wind_direction + Heading.random(-90, 90)
        print("Wind direction is", wind_direction)
        at_0m_factor = 1
        at_2000m_factor = random.uniform(1.5, 2.5)
        at_8000m_factor = random.uniform(2.0, 4.0)
        base_wind = random.uniform(
            minimum_speed.meters_per_second, maximum_speed.meters_per_second
        )

        return WindConditions(
            # Always some wind to make the smoke move a bit.
            at_0m=Wind(wind_direction.degrees, max(1, base_wind * at_0m_factor)),
            at_2000m=Wind(wind_direction_2000m.degrees, base_wind * at_2000m_factor),
            at_8000m=Wind(wind_direction_8000m.degrees, base_wind * at_8000m_factor),
        )

    @staticmethod
    def random_cloud_base() -> int:
        return random.randint(2000, 3000)

    @staticmethod
    def random_cloud_thickness() -> int:
        return random.randint(100, 400)

    @staticmethod
    def random_pressure(average_pressure: float) -> Pressure:
        # "Safe" constants based roughly on ME and viper altimeter.
        # Units are inches of mercury.
        SAFE_MIN = 28.4
        SAFE_MAX = 30.9
        # Use normalvariate to get normal distribution, more realistic than uniform
        pressure = random.normalvariate(average_pressure, 0.1)
        return inches_hg(max(SAFE_MIN, min(SAFE_MAX, pressure)))

    @staticmethod
    def random_temperature(average_temperature: float) -> float:
        # "Safe" constants based roughly on ME.
        # Temperatures are in Celcius.
        SAFE_MIN = -12
        SAFE_MAX = 49
        # Use normalvariate to get normal distribution, more realistic than uniform
        temperature = random.normalvariate(average_temperature, 2)
        temperature = round(temperature)
        return max(SAFE_MIN, min(SAFE_MAX, temperature))

    @staticmethod
    def interpolate_summer_winter(
        summer_value: float, winter_value: float, day: datetime.date
    ) -> float:
        day_of_year = day.timetuple().tm_yday
        day_of_year_peak_summer = 183
        distance_from_peak_summer = abs(-day_of_year_peak_summer + day_of_year)
        winter_factor = distance_from_peak_summer / day_of_year_peak_summer
        return interpolate(summer_value, winter_value, winter_factor, clamp=True)


class ClearSkies(Weather):
    @property
    def pressure_adjustment(self) -> float:
        return 0.4

    @property
    def temperature_adjustment(self) -> float:
        return 3.0

    def generate_clouds(self) -> Optional[Clouds]:
        return None

    def generate_fog(self) -> Optional[Fog]:
        return None

    def generate_wind(
        self, min_wind_dir: Heading, max_wind_dir: Heading, min_wind_speed: Speed
    ) -> WindConditions:
        return self.random_wind(
            max(Speed.from_meters_per_second(2), min_wind_speed),
            max(Speed.from_meters_per_second(8), min_wind_speed),
            min_wind_dir,
            max_wind_dir,
        )


class Cloudy(Weather):
    @property
    def pressure_adjustment(self) -> float:
        return 0.0

    @property
    def temperature_adjustment(self) -> float:
        return 0.0

    def generate_clouds(self) -> Optional[Clouds]:
        return Clouds.random_preset(rain=False)

    def generate_fog(self) -> Optional[Fog]:
        # DCS 2.7 says to not use fog with the cloud presets.
        return None

    def generate_wind(
        self, min_wind_dir: Heading, max_wind_dir: Heading, min_wind_speed: Speed
    ) -> WindConditions:
        return self.random_wind(
            max(Speed.from_meters_per_second(2), min_wind_speed),
            max(Speed.from_meters_per_second(8), min_wind_speed),
            min_wind_dir,
            max_wind_dir,
        )


class Raining(Weather):
    @property
    def pressure_adjustment(self) -> float:
        return -0.22

    @property
    def temperature_adjustment(self) -> float:
        return -3.0

    def generate_clouds(self) -> Optional[Clouds]:
        return Clouds.random_preset(rain=True)

    def generate_fog(self) -> Optional[Fog]:
        # DCS 2.7 says to not use fog with the cloud presets.
        return None

    def generate_wind(
        self, min_wind_dir: Heading, max_wind_dir: Heading, min_wind_speed: Speed
    ) -> WindConditions:
        return self.random_wind(
            max(Speed.from_meters_per_second(2), min_wind_speed),
            max(Speed.from_meters_per_second(12), min_wind_speed),
            min_wind_dir,
            max_wind_dir,
        )


class Thunderstorm(Weather):
    @property
    def pressure_adjustment(self) -> float:
        return 0.1

    @property
    def temperature_adjustment(self) -> float:
        return -3.0

    def generate_clouds(self) -> Optional[Clouds]:
        return Clouds(
            base=self.random_cloud_base(),
            density=random.randint(9, 10),
            thickness=self.random_cloud_thickness(),
            precipitation=PydcsWeather.Preceptions.Thunderstorm,
        )

    def generate_wind(
        self, min_wind_dir: Heading, max_wind_dir: Heading, min_wind_speed: Speed
    ) -> WindConditions:
        return self.random_wind(
            max(Speed.from_meters_per_second(4), min_wind_speed),
            max(Speed.from_meters_per_second(15), min_wind_speed),
            min_wind_dir,
            max_wind_dir,
        )


@dataclass
class Conditions:
    time_of_day: TimeOfDay
    start_time: datetime.datetime
    weather: Weather

    @classmethod
    def generate(
        cls,
        seasonal_conditions: SeasonalConditions,
        day: datetime.date,
        time_of_day: TimeOfDay,
        clearweather: Boolean,
        min_wind_dir: Heading,
        max_wind_dir: Heading,
        min_wind_speed: Speed,
    ) -> Conditions:
        _start_time = cls.generate_start_time(day, time_of_day)
        return cls(
            time_of_day=time_of_day,
            start_time=_start_time,
            weather=cls.generate_weather(
                seasonal_conditions,
                day,
                time_of_day,
                clearweather,
                min_wind_dir,
                max_wind_dir,
                min_wind_speed,
            ),
        )

    @classmethod
    def generate_start_time(
        cls,
        day: datetime.date,
        time_of_day: TimeOfDay,
    ) -> datetime.datetime:
        time_range = {
            TimeOfDay.Dawn: (7, 9),
            TimeOfDay.Day: (11, 13),
            TimeOfDay.Dusk: (17, 19),
            TimeOfDay.Night: (21, 23),
        }[time_of_day]
        time = datetime.time(hour=random.randint(*time_range))
        return datetime.datetime.combine(day, time)

    @classmethod
    def generate_weather(
        cls,
        seasonal_conditions: SeasonalConditions,
        day: datetime.date,
        time_of_day: TimeOfDay,
        clearweather: Boolean,
        min_wind_dir: Heading,
        max_wind_dir: Heading,
        min_wind_speed: Speed,
    ) -> Weather:
        season = determine_season(day)
        logging.debug("Weather: Season {}".format(season))
        logging.debug("Forced clear weather? {}".format(clearweather))
        weather_chances = seasonal_conditions.weather_type_chances[season]
        if not clearweather:
            chances = {
                Thunderstorm: weather_chances.thunderstorm,
                Raining: weather_chances.raining,
                Cloudy: weather_chances.cloudy,
                ClearSkies: weather_chances.clear_skies,
            }
        else:
            chances = {
                Thunderstorm: 0,
                Raining: 0,
                Cloudy: 0,
                ClearSkies: 100,
            }

        logging.debug("Weather: Chances {}".format(chances))
        weather_type = random.choices(
            list(chances.keys()), weights=list(chances.values())
        )[0]
        logging.debug("Weather: Type {}".format(weather_type))
        return weather_type(
            seasonal_conditions,
            day,
            time_of_day,
            min_wind_dir,
            max_wind_dir,
            min_wind_speed,
        )
