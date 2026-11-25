import re
from io import BytesIO
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests
import time
from pathlib import Path

from config import (
    COUNTRIES,
    EEA_METADATA_URL,
    EEA_PARQUET_API_URL,
    POLLUTANT,
    PROCESSED_DIR,
    STATIONS,
    RAW_EEA_DIR
)


def normalize_id(value: str) -> str:
    return re.sub(r"[-_.]", "", str(value)).upper()

def download_metadata(
    url: str = EEA_METADATA_URL,
    cache_path: Path | None = None,
    retries: int = 4,
    backoff_sec: float = 5.0,
) -> pd.DataFrame:
    # 1. Jeśli mamy już zapisany lokalny plik metadanych, użyjmy go (można go usunąć, by wymusić świeże pobranie)
    if cache_path is not None and cache_path.exists():
        print(f"Wczytuję metadane z lokalnego cache: {cache_path}")
        return pd.read_parquet(cache_path)

    # 2. Ponawianie zapytania przy chwilowych błędach serwera (502, 503, 504, 429)
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=180)
            if resp.status_code in {429, 502, 503, 504}:
                raise requests.HTTPError(
                    f"{resp.status_code} Server Error for url: {url}",
                    response=resp,
                )
            resp.raise_for_status()

            with ZipFile(BytesIO(resp.content)) as zf:
                csv_name = next(
                    name for name in zf.namelist() if name.lower().endswith(".csv")
                )
                with zf.open(csv_name) as f:
                    df = pd.read_csv(f, low_memory=False)

            if cache_path is not None:
                df.to_parquet(cache_path, index=False)
                print(f"Zapisano cache metadanych do: {cache_path}")

            return df

        except (requests.RequestException, OSError) as exc:
            last_exc = exc
            if attempt < retries:
                wait_time = backoff_sec * (2 ** (attempt - 1))
                print(
                    f"Próba {attempt}/{retries} nie powiodła się ({exc}). "
                    f"Ponawiam za {wait_time:.1f} s..."
                )
                time.sleep(wait_time)
            else:
                break

    raise RuntimeError(
        f"Nie udało się pobrać metadanych EEA po {retries} próbach."
    ) from last_exc


def fetch_hourly_parquet_urls(country_codes: list[str], pollutant: str = POLLUTANT) -> list[str]:
    payload = {
        "countries": country_codes,
        "cities": [],
        "pollutants": [pollutant],
        "dataset": 2,  # E1a: verified data
        "aggregationType": "hour",
        "source": "API",
        "compress": True,
    }
    resp = requests.post(EEA_PARQUET_API_URL, json=payload, timeout=180)
    resp.raise_for_status()
    return sorted(set(re.findall(r"https?://[^\s\])]+", resp.text)))


def download_and_clean_parquet(url: str, station_info: dict) -> pd.DataFrame:
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    df = pd.read_parquet(BytesIO(resp.content))

    df["Start"] = pd.to_datetime(df["Start"])
    df["End"] = pd.to_datetime(df["End"])

    # Zostawiamy interwały 1-godzinne
    df = df[(df["End"] - df["Start"]) == pd.Timedelta(hours=1)].copy()

    # Konwersja wartości na float i maskowanie niepoprawnych pomiarów (Validity != 1)
    df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
    df.loc[df["Validity"] != 1, "Value"] = np.nan

    df["country"] = station_info["country"]
    df["station_eoi"] = station_info["eoi"]
    df["station_name"] = station_info["name"]

    df = (
        df.sort_values("Start")
        .drop_duplicates(subset=["Start"], keep="last")
        .reset_index(drop=True)
    )
    return df


def main() -> None:
    print("1. Pobieranie metadanych EEA...")
    meta = download_metadata(cache_path=RAW_EEA_DIR / "eea_metadata_cache.parquet")    
    target_countries = set(COUNTRIES.values()) | {"Czech Republic"}
    meta_pm25 = meta[
        meta["Country"].astype(str).str.strip().isin(target_countries)
        & (meta["Air Pollutant"].astype(str).str.strip() == POLLUTANT)
    ].copy()

    selected_eois = [s["eoi"] for s in STATIONS]
    station_by_eoi = {s["eoi"]: s for s in STATIONS}

    # Zapisujemy też tabelę wybranych stacji wraz z ich współrzędnymi z metadanych
    selected_meta = (
        meta_pm25[meta_pm25["Air Quality Station EoI Code"].isin(selected_eois)]
        .sort_values("Year", ascending=False)
        .drop_duplicates(subset=["Air Quality Station EoI Code"])
        .copy()
    )
    selected_meta.to_parquet(PROCESSED_DIR / "selected_stations_meta.parquet", index=False)

    # Mapowanie Sampling Point Id -> EoI code
    sp_to_eoi = {}
    for _, row in selected_meta.iterrows():
        eoi = str(row["Air Quality Station EoI Code"]).strip()
        sp = str(row["Sampling Point Id"]).strip()
        if sp and sp != "nan":
            sp_to_eoi[normalize_id(sp)] = eoi

    print("2. Pobieranie listy plików Parquet z API EEA...")
    all_urls = fetch_hourly_parquet_urls(list(COUNTRIES.keys()), POLLUTANT)

    matched_items = []
    for url in all_urls:
        fname_norm = normalize_id(url.split("/")[-1])
        matched_eoi = None
        for sp_norm, eoi in sp_to_eoi.items():
            if sp_norm in fname_norm:
                matched_eoi = eoi
                break
        if matched_eoi is None:
            for eoi in selected_eois:
                if normalize_id(eoi) in fname_norm:
                    matched_eoi = eoi
                    break
        if matched_eoi is not None:
            matched_items.append((matched_eoi, url))

    print(f"Dopasowano {len(matched_items)} plików Parquet dla stacji: {[x[0] for x in matched_items]}")

    cleaned_dfs = []
    for eoi, url in matched_items:
        st_info = station_by_eoi[eoi]
        print(f"Pobieranie i czyszczenie [{eoi} - {st_info['name']}]: {url.split('/')[-1]} ...")
        df_clean = download_and_clean_parquet(url, st_info)

        expected = pd.date_range(df_clean["Start"].min(), df_clean["Start"].max(), freq="h")
        valid_hrs = int(df_clean["Value"].notna().sum())
        cov = 100.0 * valid_hrs / len(expected) if len(expected) else 0.0
        print(
            f"  Zakres: {df_clean['Start'].min()} -> {df_clean['Start'].max()} | "
            f"Ważne godziny: {valid_hrs}/{len(expected)} ({cov:.2f}%)"
        )
        cleaned_dfs.append(df_clean)

    if cleaned_dfs:
        final_df = pd.concat(cleaned_dfs, ignore_index=True)
        out_file = PROCESSED_DIR / "eea_pm25_clean.parquet"
        final_df.to_parquet(out_file, index=False)
        print(f"\nZapisano gotowy plik dla zespołu: {out_file} ({len(final_df)} wierszy)")


if __name__ == "__main__":
    main()