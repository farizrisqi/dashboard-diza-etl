"""
ETL DIZA — Hazard Report (Owner Tambang)
Baca langsung dari Google Sheets GID 1732323997 → klasifikasi Groq → upsert ke Supabase diza_reports
"""

import json, os, time
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
from groq import Groq, RateLimitError, APIStatusError
from supabase import create_client

load_dotenv()

SHEET_ID   = os.environ['SHEET_ID']
DIZA_GID   = 1732323997
PAGE_SIZE  = 500
BATCH_SIZE = 20
MODELS     = ['llama-3.3-70b-versatile', 'qwen/qwen3-32b', 'llama-3.1-8b-instant']

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

KATEGORI_BAHAYA = [
    'Debu & Visibilitas',
    'Kondisi Jalan & Akses',
    'Genangan & Drainase',
    'Longsoran & Geoteknik',
    'Rambu & Penanda',
    'Swabakar',
    'Dumpingan & Material',
    'Kebersihan & Perilaku',
    'Lain-Lain',
]

SYSTEM_PROMPT = f"""Kamu adalah analis HSE di tambang batubara SS6.
Tugasmu: klasifikasikan deskripsi bahaya ke SATU kategori yang paling dominan.

Kategori yang tersedia:
- Debu & Visibilitas     : debu tebal, jarak pandang berkurang, kondisi berdebu, ISPA
- Kondisi Jalan & Akses  : undulating, amblas, licin, bonyok, grade tidak standar, jalan sempit/berlubang
- Genangan & Drainase    : genangan air, water ponding, drainase buntu/tersumbat, rembesan, limpasan
- Longsoran & Geoteknik  : retakan, longsoran, lereng tegak/undercut, stabilitas lereng, slope kritis, potensi longsor
- Rambu & Penanda        : rambu rebah/roboh/tidak ada, mata kucing/delineator tidak terpasang, convex mirror, post guide
- Swabakar               : swabakar batubara, batubara temperatur tinggi, spontaneous combustion
- Dumpingan & Material   : freedump, dumping limiter tidak ada, bund wall, spoil, boulder berceceran, area dumping tidak standar
- Kebersihan & Perilaku  : sampah berserakan, tidak pakai APD/helm, pelanggaran prosedur, alat tidak di tempatnya
- Lain-Lain              : hanya jika benar-benar tidak masuk kategori manapun di atas

Aturan:
1. Pilih TEPAT SATU kategori yang paling dominan
2. Usahakan TIDAK menggunakan "Lain-Lain" — hampir semua kasus masuk salah satu kategori di atas
3. Jawab HANYA dengan JSON array: [{{"id": "...", "kategori_bahaya": "..."}}]
4. Jumlah objek harus sama dengan jumlah input"""

_DIZA_BULAN = {
    'Jan':1,'Feb':2,'Mar':3,'Apr':4,'Mei':5,'Jun':6,
    'Jul':7,'Agu':8,'Sep':9,'Okt':10,'Nov':11,'Des':12,
    'May':5,'Aug':8,'Oct':10,'Dec':12,
}

def parse_waktu(s: str):
    if not s: return None
    p = s.strip().split()
    if len(p) < 3: return None
    m = _DIZA_BULAN.get(p[1])
    if not m: return None
    year = int(p[2])
    if year > 2099:
        year -= 100  # typo century, e.g. 2126 → 2026
    t = p[3] if len(p) > 3 else '00:00'
    return f"{year}-{m:02d}-{int(p[0]):02d}T{t}:00"

_PLACEHOLDER = {'-', '.', 'n/a', 'tbd', 'belum', '–'}

def cell(r, i):
    return str(r[i]).strip() if len(r) > i and r[i] else ''

def parse_tanggal(s: str):
    if not s or s.lower() in _PLACEHOLDER: return None
    p = s.strip().split()
    if len(p) >= 3:
        m = _DIZA_BULAN.get(p[1])
        if m: return f"{int(p[2])}-{m:02d}-{int(p[0]):02d}"
    return None


def sb_fetch_all(sb, cols):
    all_data, page = [], 0
    while True:
        chunk = sb.table('diza_reports').select(cols) \
                  .range(page * PAGE_SIZE, (page + 1) * PAGE_SIZE - 1).execute().data
        all_data.extend(chunk)
        if len(chunk) < PAGE_SIZE: break
        page += 1
    return all_data

def sb_upsert(sb, rows):
    for i in range(0, len(rows), PAGE_SIZE):
        sb.table('diza_reports').upsert(rows[i:i+PAGE_SIZE]).execute()


