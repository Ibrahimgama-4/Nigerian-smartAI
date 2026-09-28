from enum import Enum

class DataType(str, Enum):
    REAL_TIME = "REAL_TIME"
    NEAR_REAL_TIME = "NEAR_REAL_TIME"
    HISTORICAL = "HISTORICAL"
    MODELLED = "MODELLED"
    SATELLITE = "SATELLITE"
    USER_PROVIDED = "USER_PROVIDED"
    DEMO = "DEMO"

WEATHER_SOURCE = {
    "name": "Open-Meteo",
    "url": "https://open-meteo.com",
    "license": "CC BY 4.0 (data). Free API: non-commercial use only.",
    "data_type": DataType.MODELLED.value,
    "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
}
RESOLUTION_NOTE = (
    "Modelled weather for a grid cell of roughly 11 km. It is not a measurement at your farm "
    "and is not a ground-station observation. Ward-level weather is not available."
)
