# Panduan Deployment STMA Controller ke Server (VPS / Cloud)

Panduan ini berisi instruksi lengkap untuk mendeploy **STMA Multi-Account Controller** dan **Mobile-First Dashboard** ke server VPS (Ubuntu 22.04 / 24.04 LTS, Debian, atau Linux lainnya).

---

## 📋 Ringkasan Persiapan

| Komponen | Keterangan |
| :--- | :--- |
| **Server OS** | Linux VPS atau Cloud PaaS (Railway / Render / VPS Ubuntu) |
| **Spesifikasi Minimal** | 1 vCPU, 1 GB RAM, 10 GB SSD |
| **Port Default** | `8000` (atau port `80`/`443` jika menggunakan reverse proxy Nginx) |
| **EA Auth Token** | `KunciAmanSTMA2026` (Dapat diubah via `controller/config.yaml` atau env `STMA_AUTH_TOKEN`) |
| **Akses Dashboard** | `http://<IP_SERVER>:8000/dashboard` (Otomatis diarahkan ke tampilan mobile-first) |

---

## ⚡ Ingin Push ke GitHub Lalu Otomatis Build (Seperti Vercel)?

> **PENTING: Jangan gunakan Vercel untuk Backend Controller ini.**  
> Vercel adalah platform *Serverless* (fungsi mati setelah beberapa detik dan hard-disk bersifat sementara). Padahal Controller STMA wajib **hidup 24/7 terus menerus** untuk memantau pergerakan harga emas, mengevaluasi siklus Stage 2/3, melayani polling EA MT5 setiap detik, dan menyimpan data database SQLite secara permanen.

Sebagai pengganti Vercel dengan **kemudahan yang sama persis (Cukup `git push` ke GitHub → Otomatis Build & Deploy)**:

### Opsi Cloud Terbaik: [Railway.app](https://railway.app/) atau [Render.com](https://render.com/)
1. **Push kode proyek Anda ke GitHub**:
   ```bash
   git add .
   git commit -m "feat: STMA Multi-Account Controller"
   git push origin main
   ```