def klasifikasi_groq(rows):
    """Klasifikasi deskripsi_bahaya via Groq. Return dict id → kategori_bahaya."""
    client = Groq(api_key=os.environ.get('GROQ_API_KEY', ''))
    chunks = [rows[i:i+BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    hasil  = {}

    for idx, chunk in enumerate(chunks, 1):
        print(f'  Groq batch {idx}/{len(chunks)} ({len(chunk)} item)...')
        lines = [
            f'- id: "{r["id"]}" | area: "{r["area_kerja"]}" | deskripsi: "{r["deskripsi"]}"'
            for r in chunk
        ]
        messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user',   'content': '\n'.join(lines)},
        ]

        resp = None
        for model in MODELS:
            delay = 5
            for attempt in range(1, 4):
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=messages, temperature=0, max_tokens=2048
                    )
                    if model != MODELS[0]:
                        print(f'    (fallback model: {model})')
                    break
                except RateLimitError:
                    if attempt == 3: break
                    print(f'    Rate limit, tunggu {delay}s...')
                    time.sleep(delay); delay *= 2
                except APIStatusError as e:
                    if e.status_code in (413, 429): break
                    if attempt == 3: raise
                    time.sleep(delay); delay *= 2
            if resp: break

        if not resp:
            print(f'  Batch {idx} gagal semua model, skip.')
            continue

        raw = resp.choices[0].message.content.strip()
        if '```' in raw:
            raw = raw.split('```')[1].lstrip('json').strip()
        # strip <think> tags (Qwen3)
        if '<think>' in raw:
            raw = raw[raw.rfind('</think>')+8:].strip()

        try:
            parsed = json.loads(raw)
            lain_count = 0
            for item in parsed:
                kat = item.get('kategori_bahaya', 'Lain-Lain')
                if kat not in KATEGORI_BAHAYA:
                    kat = 'Lain-Lain'
                if kat == 'Lain-Lain':
                    lain_count += 1
                hasil[str(item['id'])] = kat
            if lain_count:
                print(f'    ⚠ {lain_count} item masuk Lain-Lain di batch ini')
        except Exception as e:
            print(f'  Gagal parse batch {idx}: {e}')

        if idx < len(chunks):
            time.sleep(3)

    return hasil


def main():
    print('── DIZA ETL ──────────────────────────────────────')

    print('Koneksi ke Google Sheets...')
    creds = Credentials.from_service_account_info(
        json.loads(os.environ['GOOGLE_CREDS_JSON']), scopes=SCOPES
    )
    ws   = gspread.authorize(creds).open_by_key(SHEET_ID).get_worksheet_by_id(DIZA_GID)
    rows = ws.get_all_values()[1:]
    print(f'  {len(rows)} baris ditemukan di sheet.')

    hasil, skip = [], 0
    for r in rows:
        id_val = cell(r, 1)
        if not id_val:
            skip += 1
            continue
        hasil.append({
            'id'                 : id_val,
            'status'             : cell(r, 2),
            'waktu_temuan'       : parse_waktu(cell(r, 3)),
            'area_kerja'         : cell(r, 4),
            'lokasi'             : cell(r, 5),
            'kategori'           : cell(r, 6),
            'departemen_pic'     : cell(r, 7),
            'deskripsi'          : cell(r, 8),
            'status_banding'     : cell(r, 9),
            'alasan_banding'     : cell(r, 10),
            'eksekutor_perbaikan': cell(r, 11),
            'tanggal_selesai'    : parse_tanggal(cell(r, 12)),
            'foto_awal'          : cell(r, 13),
            'foto_perbaikan'     : cell(r, 14),
        })

    if skip:
        print(f'  Skip {skip} baris tanpa ID.')

    print('Koneksi ke Supabase...')
    sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])

    # Fetch yang sudah punya kategori_bahaya — skip re-klasifikasi
    print('  Cek kategori_bahaya yang sudah ada di Supabase...')
    sb_existing = {r['id']: r['kategori_bahaya']
                   for r in sb_fetch_all(sb, 'id,kategori_bahaya')
                   if r.get('kategori_bahaya')}
    print(f'  {len(sb_existing)} baris sudah punya kategori_bahaya → skip Groq.')

    perlu_klasifikasi = [r for r in hasil if r['id'] not in sb_existing]
    print(f'  {len(perlu_klasifikasi)} baris perlu klasifikasi Groq.')

    groq_map = dict(sb_existing)
    if perlu_klasifikasi:
        groq_map.update(klasifikasi_groq(perlu_klasifikasi))

    # Gabungkan hasil klasifikasi ke tiap baris
    for r in hasil:
        r['kategori_bahaya'] = groq_map.get(r['id'])

    # Hitung distribusi
    from collections import Counter
    dist = Counter(r['kategori_bahaya'] for r in hasil if r['kategori_bahaya'])
    print('\n  Distribusi kategori_bahaya:')
    for kat, n in sorted(dist.items(), key=lambda x: -x[1]):
        flag = ' ⚠' if kat == 'Lain-Lain' else ''
        print(f'    {kat}: {n}{flag}')

    print(f'\nUpsert {len(hasil)} baris...')
    sb_upsert(sb, hasil)
    print('  Upsert selesai.')

    print('Sync delete...')
    sb_all_ids  = {r['id'] for r in sb_fetch_all(sb, 'id')}
    sheet_ids   = {r['id'] for r in hasil}
    to_delete   = list(sb_all_ids - sheet_ids)
    if to_delete:
        print(f'  Menghapus {len(to_delete)} baris...')
        sb.table('diza_reports').delete().in_('id', to_delete).execute()
    else:
        print('  Tidak ada baris yang perlu dihapus.')

    print('DIZA ETL selesai.')

if __name__ == '__main__':
    main()
