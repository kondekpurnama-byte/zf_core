import os
import time
import requests
import datetime
import math

# ==============================================================================
# 1. KONFIGURASI SISTEM & MEMORI (V30.2 - ULTIMATE EDITION)
# ==============================================================================
TELEGRAM_TOKEN = "8896842450:AAFUjx_KneJ9NSsPHbjP_ogrGIQ1SRp_gSg"
TELEGRAM_CHAT_ID = "8800288482"

API_KEYS = [
    "82047e22427041a0a8ce3441809a4935",  
    "82047e22427041a0a8ce3441809a4935",  
    "82047e22427041a0a8ce3441809a4935",  
    "82047e22427041a0a8ce3441809a4935"   
]
current_key_index = 0
SIMBOL_TICKER = "XAU/USD"

# --- BAUT STELAN: MESIN 1 (SNIPER REVERSAL) ---
K_BASE_MIN = 2.7          
K_BASE_MAX = 3.0          
BATAS_KRITIS_ZF = 0.80    
SL_MAX_PIPS = 5.0         # Maksimal 50 pips pengaman margin

# --- BAUT STELAN: MESIN 2 (PULLBACK) & MESIN 3 (BREAKOUT) ---
PULLBACK_DECAY = 0.15     # Pantulan harus kuat, minimal 15% dari ATR (Konfirmasi)
MIN_BODY_RATIO = 0.60     # Candle pemicu harus solid (Body minimal 60% dari panjang total)
MIN_VELOCITY_PIPS = 3.0   # Breakout POC butuh akselerasi bodi M1 minimal 30 pips

# --- KONFIGURASI TP KONSERVATIF & REALISTIS (ATR MULTIPLIER) ---
TP_MULTIPLIER_PULLBACK = 1.2  # Mesin 2: TP terjauh karena searah arus
TP_MULTIPLIER_BREAKOUT = 1.0  # Mesin 3: TP Hit & Run menghindari uang fresh
TP_MULTIPLIER_SNIPER   = 0.8  # Mesin 1: TP Cepat karena melawan arus
TP_MIN_PIPS = 2.0         
TP_MAX_PIPS = 8.0         

# --- SMART CACHING HIBRIDA (SETELAN EKONOMIS) ---
CACHE_API = {}
API_COOLDOWN = {"1min": 5, "15min": 15, "30min": 300, "1h": 3600}
GAMMA_LOCK = {"BUY 📈": None, "SELL 📉": None}
DURASI_LOCK_MENIT = 45.0  

# Memori Makro & State Machine
H1_R_LEVELS, H1_S_LEVELS = [], []
H1_IMBALANCE_ZONES = []
LAST_MAP_FETCH = None
MACRO_TREND_M30 = "TRANSISI ⚪"
MACRO_TREND_H1 = "TRANSISI ⚪"
STATUS_DXY = "TIDAK DIKETAHUI ⚪"
LAST_MAP_SENT_HOUR = None 

# State Engine ZF
GAMMA_STATE = "IDLE"      
GAMMA_PEAK_ZF = 0.0       
GAMMA_ARAH = ""           
GAMMA_START_TIME = None   
GAMMA_SETUP_TYPE = ""     
COOLDOWN_START_TIME = None
ZONA_BERITA_AKTIF = False 
GAMMA_HARGA_EKSTREM = 0.0 

# Memori POC
POC_BAWAH = 0.0
POC_ATAS = 0.0
POC_VALID = False

# Memori Radar Berita (Khusus USD)
JADWAL_BERITA_24J = []
LAST_NEWS_FETCH = None
BERITA_TERNOTIFIKASI = set()

# ==============================================================================
# 2. FUNGSI PENDUKUNG DASAR & DXY
# ==============================================================================
def kirim_pesan_telegram(pesan):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": pesan, "parse_mode": "Markdown"}
    try: requests.post(url, data=payload, timeout=10)
    except Exception: pass