2. **Buka [Railway.app](https://railway.app/)**:
   - Login dengan akun GitHub Anda.
   - Klik **New Project** → **Deploy from GitHub repo** → Pilih repository Anda.
   - Railway otomatis mendeteksi file [`Dockerfile`](file:///d:/project-code/ea-project/ea_multiakun/Dockerfile) dan [`railway.json`](file:///d:/project-code/ea-project/ea_multiakun/railway.json) yang telah kita siapkan.
3. **Tambahkan Persistent Volume (Agar database SQLite awet)**:
   - Di dashboard Railway, klik service Anda → tab **Volumes** → **Add Volume** → Mount Path: `/app/data`.
4. **Selesai!** Railway akan memberikan domain HTTPS gratis (contoh: `https://stma-production.up.railway.app`).
   - Setiap kali Anda `git push`, Railway akan **otomatis build dan deploy ulang** persis seperti Vercel!

---

## 🚀 Metode 1: Deploy Menggunakan Docker & Docker Compose (Sangat Direkomendasikan)

Metode ini adalah yang paling mudah, cepat, dan terisolasi.

### 1. Install Docker & Docker Compose di VPS (Jika belum ada)
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Login ulang ke session shell Anda agar permission docker aktif
```

### 2. Clone atau Upload Kode Proyek ke VPS
```bash
# Contoh upload via SCP dari laptop Anda:
# scp -r D:\project-code\ea-project\ea_multiakun user@IP_VPS:/opt/stma

# Atau buat folder di VPS:
mkdir -p /opt/stma
cd /opt/stma
```

### 3. Jalankan dengan Satu Perintah
```bash
cd /opt/stma
docker compose up -d --build
```

### 4. Periksa Status Container
```bash
docker compose ps
docker compose logs -f
```
Server langsung aktif di port `8000` dengan auto-restart jika VPS reboot!

---

## 🛠️ Metode 2: Deploy Native Python + Systemd Service

Jika Anda tidak ingin menggunakan Docker dan ingin menjalankan Python langsung di VPS:

### 1. Install Python 3.11 & Virtualenv
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv git
```

### 2. Setup Direktori & Virtual Environment
```bash
sudo mkdir -p /opt/stma
# Copy seluruh isi proyek ke /opt/stma
cd /opt/stma
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Pasang Systemd Daemon (Agar Jalan Otomatis di Background & Auto-Restart)
```bash
sudo cp deploy/stma-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable stma-controller
sudo systemctl start stma-controller
```

### 4. Cek Status Service
```bash
sudo systemctl status stma-controller
# Melihat log real-time:
sudo journalctl -u stma-controller -f
```

---

## 🔒 Konfigurasi Firewall VPS

Buka port `8000` (dan `80`/`443` jika memakai Nginx):
```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 8000/tcp
sudo ufw enable
```

---

## 🌐 Metode 3: Setup Domain & SSL Gratis (HTTPS) dengan Nginx

Sangat direkomendasikan jika ingin mengakses dashboard secara aman lewat HTTPS (`https://trading.domainanda.com`).

### 1. Install Nginx & Certbot
```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 2. Copy Konfigurasi Nginx
```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/stma
# Edit server_name sesuai domain Anda:
sudo nano /etc/nginx/sites-available/stma
# Aktifkan site:
sudo ln -s /etc/nginx/sites-available/stma /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

### 3. Generate Sertifikat SSL Gratis (HTTPS)
```bash
sudo certbot --nginx -d trading.domainanda.com
```
Certbot akan otomatis memperbarui konfigurasi SSL dan auto-renewal setiap 3 bulan!

---

## 📱 Cara Mengakses Mobile Dashboard dari Smartphone

1. Buka browser di smartphone Anda (Google Chrome, Safari, dsb).
2. Kunjungi:
   - Jika tanpa domain: `http://<IP_VPS_ANDA>:8000/dashboard`
   - Jika memakai domain/SSL: `https://trading.domainanda.com/`
3. **PWA Tip (Tambahkan ke Home Screen):**
   - Di Android Chrome: Klik menu titik tiga `⋮` → **Add to Home screen** (Tambahkan ke Layar Utama).
   - Di iPhone Safari: Klik ikon **Share** → **Add to Home Screen**.
   - Dashboard akan terbuka seperti aplikasi native trading tanpa address bar browser!

---

## 💻 Cara Menghubungkan EA MetaTrader 5 ke Server VPS

Setelah server aktif di VPS, hubungkan ketiga EA MT5 Anda:

### 1. Allow WebRequest di MetaTrader 5:
Buka masing-masing terminal MT5 (account_1, account_2, account_3):
- Menu **Tools** → **Options** → tab **Expert Advisors**.
- Centang: `Allow WebRequest for listed URL`.
- Tambahkan URL VPS Anda:
  - Contoh: `http://<IP_VPS_ANDA>:8000` atau `https://trading.domainanda.com`
- Klik **OK**.

### 2. Konfigurasi Input Parameter EA di Chart MT5:
| Parameter | Nilai untuk Account 1 | Nilai untuk Account 2 | Nilai untuk Account 3 |
| :--- | :--- | :--- | :--- |
| `AccountAlias` | `account_1` | `account_2` | `account_3` |
| `ControllerUrl` | `http://<IP_VPS>:8000` | `http://<IP_VPS>:8000` | `http://<IP_VPS>:8000` |
| `AuthToken` | `KunciAmanSTMA2026` | `KunciAmanSTMA2026` | `KunciAmanSTMA2026` |

Setelah EA dipasang:
Status koneksi ketiga akun akan langsung muncul dan berwarna hijau di **Mobile Dashboard** Anda!
