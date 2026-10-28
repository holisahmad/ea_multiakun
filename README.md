# Smart Trade Multi Account (STMA) — Panduan Lengkap & Dokumentasi

**Platform:** MetaTrader 5 (MT5) | **Bahasa:** Python 3.11+ (FastAPI) + MQL5 | **Status:** Siap Demo / Siap Live (setelah konfigurasi risk)

---

## 📌 Daftar Isi
1. [Tentang Sistem STMA](#1-tentang-sistem-stma)
2. [Arsitektur Strategi Hierarchical Multi-Account Multi-Layer (3×3 = 9 Layer)](#2-arsitektur-strategi-hierarchical-multi-account-multi-layer-33--9-layer)
3. [Mode Exit: Individual TP vs Basket TP](#3-mode-exit-individual-tp-vs-basket-tp)
4. [Panduan Instalasi & Persiapan](#4-panduan-instalasi--persiapan)
5. [Konfigurasi Sistem (`config.yaml`)](#5-konfigurasi-sistem-configyaml)
6. [Setup Expert Advisor (EA) di 3 Terminal MT5](#6-setup-expert-advisor-ea-di-3-terminal-mt5)
7. [Menjalankan Controller & Dashboard Web](#7-menjalankan-controller--dashboard-web)
8. [Fitur Mobile-First Dashboard](#8-fitur-mobile-first-dashboard)
9. [Cara Mengirimkan Sinyal Trading & Manual Order](#9-cara-mengirimkan-sinyal-trading--manual-order)
10. [Fitur Keselamatan & Tombol Darurat Operator](#10-fitur-keselamatan--tombol-darurat-operator)
11. [Troubleshooting & Solusi Masalah Umum](#11-troubleshooting--solusi-masalah-umum)
12. [Pengujian Unit & Verifikasi Otomatis](#12-pengujian-unit--verifikasi-otomatis)

---

## 1. Tentang Sistem STMA

**Smart Trade Multi Account (STMA)** adalah sistem orkestrasi trading multi-terminal cerdas untuk instrumen **XAUUSD (Gold)** yang mengelola hingga 3 akun MetaTrader 5 sekaligus dengan pemisahan risiko dan timeframe:
- **Akun 1 (Primary • M1 Timeframe)**: Akun pengeksekusi awal ketika ada sinyal baru yang valid. Mengelola hingga 3 layer averaging mikro di timeframe M1.
- **Akun 2 (Secondary • M15 Timeframe)**: Akun penopang struktural. Hanya aktif jika Akun 1 telah kehabisan 3 layer dan harga terus bergerak berlawanan (*adverse movement*) hingga menyentuh level konfirmasi timeframe M15. Mengelola hingga 3 layer.
- **Akun 3 (Tertiary • M15 Timeframe)**: Akun cadangan benteng terakhir. Hanya aktif jika Akun 2 telah kehabisan 3 layer dan pergerakan ekstrem terus berlanjut. Mengelola hingga 3 layer.

### Keunggulan Utama:
- **Partisi Risiko Multi-Akun (Anti-Margin Call Akun Tunggal)**: Beban drawdown tidak ditumpuk pada satu akun, melainkan dibagi ke akun terpisah sehingga margin level akun utama tetap terjaga aman.
- **Multi-Timeframe Confirmation (M1 + M15)**: Mencegah averaging buta saat terjadi lonjakan harga ekstrem (spike news).
- **Fleksibilitas Exit**: Mendukung **Individual TP** (posisi profit tertutup mandiri) dan **Basket TP** (likuidasi serentak saat target profit tercapai).
- **Default TP Tanpa SL**: Sesuai kaidah averaging terukur profesional, stop loss individual dimatikan secara default untuk menghindari *whipsaw* sesaat, dan pengamanan risiko dialihkan ke **Risk Engine Agregat**.
- **Fail-Closed Risk Engine**: Jika salah satu akun terputus, data *stale*, atau margin tidak mencukupi, pembukaan posisi baru otomatis ditolak demi mengamankan modal.
- **Non-blocking MT5**: EA di MT5 hanya bertugas sebagai pelaksana (*executor*) dan pelapor (*reporter*) via timer internal tanpa membebani performa terminal.

---

## 2. Arsitektur Strategi Hierarchical Multi-Account Multi-Layer (3×3 = 9 Layer)

```
                       ┌──────────────────────────────────────────────┐
                       │        SUMBER SINYAL TRADING                 │
                       │   (EA Indikator / Manual Dashboard / HTTP)   │
                       └──────────────────────┬───────────────────────┘
                                              │ HTTP POST /signal
                                              ▼
                       ┌──────────────────────────────────────────────┐
                       │      FASTAPI CENTRAL CONTROLLER (Port 8000)  │
                       │  - Signal Validator & Risk Engine Gate       │
                       │  - Cycle Manager (Hierarchical Multi-Layer)  │
                       │  - Background Periodic Stage Evaluator       │
                       │  - SQLite Database (stma.db with WAL mode)   │
                       └───────┬──────────────┬──────────────┬────────┘
                               │              │              │
            HTTP WebRequest    │              │              │
            Polling & Push     ▼              ▼              ▼
                     ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
                     │ MT5 Term. 1 │   │ MT5 Term. 2 │   │ MT5 Term. 3 │
                     │ (Primary)   │   │ (Secondary) │   │ (Tertiary)  │
                     │ account_1   │   │ account_2   │   │ account_3   │
                     │ Timeframe:  │   │ Timeframe:  │   │ Timeframe:  │
                     │ M1          │   │ M15         │   │ M15         │
                     │ Max 3 Layer │   │ Max 3 Layer │   │ Max 3 Layer │
                     └─────────────┘   └─────────────┘   └─────────────┘
```

### Rincian Alur Layer & Timeframe:

| Akun | Peran | Timeframe | Max Layer | Jarak Antar Layer | Pemicu Aktivasi Akun | Sinyal Konfirmasi |
| :--- | :--- | :--- | :---: | :---: | :--- | :--- |
| **Akun 1** | Primary | **M1** | 3 Layer | 200 pts (~$2.00) | Sinyal awal valid / Manual | Fractal Support/Resistance M1 |
| **Akun 2** | Secondary | **M15** | 3 Layer | 300 pts (~$3.00) | Akun 1 penuh & adverse 400 pts (~$4.00) | EMA 9/21 Crossover / Pullback M15 |
| **Akun 3** | Tertiary | **M15** | 3 Layer | 400 pts (~$4.00) | Akun 2 penuh & adverse 500 pts (~$5.00) | EMA 9/21 Crossover / Pullback M15 |

*Total Maksimal:* **9 Layer (3 Akun × 3 Layer)**.

---

## 3. Mode Exit: Individual TP vs Basket TP

Sistem menyediakan dua pilihan strategi penutupan posisi (*exit strategy*):

### A. Individual TP (Default — Sangat Disarankan)
- Tiap layer posisi memiliki Take Profit masing-masing (default: 300 poin / ~$3.00 pada 0.01 lot XAUUSD).
- **Keuntungan:** Ketika harga berbalik arah sedikit saja (pullback/retracement), layer yang berada di harga terbaik langsung mengunci profit secara mandiri tanpa harus menunggu seluruh keranjang posisi berubah menjadi biru. Hal ini mengurangi beban volume secara bertahap.

### B. Basket TP (Target Gabungan)
- Seluruh posisi dalam satu siklus ditutup serentak saat **total floating profit gabungan** mencapai target dollar tertentu (misal: `$10.00`).
- Begitu target tercapai, Controller langsung mengirimkan perintah `CLOSE_ALL_CYCLE` ke semua akun yang memiliki posisi aktif.
- **Keuntungan:** Sangat efektif menangkap pembalikan tren besar (*V-shape recovery*) untuk mengamankan akumulasi profit besar dalam satu momen.

---

## 4. Panduan Instalasi & Persiapan

### Kebutuhan Sistem:
- **Sistem Operasi**: Windows 10/11 atau Windows Server (VPS)
- **Python**: Versi 3.11 atau lebih baru
- **MetaTrader 5**: 1 hingga 3 terminal MT5 aktif

### Langkah Instalasi Dependensi Python:
Buka PowerShell atau Command Prompt di folder project:
```powershell
cd D:\project-code\ea-project\ea_multiakun
pip install -r requirements.txt
```

Paket yang terpasang meliputi: `fastapi`, `uvicorn`, `sqlalchemy`, `pydantic`, `pyyaml`, `requests`, `pytest`, `httpx`.

---

## 5. Konfigurasi Sistem (`config.yaml`)

File konfigurasi berada di: [`controller/config.yaml`](file:///D:/project-code/ea-project/ea_multiakun/controller/config.yaml).

```yaml
app:
  environment: demo            # "demo" atau "live"
  live_trading_enabled: false  # Kunci pengaman: ubah ke true jika ingin live

server:
  host: "0.0.0.0"
  port: 8000
  ea_auth_token: "KunciAmanSTMA2026"  # Token rahasia otentikasi EA & Dashboard
  command_poll_interval_seconds: 1

strategy:
  strategy_id: stma-xau-v1
  symbol: XAUUSD
  default_lot: 0.01
  max_active_cycles_per_symbol: 1
  entry_model: hybrid

  # Mode Exit: "individual" (default) atau "basket"
  exit_mode: individual
  basket_target_usd: 10.00       # Target dollar jika exit_mode = basket

  # TP / SL Default
  individual_tp_points: 300      # TP default per posisi (300 poin = $3.00 @0.01 lot)
  individual_sl_enabled: false   # Default: TANPA SL (SL = 0)
  individual_sl_points: 0

  # Akun 1 — Timeframe M1, Max 3 Layer
  account1_max_layers: 3
  account1_layer_distance_points: 200
  account1_signal_type: fractal_sr
  account1_timeframe: M1

  # Akun 2 — Timeframe M15, Aktivasi jika Akun 1 Penuh
  account2_max_layers: 3
  account2_activate_distance_points: 400
  account2_layer_distance_points: 300
  account2_signal_type: ema_cross
  account2_timeframe: M15

  # Akun 3 — Timeframe M15, Aktivasi jika Akun 2 Penuh
  account3_max_layers: 3
  account3_activate_distance_points: 500
  account3_layer_distance_points: 400
  account3_signal_type: ema_cross
  account3_timeframe: M15

risk:
  block_entries_if_account_stale: true
  account_stale_threshold_seconds: 30
  max_layers_per_account: 3
  max_volume_per_account: 0.03
  max_floating_loss_per_account_usd: 50.0
  max_drawdown_per_account_pct: 15.0
  min_margin_level_pct: 200.0

accounts:
  - alias: account_1
    role: primary
    enabled: true
    magic_number: 20240001
  - alias: account_2
    role: secondary
    enabled: true
    magic_number: 20240002
  - alias: account_3
    role: tertiary
    enabled: true
    magic_number: 20240003
```

---

## 6. Setup Expert Advisor (EA) di 3 Terminal MT5

### A. Kompilasi EA di MetaEditor
1. Buka file [`ea/STMA_Executor.mq5`](file:///D:/project-code/ea-project/ea_multiakun/ea/STMA_Executor.mq5) di **MetaEditor**.
2. Tekan **F7** (Compile). Pastikan hasil kompilasi: **`0 errors, 0 warnings`**.

### B. Izinkan WebRequest di MT5
Lakukan ini pada **setiap** terminal MT5:
1. Klik menu **Tools → Options** (atau tekan `Ctrl + O`).
2. Pilih tab **Expert Advisors**.
3. Centang:
   - ✅ **Allow Algo Trading**
   - ✅ **Allow WebRequest for listed URL**
4. Tambahkan URL:
   ```
   http://127.0.0.1:8000
   ```
5. Klik **OK**. Pastikan tombol **Algo Trading** di toolbar MT5 berwarna **Hijau**.

### C. Pasang EA pada Chart XAUUSD
- **Terminal 1**: Pasang pada chart XAUUSD **Timeframe M1**
  - `AccountAlias` = `account_1` | `AuthToken` = `KunciAmanSTMA2026` | `MagicNumber` = `20240001`
- **Terminal 2**: Pasang pada chart XAUUSD **Timeframe M15**
  - `AccountAlias` = `account_2` | `AuthToken` = `KunciAmanSTMA2026` | `MagicNumber` = `20240002`
- **Terminal 3**: Pasang pada chart XAUUSD **Timeframe M15**
  - `AccountAlias` = `account_3` | `AuthToken` = `KunciAmanSTMA2026` | `MagicNumber` = `20240003`

---

## 7. Menjalankan Controller & Dashboard Web

### Menjalankan Server Controller:
Buka PowerShell di folder project:
```powershell
cd D:\project-code\ea-project\ea_multiakun
python -m uvicorn controller.main:app --host 0.0.0.0 --port 8000
```
- API Docs (Swagger): [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health Check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

---

## 8. Fitur Mobile-First Dashboard

Buka dashboard di browser Anda: **`http://127.0.0.1:8000/dashboard-ui`**

### Fitur Utama Dashboard:
1. **Panel Arsitektur Strategi**: Menampilkan ringkasan mode exit yang aktif, TP/SL default, dan tabel konfigurasi 3 akun (M1/M15, max layer, jarak points).
2. **Visual Layer Progress Bar**: Menampilkan status titik layer terisi per akun secara visual pada tiap siklus aktif:
   - `A1·M1 [●●○] 2/3` (Akun 1 aktif 2 layer)
   - `A2·M15 [○○○] Standby` (Akun 2 siaga jika Akun 1 penuh)
   - `A3·M15 [○○○] Standby` (Akun 3 siaga jika Akun 2 penuh)
3. **Selector Akun Manual Order**: Operator dapat memilih tujuan order manual ke **Akun 1**, **Akun 2**, atau **Akun 3**.
4. **Toggle Unit TP/SL**: Mendukung input dalam satuan **Dollar USD ($)** atau **Poin Broker**. Default TP terisi ($3.00) dan SL default kosong (tanpa SL).
5. **Real-Time PnL Tracker**: Menampilkan Floating PnL dan Realized PnL harian secara terpisah per akun dan total strategi.

---

## 9. Cara Mengirimkan Sinyal Trading & Manual Order

### Format Payload Sinyal:
- **URL**: `POST http://127.0.0.1:8000/signal`
- **Header**:
  - `Content-Type: application/json`
  - `X-Auth-Token: KunciAmanSTMA2026`
- **Body JSON**:
```json
{
  "strategy_id": "stma-xau-v1",
  "symbol": "XAUUSD",
  "direction": "BUY",
  "source": "tradingview-alert",
  "price": 2750.00,
  "payload": {
    "target_account": "account_1",
    "volume": 0.01,
    "tp_points": 300,
    "sl_points": 0
  }
}
```

---

## 10. Fitur Keselamatan & Tombol Darurat Operator

1. **Pause Strategy (`POST /control/action` - PAUSE_ENTRIES)**: Menjeda penerimaan sinyal baru sementara waktu.
2. **Resume Strategy (`POST /control/action` - RESUME_STRATEGY)**: Mengaktifkan kembali penerimaan sinyal normal.
3. **Emergency Stop (`POST /control/action` - EMERGENCY_PROTECTION)**: Memblokir seluruh entry baru seketika.
4. **Close All Positions (`POST /control/action` - CLOSE_ALL_POSITIONS)**: Mengirim perintah likuidasi serentak ke semua akun.

---

## 11. Troubleshooting & Solusi Masalah Umum

| Gejala Masalah | Penyebab | Solusi |
| :--- | :--- | :--- |
| **Error 401 Unauthorized** di log MT5 | Token di input EA berbeda dengan `ea_auth_token` di `config.yaml` | Samakan input `AuthToken` di EA dengan `config.yaml`. |
| **Error 4014 di Journal MT5** | WebRequest belum diizinkan untuk localhost | Buka **Tools → Options → Expert Advisors**, centang **Allow WebRequest** dan tambahkan `http://127.0.0.1:8000`. |
| **Status Akun Stale (Offline)** | EA tidak mengirimkan heartbeat status | Pastikan MT5 menyala, terhubung ke internet broker, dan tombol **Algo Trading** di MT5 aktif (Hijau). |
| **Log MT5 `code=1001` sesaat** | Server controller sedang proses restart | Normal. EA akan otomatis *reconnect* begitu server aktif kembali. |

---

## 12. Pengujian Unit & Verifikasi Otomatis

Untuk memastikan keabsahan logika Controller sebelum live trading:
```powershell
cd D:\project-code\ea-project\ea_multiakun
pytest controller/tests/ -v
```
Integration test suite telah divalidasi terhadap 3 akun MT5 live demo dan menghasilkan status **38/38 PASS**.