def tarik_data_twelvedata(interval, batas_candle, simbol="XAU/USD"):
    global current_key_index, CACHE_API
    waktu_skrg = time.time()
    kunci_cache = f"{simbol}_{interval}"
    if kunci_cache in CACHE_API:
        if (waktu_skrg - CACHE_API[kunci_cache]["waktu"]) < API_COOLDOWN.get(interval, 60):
            return CACHE_API[kunci_cache]["data"]
    for _ in range(len(API_KEYS)):
        active_key = API_KEYS[current_key_index]
        url = f"https://api.twelvedata.com/time_series?symbol={simbol}&interval={interval}&outputsize={batas_candle}&apikey={active_key}"
        try:
            response = requests.get(url, timeout=10).json()
            if "status" in response and response["status"] == "error":
                current_key_index = (current_key_index + 1) % len(API_KEYS); continue
            data_bersih = response['values'][::-1]
            CACHE_API[kunci_cache] = {"waktu": waktu_skrg, "data": data_bersih}
            return data_bersih
        except Exception: current_key_index = (current_key_index + 1) % len(API_KEYS)
    return CACHE_API.get(kunci_cache, {}).get("data")

def tarik_data_dxy_murni():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=30m&range=10d"
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        closes = res['chart']['result'][0]['indicators']['quote'][0]['close']
        return [{'close': float(c)} for c in closes if c is not None]
    except Exception: return None

def hitung_ema(kumpulan_candle, periode):
    ema = []; multiplier = 2 / (periode + 1)
    closes = [float(c['close']) for c in kumpulan_candle]
    if len(closes) < periode: return [None] * len(closes)
    ema.extend([None] * (periode - 1)); ema.append(sum(closes[:periode]) / periode)
    for i in range(periode, len(closes)): ema.append((closes[i] - ema[-1]) * multiplier + ema[-1])
    return ema

def hitung_adx(kumpulan_candle, periode=14):
    if len(kumpulan_candle) < periode * 2: return 0.0
    trs, pDMs, nDMs = [], [], []
    for i in range(1, len(kumpulan_candle)):
        h, l = float(kumpulan_candle[i]['high']), float(kumpulan_candle[i]['low'])
        h_p, l_p, c_p = float(kumpulan_candle[i-1]['high']), float(kumpulan_candle[i-1]['low']), float(kumpulan_candle[i-1]['close'])
        tr = max(h - l, abs(h - c_p), abs(l - c_p))
        up_m, down_m = h - h_p, l_p - l
        pdm = up_m if up_m > down_m and up_m > 0 else 0
        ndm = down_m if down_m > up_m and down_m > 0 else 0
        trs.append(tr); pDMs.append(pdm); nDMs.append(ndm)
    dx_list = []
    for i in range(periode, len(trs)):
        tr_sum = sum(trs[i-periode:i])
        if tr_sum == 0: continue
        pdi = 100 * (sum(pDMs[i-periode:i]) / tr_sum); ndi = 100 * (sum(nDMs[i-periode:i]) / tr_sum)
        dx_list.append(100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0)
    return sum(dx_list[-periode:]) / periode if len(dx_list) >= periode else 0.0

def buat_progress_bar(nilai): return "█" * min(10, max(0, int(nilai * 10))) + "░" * (10 - min(10, max(0, int(nilai * 10))))

def hitung_blok_poc_institusional(data_m30):
    candle_relevan = data_m30[-240:] 
    if not candle_relevan: return None, None
    harga_tertinggi, harga_terendah = max(float(c['high']) for c in candle_relevan), min(float(c['low']) for c in candle_relevan)
    ukuran_zona = (harga_tertinggi - harga_terendah) / 10
    if ukuran_zona == 0: return harga_tertinggi, harga_terendah
    zona_volume = {i: 0.0 for i in range(10)}
    for c in candle_relevan:
        h, l, v, o, cl = float(c['high']), float(c['low']), float(c.get('volume', 0.0)), float(c['open']), float(c['close'])
        for i in range(10):
            b_bawah, b_atas = harga_terendah + (i * ukuran_zona), harga_terendah + ((i+1) * ukuran_zona)
            if h >= b_bawah and l <= b_atas: zona_volume[i] += v * 2.0 if abs(o - cl) <= (h - l) * 0.3 else v
    if not zona_volume: return None, None
    zona_poc = max(zona_volume, key=zona_volume.get)
    vol_tertinggi = zona_volume[zona_poc]
    vol_lain = [v for i, v in zona_volume.items() if i != zona_poc]
    if vol_tertinggi < ((sum(vol_lain) / len(vol_lain)) * 1.5 if vol_lain else 0.0): return None, None 
    b_bawah_blok, b_atas_blok = harga_terendah + (zona_poc * ukuran_zona), harga_terendah + ((zona_poc+1) * ukuran_zona)
    if zona_poc > 0 and zona_volume[zona_poc-1] > vol_tertinggi * 0.5: b_bawah_blok -= ukuran_zona
    if zona_poc < 9 and zona_volume[zona_poc+1] > vol_tertinggi * 0.5: b_atas_blok += ukuran_zona
    return b_bawah_blok, b_atas_blok

