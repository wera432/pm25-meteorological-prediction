import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(os.environ.get("PM25_DATA_DIR", BASE_DIR / "data"))
RAW_EEA_DIR = DATA_DIR / "raw" / "eea"
RAW_ERA5_DIR = DATA_DIR / "raw" / "era5"
PROCESSED_DIR = DATA_DIR / "processed"

for folder in (RAW_EEA_DIR, RAW_ERA5_DIR, PROCESSED_DIR):
    folder.mkdir(parents=True, exist_ok=True)

COUNTRIES = {
    "AT": "Austria",
    "CZ": "Czechia",
    "DE": "Germany",
    "PL": "Poland",
}

STATIONS = [
    {"country": "AT", "eoi": "AT0ILL1", "name": "Illmitz"},
    {"country": "CZ", "eoi": "CZ0JKOS", "name": "Kosetice"},
    {"country": "DE", "eoi": "DEUB005", "name": "Waldhof"},
    {"country": "PL", "eoi": "PL0150A", "name": "Borsukowizna"},
]

POLLUTANT = "PM2.5"
EEA_METADATA_URL = (
    "https://discomap.eea.europa.eu/App/AQViewer/download"
    "?fqn=Airquality_Dissem.b2g.measurements&f=csv"
)
EEA_PARQUET_API_URL = (
    "https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls"
)

ERA5_YEARS = ["2020", "2021", "2022", "2023", "2024"]
BBOX_NWE_S = [55.0, 8.0, 47.0, 25.0]