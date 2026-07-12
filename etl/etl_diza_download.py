"""
ETL DIZA — Download dari website → simpan ke data/Diza.xlsx → upload ke Google Sheets.

Jalankan: python etl/etl_diza_download.py
"""

import json
import os
from pathlib import Path

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

SAVE_DIR   = Path(__file__).parent.parent / "data"
SAVE_DIR.mkdir(exist_ok=True)

SHEET_ID   = os.environ['SHEET_ID']
DIZA_GID   = 1732323997
BATCH_SIZE = 500

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


# ── Step 1: Download dari website ────────────────────────────────────────────

def download_diza() -> Path:
    dest = SAVE_DIR / "Diza.xlsx"
    with sync_playwright() as p:
        headless = os.environ.get('CI', '').lower() == 'true'
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(ignore_https_errors=True)
        page    = context.new_page()

        print('Membuka Hazard Tracker...')
        page.goto("https://script.google.com/macros/s/AKfycbyiUoZpxq5Z6b8ZAjxcywsMG8hcI40y60AjK2Qar-PsIJqKjcMQ16eUaPF7b1bDvYxW7w/exec")

        # Masuk ke iframe bertingkat
        frame = (
            page.locator("iframe[title='Hazard Tracker']").content_frame
                .locator("iframe[title='Hazard Tracker']").content_frame
        )

        print('Set filter Status = All...')
        frame.locator("#f-status").select_option("All")

        print('Pilih PIC = PT. ANTAREJA MAHADA MAKMUR...')
        frame.locator("#f-pic-container div").filter(has_text="Semua PIC/Dept").click()
        frame.locator("#f-pic-dropdown label").filter(has_text="Pilih Semua").click()
        frame.locator("#f-pic-dropdown").get_by_text("PT. ANTAREJA MAHADA MAKMUR").click()

        print('Terapkan filter...')
        frame.get_by_text("HAZARD TRACKER Admin Filter").click()

        print('Buka tampilan Detail Temuan...')
        frame.locator("div").filter(has_text="DETAIL TEMUAN").nth(1).click()
        frame.locator("#page-detail > .header > .fa-solid").click()

        print('Download Excel...')
        with page.expect_download() as dl_info:
            frame.get_by_role("button", name=" Excel").click()
        dl_info.value.save_as(dest)

        context.close()
        browser.close()

    print(f'Tersimpan: {dest}')
    return dest


# ── Step 2: Upload ke Google Sheets ──────────────────────────────────────────

def upload_to_sheets(filepath: Path) -> None:
    print('\nAutentikasi Google Sheets...')
    creds = Credentials.from_service_account_info(
        json.loads(os.environ['GOOGLE_CREDS_JSON']), scopes=SCOPES
    )
    ss = gspread.authorize(creds).open_by_key(SHEET_ID)
    ws = ss.get_worksheet_by_id(DIZA_GID)
    print(f'Terhubung ke sheet GID {DIZA_GID}: "{ws.title}"')

    print(f'Membaca {filepath.name}...')
    df = pd.read_excel(filepath, dtype=str).fillna('')
    print(f'  {len(df)} baris, {len(df.columns)} kolom.')

    rows        = [df.columns.tolist()] + df.astype(str).replace('nan', '').values.tolist()
    total       = len(rows)
    n_batches   = -(-total // BATCH_SIZE)

    print('Menghapus data lama...')
    ws.clear()

    print(f'Menulis {total} baris dalam {n_batches} batch...')
    ws.update(rows[:BATCH_SIZE], 'A1', value_input_option='RAW')
    for i, start in enumerate(range(BATCH_SIZE, total, BATCH_SIZE), 2):
        chunk = rows[start:start + BATCH_SIZE]
        ws.append_rows(chunk, value_input_option='RAW', insert_data_option='INSERT_ROWS')
        print(f'  Batch {i}/{n_batches}: {len(chunk)} baris.')

    print(f'Upload selesai → "{ws.title}".')


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print('── DIZA: Download → Sheets ──────────────────────────')
    xlsx = download_diza()
    upload_to_sheets(xlsx)
    print('Selesai.')