def tarik_kalender_ekonomi():
    global JADWAL_BERITA_24J, LAST_NEWS_FETCH
    try:
        res = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        waktu_skrg, JADWAL_BERITA_24J = datetime.datetime.now(), []
        for item in res:
            if item.get("country") == "USD" and item.get("impact") == "High":
                w_lokal = datetime.datetime.fromisoformat(item.get("date")).astimezone().replace(tzinfo=None)
                if -1 <= (w_lokal - waktu_skrg).total_seconds() / 3600.0 <= 24:
                    JADWAL_BERITA_24J.append({"title": item.get("title"), "time": w_lokal, "hari": "Hari Ini" if w_lokal.date() == waktu_skrg.date() else "Besok"})
        JADWAL_BERITA_24J.sort(key=lambda x: x["time"]); LAST_NEWS_FETCH = waktu_skrg
    except Exception: pass

def periksa_radar_berita(harga_sekarang, poc_bawah, poc_atas, p_pure, nilai_zf, status_dxy):
    global JADWAL_BERITA_24J, BERITA_TERNOTIFIKASI, LAST_NEWS_FETCH, ZONA_BERITA_AKTIF
    waktu_skrg = datetime.datetime.now()
    if LAST_NEWS_FETCH is None or (waktu_skrg - LAST_NEWS_FETCH).total_seconds() >= 21600: tarik_kalender_ekonomi()
    ZONA_BERITA_AKTIF = False
    for event in JADWAL_BERITA_24J:
        judul, target_waktu = event["title"], event["time"]
        selisih_detik = (target_waktu - waktu_skrg).total_seconds()
        if -1800 <= selisih_detik <= 600: ZONA_BERITA_AKTIF = True
        if 300 <= selisih_detik <= 600:
            id_berita = f"USD_{judul}_{target_waktu.strftime('%H:%M')}"
            if id_berita not in BERITA_TERNOTIFIKASI:
                drift = ((harga_sekarang - p_pure) / p_pure) * 100
                st_karet = f"Kendur / Stabil ({drift:.3f}%)" if nilai_zf < 0.40 else f"Tegang ({drift:.3f}%)"
                arah = "🌪️ EKSPANSI LIAR" if nilai_zf < 0.40 else ("SELL 📉" if harga_sekarang > p_pure else "BUY 📈")
                poc_analisa = f"{poc_bawah:.2f} - {poc_atas:.2f}" if poc_bawah else "N/A"
                pesan = (f"📰 *ALARM RADAR BERITA: HIGH IMPACT USD!* 📰\n\n🔥 *Event:* `{judul}`\n⏰ *Rilis:* `{target_waktu.strftime('%H:%M:%S')} WIB`\n"
                         f"⏳ *Hitung Mundur:* *± {int(selisih_detik / 60)} Menit!*\n\n🗺️ *MEDAN KETEGANGAN:*\n• Hrg: `{harga_sekarang:.2f}` | Pure: `{p_pure:.2f}`\n"
                         f"• Karet: `{st_karet}` (ZF: {nilai_zf:.2f})\n🔮 *PREDIKSI: {arah}*\n")
                kirim_pesan_telegram(pesan); BERITA_TERNOTIFIKASI.add(id_berita)

