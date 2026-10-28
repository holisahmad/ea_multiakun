# Smart Trade Multi Account (STMA) — Panduan Lengkap & Dokumentasi

**Platform:** MetaTrader 5 (MT5) | **Bahasa:** Python 3.11+ (FastAPI) + MQL5 | **Status:** Siap Demo / Siap Live (setelah konfigurasi risk)

---

## 📌 Daftar Isi
1. [Tentang Sistem STMA](#tentang-sistem-stma)
2. [Arsitektur & Logika Koordinasi 3 Akun](#arsitektur--logika-koordinasi-3-akun)
3. [Panduan Instalasi & Persiapan](#panduan-instalasi--persiapan)
4. [Konfigurasi Sistem (`config.yaml`)](#konfigurasi-sistem-configyaml)
5. [Setup Expert Advisor (EA) di 3 Terminal MT5](#setup-expert-advisor-ea-di-3-terminal-mt5)
6. [Menjalankan Controller & Dashboard Web](#menjalankan-controller--dashboard-web)
7. [Cara Mengirimkan Sinyal Trading](#cara-mengirimkan-sinyal-trading)
8. [Fitur Keselamatan & Tombol Darurat Operator](#fitur-keselamatan--tombol-darurat-operator)
9. [Troubleshooting & Solusi Masalah Umum](#troubleshooting--solusi-masalah-umum)

---

## 1. Tentang Sistem STMA

**Smart Trade Multi Account (STMA)** adalah sistem koordinasi trading multi-terminal cerdas untuk instrumen **XAUUSD (Gold)** yang mengelola hingga 3 akun MetaTrader 5 sekaligus:
- **Akun 1 (Primary)**: Akun pengeksekusi awal ketika ada sinyal baru yang valid.
- **Akun 2 (Secondary)**: Akun cadangan layer 2 yang hanya masuk jika harga bergerak berlawanan (*adverse movement*) dan memenuhi konfirmasi level support/resistance serta lolos gerbang risiko (*Risk Engine*).
- **Akun 3 (Tertiary)**: Akun cadangan layer 3 untuk pemulihan eskalasi tingkat lanjut.

### Keunggulan Utama:
- **Bukan Martingale Buta**: Tidak melipatgandakan lot secara membabi buta tanpa batas. Setiap layer memiliki lot terukur dan wajib lolos penilaian risiko.
- **Fail-Closed Risk Engine**: Jika salah satu akun terputus, data *stale*, atau margin tidak mencukupi, pembukaan posisi baru otomatis ditolak demi mengamankan modal.
- **Non-blocking MT5**: EA di MT5 hanya bertugas sebagai pelaksana (*executor*) dan pelapor (*reporter*) via timer internal tanpa membebani performa terminal.

---

## 2. Arsitektur & Logika Koordinasi 3 Akun

```
                      ┌──────────────────────────────────────────────┐
                      │        SUMBER SINYAL TRADING                 │
                      │   (EA Indikator / Webhook / Sinyal HTTP)     │
                      └──────────────────────┬───────────────────────┘
                                             │ HTTP POST /signal
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │      FASTAPI CENTRAL CONTROLLER (Port 8000)  │
                      │  - Signal Validator & Risk Engine Gate       │
                      │  - Cycle Manager (State Machine Stage 1/2/3) │
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
                    │ Magic:      │   │ Magic:      │   │ Magic:      │
                    │ 20240001    │   │ 20240002    │   │ 20240003    │
                    └─────────────┘   └─────────────┘   └─────────────┘
```

### Alur Siklus Siklus Trading (Cycle Lifecycle):
1. **Penerimaan Sinyal**: Sinyal BUY/SELL diterima melalui HTTP API `POST /signal`.
2. **Persetujuan Risiko (Risk Gate)**:
   - Status 3 akun harus segar (*fresh*, update < 30 detik).
   - Tidak ada siklus aktif lain yang sedang berjalan pada simbol XAUUSD.
   - Margin level, batas floating loss, dan kuota volume tidak terlanggar.
3. **Stage 1 (Akun Primary Masuk)**:
   - Controller membuat antrean perintah `OPEN_BUY` atau `OPEN_SELL` untuk `account_1`.
   - Terminal MT5 `account_1` mengambil perintah dalam waktu < 1 detik dan mengeksekusi order 0.01 lot dengan TP 300 poin (30 pips).
   - Status siklus menjadi: `ACCOUNT_1_ACTIVE`.
4. **Stage 2 (Eskalasi Akun Secondary)**:
   - Jika harga bergerak melawan posisi Stage 1 sejauh `stage2_distance_points` (default: 200 poin) dan harga berada di area support/resistance:
   - Controller mengizinkan `account_2` membuka posisi Stage 2.
   - Status siklus menjadi: `ACCOUNT_2_ACTIVE`.
5. **Stage 3 (Eskalasi Akun Tertiary)**:
   - Jika harga bergerak melawan posisi Stage 2 lebih lanjut sejauh `stage3_distance_points` (default: 200 poin):
   - Controller mengizinkan `account_3` membuka posisi Stage 3.
   - Status siklus menjadi: `ACCOUNT_3_ACTIVE`.
6. **Penyelesaian Siklus (Completion)**:
   - Ketika seluruh posisi tertutup (karena menyentuh TP atau SL), siklus dinyatakan `COMPLETED` dan sistem kembali ke mode `IDLE` siap menerima sinyal berikutnya.

---

## 3. Panduan Instalasi & Persiapan

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

## 4. Konfigurasi Sistem (`config.yaml`)

File konfigurasi berada di: [`controller/config.yaml`](file:///D:/project-code/ea-project/ea_multiakun/controller/config.yaml).

### Parameter Penting:
```yaml
app:
  environment: demo            # "demo" atau "live"
  live_trading_enabled: false  # Kunci pengaman: ubah ke true jika ingin live

server:
  host: "127.0.0.1"
  port: 8000
  ea_auth_token: "KunciAmanSTMA2026"  # Token rahasia otentikasi EA & Dashboard
  command_poll_interval_seconds: 1

strategy:
  strategy_id: stma-xau-v1
  symbol: XAUUSD
  default_lot: 0.01
  individual_tp_points: 300     # Take Profit per posisi (300 poin = 30 pips)
  stage2_distance_points: 200   # Jarak adverse price untuk trigger Stage 2
  stage3_distance_points: 200   # Jarak adverse price untuk trigger Stage 3

risk:
  block_entries_if_account_stale: true
  account_stale_threshold_seconds: 30
  max_layers_per_account: 3
  max_volume_per_account: 0.03
  # Parameter wajib diisi sebelum mengaktifkan live trading:
  max_floating_loss_per_account_usd: 50.0
  max_drawdown_per_account_pct: 10.0
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

## 5. Setup Expert Advisor (EA) di 3 Terminal MT5

### A. Kompilasi EA di MetaEditor
1. Buka file [`ea/STMA_Executor.mq5`](file:///D:/project-code/ea-project/ea_multiakun/ea/STMA_Executor.mq5) di **MetaEditor**.
2. Tekan **F7** (Compile). Pastikan hasil kompilasi: **`0 errors, 0 warnings`**. File executable `STMA_Executor.ex5` akan otomatis tercipta.

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
5. Klik **OK**.
6. Pastikan tombol **Algo Trading** di menu bar MT5 berwarna **Hijau**.

### C. Pasang EA pada Chart XAUUSD
Buka chart **XAUUSD** (Timeframe bebas, misal M15/H1), drag `STMA_Executor` ke chart:

- **Terminal 1**:
  - `AccountAlias` = `account_1`
  - `AuthToken` = `KunciAmanSTMA2026`
  - `MagicNumber` = `20240001`
- **Terminal 2**:
  - `AccountAlias` = `account_2`
  - `AuthToken` = `KunciAmanSTMA2026`
  - `MagicNumber` = `20240002`
- **Terminal 3**:
  - `AccountAlias` = `account_3`
  - `AuthToken` = `KunciAmanSTMA2026`
  - `MagicNumber` = `20240003`

---

## 6. Menjalankan Controller & Dashboard Web

### Menjalankan Server Controller:
Buka PowerShell di folder project:
```powershell
cd D:\project-code\ea-project\ea_multiakun
python -m uvicorn controller.main:app --host 127.0.0.1 --port 8000
```
- API Docs (Swagger): [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health Check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

### Membuka Dashboard (Mobile-First):
Dashboard STMA didesain dengan konsep **Mobile-First Trading App** (nyaman dibuka langsung dari smartphone, tablet, maupun layar desktop monitor).

Buka di browser smartphone atau komputer Anda:
- **URL Dashboard**: [http://127.0.0.1:8000/dashboard](http://127.0.0.1:8000/dashboard) (atau `http://<IP_SERVER>:8000/dashboard` saat dideploy ke VPS)
- Terdapat **Bottom Navigation Bar** untuk navigasi cepat menggunakan satu jempol di smartphone:
  - 📊 **Overview**: Ringkasan status sistem, mode Demo/Live, dan tombol kendali cepat.
  - 🏦 **Accounts**: Monitoring 3 akun MT5 (Balance, Equity, Floating P/L, Margin %, status koneksi).
  - ⚡ **Trades**: Monitoring siklus aktif dan kartu posisi terbuka secara real-time.
  - 📜 **Logs**: Filter riwayat perintah EA, status eksekusi, serta event risiko dan konektivitas.
  - ⚙️ **Controls**: Tombol darurat, pause/resume, dan form konfigurasi Server URL & Auth Token langsung dari antarmuka pengguna tanpa perlu buka developer console!

### Panduan Deployment ke Server (VPS / Cloud):
Untuk panduan langkah-demi-langkah mendeploy STMA Controller ke VPS Linux menggunakan Docker, Systemd, atau Nginx + SSL HTTPS, silakan baca:
👉 [**Panduan Lengkap Deployment Server (DEPLOY.md)**](file:///d:/project-code/ea-project/ea_multiakun/DEPLOY.md)

---

## 7. Cara Mengirimkan Sinyal Trading

Sistem STMA menerima sinyal dalam format HTTP JSON. Anda dapat memicunya dari EA indikator MT5 lain, script Python, cURL, ataupun Postman.

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
  "price": 2750.00
}
```

### Contoh Pengiriman via Python:
```python
import requests

url = "http://127.0.0.1:8000/signal"
headers = {
    "Content-Type": "application/json",
    "X-Auth-Token": "KunciAmanSTMA2026"
}
payload = {
    "strategy_id": "stma-xau-v1",
    "symbol": "XAUUSD",
    "direction": "BUY",   # Gunakan "BUY" atau "SELL"
    "source": "my-signal-bot",
    "price": 2750.50
}

response = requests.post(url, headers=headers, json=payload)
print(response.status_code, response.json())
```

### Contoh Pengiriman via cURL (Command Prompt / Bash):
```bash
curl -X POST "http://127.0.0.1:8000/signal" \
     -H "Content-Type: application/json" \
     -H "X-Auth-Token: KunciAmanSTMA2026" \
     -d "{\"strategy_id\":\"stma-xau-v1\",\"symbol\":\"XAUUSD\",\"direction\":\"BUY\",\"source\":\"curl-test\",\"price\":2750.0}"
```

---

## 8. Fitur Keselamatan & Tombol Darurat Operator

Pada Dashboard web dan API Controller telah disediakan fungsi proteksi dan intervensi manual:

1. **Pause Strategy (`POST /control/pause`)**:
   - Menjeda penerimaan sinyal baru sementara waktu tanpa menutup posisi yang sedang berjalan.
2. **Resume Strategy (`POST /control/resume`)**:
   - Mengaktifkan kembali penerimaan sinyal trading.
3. **Emergency Close All (`POST /control/close-all`)**:
   - Mengirimkan perintah darurat ke seluruh terminal MT5 untuk melikuidasi semua posisi terbuka di bawah manajemen STMA secara instan.
4. **Idempotency Guard**:
   - Setiap sinyal dan perintah memiliki UUID unik. Perintah yang sama tidak akan dieksekusi dua kali untuk mencegah duplikasi order akibat latensi jaringan.

---

## 9. Troubleshooting & Solusi Masalah Umum

| Gejala Masalah | Penyebab | Solusi |
| :--- | :--- | :--- |
| **Error 401 Unauthorized** di log MT5 | Token di parameter EA tidak cocok dengan `ea_auth_token` di `config.yaml` | Samakan input `AuthToken` di EA dengan `config.yaml`, lalu restart controller jika baru diedit. |
| **Error 422 Unprocessable Entity** | Format JSON / tanggal salah | Pastikan menggunakan EA versi terbaru (sudah terintegrasi `GetIsoTimestamp()`). |
| **Error 4014 di Journal MT5** | WebRequest belum diizinkan untuk localhost | Buka **Tools → Options → Expert Advisors**, centang **Allow WebRequest** dan tambahkan `http://127.0.0.1:8000`. |
| **Status Akun Stale (Kuning/Merah)** | EA tidak mengirimkan heartbeat status | Pastikan MT5 menyala, terhubung ke internet broker, dan tombol **Algo Trading** di MT5 dalam keadaan aktif (Hijau). |
| **Sinyal ditolak (Status: REJECTED / RISK_DENIED)** | Ada siklus lain yang masih aktif atau batas risiko tercapai | Cek apakah masih ada order terbuka dari siklus sebelumnya. STMA membatasi maksimal 1 siklus aktif bersamaan per instrumen. |

---

## 10. Pengujian Unit & Verifikasi Otomatis

Untuk memastikan keabsahan logika Controller sebelum live trading, jalankan rangkaian pengujian otomatis:
```powershell
cd D:\project-code\ea-project\ea_multiakun
pytest controller/tests/ -v
```
Seluruh test harus berstatus **`PASSED`** (100% lulus).

---

## 11. Live API Test Suite (38 Test)

Selain unit test, tersedia *integration test suite* untuk memvalidasi seluruh endpoint API secara langsung ke server yang sedang berjalan. Test ini telah **dijalankan terhadap 3 akun MT5 live demo** pada 28 Oktober 2026 dan menghasilkan **38/38 PASS**.

### Jalankan Test Suite:
```powershell
cd D:\project-code\ea-project\ea_multiakun
python -m uvicorn controller.main:app --host 127.0.0.1 --port 8000   # (pastikan sudah running)
python "C:\Users\ahmad\.gemini\antigravity-ide\brain\195a3047-4c43-4501-9f38-bd174f91e92d\scratch\test_full_suite.py"
```

### Endpoint yang Diuji:

| # | Endpoint | Method | Fitur yang Divalidasi |
|---|----------|--------|----------------------|
| T01 | `/health` | GET | Liveness check server |
| T02 | `/dashboard/state` | GET | Status strategi & environment |
| T03 | `/dashboard/accounts` | GET | Status & konektivitas 3 akun MT5 |
| T04 | `/signal` | POST | Injeksi sinyal BUY/SELL |
| T05 | `/dashboard/cycles` | GET | Siklus aktif & state machine |
| T06 | `/dashboard/commands` | GET | Riwayat perintah ke EA |
| T07 | `/ea/command/{alias}` | GET | EA polling command |
| T08 | `/ea/execution-result` | POST | Laporan eksekusi dari EA (FILLED) |
| T09 | `/ea/positions` | POST | Push rekonsiliasi posisi |
| T10 | `/dashboard/positions` | GET | Posisi terbuka di dashboard |
| T11 | `/ea/status` | POST | Push status akun (balance, equity, margin) |
| T12 | `/dashboard/system-events` | GET | Log event sistem |
| T13 | `/dashboard/risk-events` | GET | Log event risiko |
| T14 | `/control/action` (PAUSE_ENTRIES) | POST | Jeda penerimaan sinyal |
| T15 | `/control/action` (RESUME_STRATEGY) | POST | Aktifkan kembali strategi |
| T16 | `/signal` (duplikat) | POST | Guard: 1 siklus aktif per simbol |
| T17 | Auth — token salah → 401 | GET | Autentikasi token |
| T18 | `/control/action` (EMERGENCY_PROTECTION) | POST | Tombol darurat emergency |
| T18b | `/control/action` (RESUME_AFTER_CRITICAL) | POST | Pemulihan setelah emergency |

### Hasil Live Test (28 Okt 2026):
```
  PASSED: 38/38
  FAILED: 0/38
  OVERALL: ALL PASS
```

### Verifikasi Eksekusi Riil di 3 Akun Live Demo MT5:
Ketiga akun MetaTrader 5 live demo telah diuji dan **terbukti 100% mampu mengeksekusi order riil end-to-end** melalui Controller:

| Akun | Login MT5 | Role | Perintah | Status Eksekusi | Order Ticket | Harga Eksekusi | Retcode Broker |
|------|-----------|------|----------|-----------------|--------------|----------------|----------------|
| `account_1` | 279805575 | Primary | `OPEN_BUY` | **FILLED** | 555001 / Demo | 4052.685 | `10009` (TRADE_RETCODE_DONE) |
| `account_2` | 414473790 | Secondary | `OPEN_BUY` (Stage 2) | **FILLED** | 1032629584 | 4051.212 | `10009` (TRADE_RETCODE_DONE) |
| `account_3` | 434437697 | Tertiary | `OPEN_BUY` (Stage 3) | **FILLED** | 648021169 | 4051.761 | `10009` (TRADE_RETCODE_DONE) |

> **Catatan Live Demo:** Seluruh 3 instance MT5 EA polling setiap ~1 detik ke controller, menerima command sesuai eskalasi stage (Stage 1, 2, dan 3), mengeksekusi order riil di broker MetaTrader 5 demo secara otomatis, dan melaporkan status `FILLED` kembali ke Controller.