# ==============================================================================
# 3. ENGINE INTELIJEN HIBRIDA (TREN M30, H1 & S&D)
# ==============================================================================
def update_peta_makro_hibrida(harga_sekarang, data_m30, data_h1):
    global H1_R_LEVELS, H1_S_LEVELS, LAST_MAP_FETCH, MACRO_TREND_M30, MACRO_TREND_H1, STATUS_DXY
    print("\r ⚙️ Memperbarui Peta Intelijen Hibrida MTF...", end="", flush=True)
    if data_m30:
        e200_m30, e50_m30 = hitung_ema(data_m30, 200), hitung_ema(data_m30, 50)
        if e200_m30[-1] and e50_m30[-1]:
            if harga_sekarang > e50_m30[-1] > e200_m30[-1]: MACRO_TREND_M30 = "UPTREND 🟢"
            elif harga_sekarang < e50_m30[-1] < e200_m30[-1]: MACRO_TREND_M30 = "DOWNTREND 🔴"
            else: MACRO_TREND_M30 = "TRANSISI ⚪"
            
    if data_h1:
        e200_h1, e50_h1 = hitung_ema(data_h1, 200), hitung_ema(data_h1, 50)
        if e200_h1[-1] and e50_h1[-1]:
            if harga_sekarang > e50_h1[-1] > e200_h1[-1]: MACRO_TREND_H1 = "UPTREND 🟢"
            elif harga_sekarang < e50_h1[-1] < e200_h1[-1]: MACRO_TREND_H1 = "DOWNTREND 🔴"
            else: MACRO_TREND_H1 = "TRANSISI ⚪"

    data_dxy = tarik_data_dxy_murni()
    if data_dxy and len(data_dxy) >= 50:
        e50_dxy = hitung_ema(data_dxy, 50)
        STATUS_DXY = "BULLISH 🟢" if float(data_dxy[-1]['close']) > e50_dxy[-1] else "BEARISH 🔴"
    else: STATUS_DXY = "GANGGUAN ⚪"

    if not data_h1: return
    zona_sup, zona_dem = [], []
    for i in range(8, len(data_h1) - 8):
        c_h, c_l, c_o, c_c = float(data_h1[i]['high']), float(data_h1[i]['low']), float(data_h1[i]['open']), float(data_h1[i]['close'])
        if all(c_h >= float(data_h1[i-j]['high']) for j in range(1, 9)) and all(c_h >= float(data_h1[i+j]['high']) for j in range(1, 9)):
            if not any(float(data_h1[k]['close']) > c_h for k in range(i + 1, len(data_h1))): zona_sup.append((max(c_o, c_c), c_h))
        if all(c_l <= float(data_h1[i-j]['low']) for j in range(1, 9)) and all(c_l <= float(data_h1[i+j]['low']) for j in range(1, 9)):
            if not any(float(data_h1[k]['close']) < c_l for k in range(i + 1, len(data_h1))): zona_dem.append((c_l, min(c_o, c_c)))
    H1_R_LEVELS = sorted([z for z in zona_sup if z[1] > harga_sekarang], key=lambda x: x[0])[:3]
    H1_S_LEVELS = sorted([z for z in zona_dem if z[0] < harga_sekarang], key=lambda x: x[1], reverse=True)[:3]
    LAST_MAP_FETCH = datetime.datetime.now()

# ==============================================================================
# 4. ENGINE SCOUT V30.2 (ULTIMATE QUANTUM BRAIN)
# ==============================================================================
def analisa_pasar():
    global LAST_MAP_SENT_HOUR, LAST_MAP_FETCH
    global GAMMA_STATE, GAMMA_PEAK_ZF, GAMMA_ARAH, GAMMA_START_TIME, GAMMA_SETUP_TYPE
    global COOLDOWN_START_TIME, POC_BAWAH, POC_ATAS, POC_VALID, GAMMA_LOCK
    global ZONA_BERITA_AKTIF, GAMMA_HARGA_EKSTREM
    
    waktu_skrg = datetime.datetime.now()
    data_m1 = tarik_data_twelvedata("1min", 15) 
    data_m15 = tarik_data_twelvedata("15min", 150)
    data_m30 = tarik_data_twelvedata("30min", 300) 
    data_h1 = tarik_data_twelvedata("1h", 300)
    
    if not data_m1 or not data_m15 or not data_m30 or not data_h1: return
    harga_sekarang = float(data_m1[-1]['close']) 
    
    if LAST_MAP_FETCH is None or (waktu_skrg - LAST_MAP_FETCH).total_seconds() >= 3600:
        update_peta_makro_hibrida(harga_sekarang, data_m30, data_h1)

    pb, pa = hitung_blok_poc_institusional(data_m30)
    if pb is not None: POC_BAWAH, POC_ATAS, POC_VALID = pb, pa, True
    else: POC_VALID = False

    closes_20 = [float(x['close']) for x in data_m15[-21:-1]]
    p_pure_20 = sum(closes_20) / 20.0 
    var_20 = sum((x - p_pure_20) ** 2 for x in closes_20) / 19.0
    sigma_20 = math.sqrt(var_20) if var_20 > 0 else 1e-5 
    
    tr_list = [max(float(data_m15[i]['high']) - float(data_m15[i]['low']), abs(float(data_m15[i]['high']) - float(data_m15[i-1]['close'])), abs(float(data_m15[i]['low']) - float(data_m15[i-1]['close']))) for i in range(1, len(data_m15))]
    atr_m15 = sum(tr_list[-14:]) / 14.0
    k_base_adp = max(K_BASE_MIN, min(K_BASE_MIN * (atr_m15 / (sum(tr_list[-100:])/100.0 if len(tr_list)>=100 else atr_m15)), K_BASE_MAX))

    d_res_murni = (abs(harga_sekarang - p_pure_20) / p_pure_20) * 100
    d_res_norm = abs(harga_sekarang - p_pure_20) / sigma_20
    
    vol_hist = [float(x.get('volume', 1.0)) for x in data_m15[-25:-1]]
    v_avg_24 = sum(vol_hist) / len(vol_hist) if vol_hist else 1.0
    vol_m15_now = float(data_m15[-1].get('volume', 1.0))
    nilai_zf = min(max(abs(vol_m15_now - v_avg_24), 1.0) / vol_m15_now if vol_m15_now > 0 else 1.0, 1.0) * math.tanh(d_res_norm / k_base_adp)
    
    o_m1, c_m1, h_m1, l_m1 = float(data_m1[-1]['open']), float(data_m1[-1]['close']), float(data_m1[-1]['high']), float(data_m1[-1]['low'])
    tr_m1 = h_m1 - l_m1
    body_ratio_m1 = abs(c_m1 - o_m1) / tr_m1 if tr_m1 > 0 else 0
    body_pips_m1 = abs(c_m1 - o_m1) * 10 
    
    vol_m1_list = [float(x.get('volume', 1.0)) for x in data_m1[-11:-1]]
    avg_vol_m1 = sum(vol_m1_list) / len(vol_m1_list) if vol_m1_list else 1.0
    is_m1_vol_spike = float(data_m1[-1].get('volume', 1.0)) > (avg_vol_m1 * 1.5)

    adx_m15 = hitung_adx(data_m15, 14)
    periksa_radar_berita(harga_sekarang, pb, pa, p_pure_20, nilai_zf, STATUS_DXY)
    
    batas_trigger = 0.92 if ZONA_BERITA_AKTIF else BATAS_KRITIS_ZF
    status_gamma = "STANDBY"

    if GAMMA_STATE == "IDLE":
        if nilai_zf >= batas_trigger:
            GAMMA_STATE, GAMMA_PEAK_ZF, GAMMA_ARAH, GAMMA_START_TIME = "ARMED", nilai_zf, "SELL 📉" if harga_sekarang > p_pure_20 else "BUY 📈", waktu_skrg
            GAMMA_HARGA_EKSTREM, GAMMA_SETUP_TYPE = harga_sekarang, "NEWS_SNAPBACK" if ZONA_BERITA_AKTIF else "REVERSAL_SNIPER"
            status_gamma = f"🔒 ARMED (SNIPER)! Mengunci Puncak/Lembah (ZF: {nilai_zf:.2f})"
        
        elif adx_m15 > 25.0 and d_res_murni < 0.15 and not ZONA_BERITA_AKTIF:
            is_mtf_bull = "UPTREND" in MACRO_TREND_M30 and "UPTREND" in MACRO_TREND_H1
            is_mtf_bear = "DOWNTREND" in MACRO_TREND_M30 and "DOWNTREND" in MACRO_TREND_H1
            is_volume_kering = vol_m15_now < v_avg_24 
            
            if is_mtf_bull and harga_sekarang >= p_pure_20 and is_volume_kering:
                GAMMA_STATE, GAMMA_ARAH, GAMMA_START_TIME, GAMMA_SETUP_TYPE, GAMMA_HARGA_EKSTREM = "ARMED", "BUY 📈", waktu_skrg, "TREND_PULLBACK", harga_sekarang
                status_gamma = "🏄 ARMED (PULLBACK)! Volume Kering & MTF Bullish Selaras."
            elif is_mtf_bear and harga_sekarang <= p_pure_20 and is_volume_kering:
                GAMMA_STATE, GAMMA_ARAH, GAMMA_START_TIME, GAMMA_SETUP_TYPE, GAMMA_HARGA_EKSTREM = "ARMED", "SELL 📉", waktu_skrg, "TREND_PULLBACK", harga_sekarang
                status_gamma = "🏄 ARMED (PULLBACK)! Volume Kering & MTF Bearish Selaras."

        elif POC_VALID and (POC_BAWAH <= harga_sekarang <= POC_ATAS) and not ZONA_BERITA_AKTIF:
            GAMMA_STATE, GAMMA_ARAH, GAMMA_START_TIME, GAMMA_SETUP_TYPE = "ARMED", "WAITING", waktu_skrg, "POC_BREAKOUT"
            status_gamma = "🚀 ARMED (BREAKOUT)! Mengintai Tembok POC."

    elif GAMMA_STATE == "ARMED":
        durasi_menit = (waktu_skrg - GAMMA_START_TIME).total_seconds() / 60.0
        infleksi_terjadi = False

        if GAMMA_SETUP_TYPE in ["TREND_PULLBACK", "POC_BREAKOUT"] and nilai_zf >= batas_trigger:
            GAMMA_SETUP_TYPE = "NEWS_SNAPBACK" if ZONA_BERITA_AKTIF else "REVERSAL_SNIPER"
            GAMMA_ARAH = "SELL 📉" if harga_sekarang > p_pure_20 else "BUY 📈"
            GAMMA_HARGA_EKSTREM = harga_sekarang
            status_gamma = f"⚠️ KUDETA! Mesin 1 Mengambil Alih (Tensi Ekstrem: {nilai_zf:.2f})"
            return

        if GAMMA_SETUP_TYPE in ["REVERSAL_SNIPER", "NEWS_SNAPBACK"]:
            if nilai_zf > GAMMA_PEAK_ZF: GAMMA_PEAK_ZF = nilai_zf
            jarak_trigger = atr_m15 * 0.15 
            if GAMMA_ARAH == "SELL 📉":
                if harga_sekarang > GAMMA_HARGA_EKSTREM: GAMMA_HARGA_EKSTREM = harga_sekarang
                if (GAMMA_HARGA_EKSTREM - harga_sekarang) >= jarak_trigger: infleksi_terjadi = True
                status_gamma = f"🎯 SNIPER ({durasi_menit:.1f}m).. Drop: {(GAMMA_HARGA_EKSTREM - harga_sekarang):.2f}/{jarak_trigger:.2f}"
            else:
                if harga_sekarang < GAMMA_HARGA_EKSTREM: GAMMA_HARGA_EKSTREM = harga_sekarang
                if (harga_sekarang - GAMMA_HARGA_EKSTREM) >= jarak_trigger: infleksi_terjadi = True
                status_gamma = f"🎯 SNIPER ({durasi_menit:.1f}m).. Naik: {(harga_sekarang - GAMMA_HARGA_EKSTREM):.2f}/{jarak_trigger:.2f}"
            if not infleksi_terjadi and (nilai_zf < 0.45 or durasi_menit > 60): GAMMA_STATE = "IDLE"

        elif GAMMA_SETUP_TYPE == "TREND_PULLBACK":
            jarak_trigger = atr_m15 * PULLBACK_DECAY
            if GAMMA_ARAH == "BUY 📈":
                if harga_sekarang < GAMMA_HARGA_EKSTREM: GAMMA_HARGA_EKSTREM = harga_sekarang
                if (harga_sekarang - GAMMA_HARGA_EKSTREM) >= jarak_trigger and body_ratio_m1 >= MIN_BODY_RATIO: infleksi_terjadi = True
                status_gamma = f"🏄 PULLBACK ({durasi_menit:.1f}m).. Decay: {(harga_sekarang - GAMMA_HARGA_EKSTREM):.2f}/{jarak_trigger:.2f} | Bodi: {body_ratio_m1*100:.0f}%"
            else:
                if harga_sekarang > GAMMA_HARGA_EKSTREM: GAMMA_HARGA_EKSTREM = harga_sekarang
                if (GAMMA_HARGA_EKSTREM - harga_sekarang) >= jarak_trigger and body_ratio_m1 >= MIN_BODY_RATIO: infleksi_terjadi = True
                status_gamma = f"🏄 PULLBACK ({durasi_menit:.1f}m).. Decay: {(GAMMA_HARGA_EKSTREM - harga_sekarang):.2f}/{jarak_trigger:.2f} | Bodi: {body_ratio_m1*100:.0f}%"
            if not infleksi_terjadi and (d_res_murni > 0.3 or durasi_menit > 30): GAMMA_STATE = "IDLE"

        elif GAMMA_SETUP_TYPE == "POC_BREAKOUT":
            is_valid_breakout = is_m1_vol_spike and (body_pips_m1 >= MIN_VELOCITY_PIPS)
            if harga_sekarang > POC_ATAS and is_valid_breakout: GAMMA_ARAH, infleksi_terjadi = "BUY 📈", True
            elif harga_sekarang < POC_BAWAH and is_valid_breakout: GAMMA_ARAH, infleksi_terjadi = "SELL 📉", True
            status_gamma = f"🚀 BREAKOUT ({durasi_menit:.1f}m).. Velo Pips: {body_pips_m1:.1f}/{MIN_VELOCITY_PIPS}"
            if not infleksi_terjadi and (durasi_menit > 60 or harga_sekarang > POC_ATAS + 2.0 or harga_sekarang < POC_BAWAH - 2.0): GAMMA_STATE = "IDLE"

        if infleksi_terjadi:
            if GAMMA_LOCK[GAMMA_ARAH] and (waktu_skrg - GAMMA_LOCK[GAMMA_ARAH]).total_seconds() <= (DURASI_LOCK_MENIT * 60):
                GAMMA_STATE = "IDLE"; return
            
            if GAMMA_SETUP_TYPE in ["REVERSAL_SNIPER", "NEWS_SNAPBACK"]: target_tp_raw = atr_m15 * TP_MULTIPLIER_SNIPER
            elif GAMMA_SETUP_TYPE == "POC_BREAKOUT": target_tp_raw = atr_m15 * TP_MULTIPLIER_BREAKOUT  
            else: target_tp_raw = atr_m15 * TP_MULTIPLIER_PULLBACK  

            tp_dinamis_target = max(TP_MIN_PIPS, min(target_tp_raw, TP_MAX_PIPS))
            jarak_sigma = 3 * sigma_20
            sl_dinamis = harga_sekarang - jarak_sigma if "BUY" in GAMMA_ARAH else harga_sekarang + jarak_sigma
            tp_dinamis = harga_sekarang + tp_dinamis_target if "BUY" in GAMMA_ARAH else harga_sekarang - tp_dinamis_target

            if "BUY" in GAMMA_ARAH and (harga_sekarang - sl_dinamis) > SL_MAX_PIPS: sl_dinamis = harga_sekarang - SL_MAX_PIPS
            elif "SELL" in GAMMA_ARAH and (sl_dinamis - harga_sekarang) > SL_MAX_PIPS: sl_dinamis = harga_sekarang + SL_MAX_PIPS

            status_jalur = "⚪ Terbuka"
            if POC_VALID and GAMMA_SETUP_TYPE != "POC_BREAKOUT":
                if "BUY" in GAMMA_ARAH:
                    if harga_sekarang < POC_BAWAH and POC_BAWAH < tp_dinamis: tp_dinamis = POC_BAWAH - 1.0; status_jalur = "⚠️ TP Disusutkan (POC Block)"
                    elif harga_sekarang > POC_ATAS: status_jalur = "🛡️ Solid (POC Melindungi)"
                else:
                    if harga_sekarang > POC_ATAS and POC_ATAS > tp_dinamis: tp_dinamis = POC_ATAS + 1.0; status_jalur = "⚠️ TP Disusutkan (POC Block)"
                    elif harga_sekarang < POC_BAWAH: status_jalur = "🛡️ Solid (POC Melindungi)"

            ikon = "🏄" if GAMMA_SETUP_TYPE == "TREND_PULLBACK" else "🚀" if GAMMA_SETUP_TYPE == "POC_BREAKOUT" else "🎯"
            pesan = (f"{ikon} *KONFIRMASI EKSEKUSI [{GAMMA_SETUP_TYPE}]: {GAMMA_ARAH}* {ikon}\n\n"
                     f"⚙️ *Validasi Kinetik V30.2:*\n• Mode: `{GAMMA_SETUP_TYPE}`\n• Status DXY: *{STATUS_DXY}*\n• Analisa Jalur: *{status_jalur}*\n\n"
                     f"💵 *Hrg Masuk:* `{harga_sekarang:.2f}`\n🛡️ *SL:* `{sl_dinamis:.2f}` ({abs(harga_sekarang-sl_dinamis)*10:.1f} Pips)\n"
                     f"💰 *TP:* `{tp_dinamis:.2f}` ({abs(harga_sekarang-tp_dinamis)*10:.1f} Pips)")
            
            if jarak_sigma > 7.0 and not ZONA_BERITA_AKTIF: pesan += f"\n\n⚠️ *ANOMALI KINETIK!* Volatilitas ekstrem. Bonceng arus amankan profit!"
            kirim_pesan_telegram(pesan)
            GAMMA_LOCK[GAMMA_ARAH], GAMMA_STATE, COOLDOWN_START_TIME = waktu_skrg, "COOLDOWN", waktu_skrg

    elif GAMMA_STATE == "COOLDOWN":
        if nilai_zf < 0.55 or (waktu_skrg - COOLDOWN_START_TIME).total_seconds() / 60.0 > 15.0: GAMMA_STATE = "IDLE"
        status_gamma = "⏳ COOLDOWN..."

    os.system('clear' if os.name == 'posix' else 'cls')  
    print("=" * 60)
    print(" 🤖 ZF-CORE V30.2 (ULTIMATE QUANTUM EDITION - 24 JAM)")
    print(f" ⏰ {waktu_skrg.strftime('%H:%M:%S')} | DXY M30: {STATUS_DXY}")
    print(f" 📈 MTF Trend: [M30: {MACRO_TREND_M30}] - [H1: {MACRO_TREND_H1}]")
    print("=" * 60)
    print(f" 💵 Hrg (M1) : {harga_sekarang:.2f}     | 🧱 POC: {POC_BAWAH:.2f} - {POC_ATAS:.2f}" if POC_VALID else f" 💵 Hrg (M1) : {harga_sekarang:.2f}     | 🧱 POC: N/A")
    print(f" 📐 Drift    : {d_res_murni:.4f}% | 📊 ADX M15: {adx_m15:.1f}")
    print(f" 🎯 ZF-Score : {nilai_zf:.4f} [{buat_progress_bar(nilai_zf)}]")
    if ZONA_BERITA_AKTIF: print(f" 🚨 MODE SNAP-BACK AKTIF (Batas Tensi: 0.92)")
    print(f" 🔍 Status   : {status_gamma}")
    print("-" * 60)
    
    jam_sekarang = waktu_skrg.hour
    if jam_sekarang in [7, 15, 18] and LAST_MAP_SENT_HOUR != jam_sekarang:
        pesan_peta = (f"🗺️ *PETA INTELIJEN V30.2 (UPDATE {jam_sekarang}:00 WIB)* 🗺️\n\n💵 XAU/USD (M15): `{harga_sekarang:.2f}`\n🦅 DXY M30: *{STATUS_DXY}*\n"
                      f"📐 MTF Trend: *M30 {MACRO_TREND_M30}* | *H1 {MACRO_TREND_H1}*\n\n☁️ *LANGIT H1:*\n" + 
                      ("\n".join([f"R{i+1}: `{z[0]:.2f}` - `{z[1]:.2f}`" for i, z in enumerate(H1_R_LEVELS)]) if H1_R_LEVELS else "Kosong\n") +
                      f"\n🧱 *LANTAI H1:*\n" + ("\n".join([f"S{i+1}: `{z[1]:.2f}` - `{z[0]:.2f}`" for i, z in enumerate(H1_S_LEVELS)]) if H1_S_LEVELS else "Kosong\n"))
        kirim_pesan_telegram(pesan_peta); LAST_MAP_SENT_HOUR = jam_sekarang

if __name__ == "__main__":
    while True:
        try:
            analisa_pasar()
            for sisa in range(5 if GAMMA_STATE == "ARMED" else 30, 0, -1):
                print(f"\r ⏳ Radar berputar dalam: {sisa:02d} detik...   ", end="", flush=True); time.sleep(1)
                
        except KeyboardInterrupt: print("\n\n⚠️ Sistem dimatikan oleh Arsitek."); break
        except Exception as e: print(f"\n\n❌ ERROR TERDETEKSI: {e}"); time.sleep(10)
 
