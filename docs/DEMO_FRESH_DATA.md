# Demo upload manual — data USD, 15 Oktober 2026

Panduan lengkap arsitektur, provenance, reproduksi dan hasil kualitas ada di
**[ML_DATA_ENGINE.md](ML_DATA_ENGINE.md)**. Gunakan aplikasi sebenarnya di
http://localhost:5173, halaman **Overview**. Angka demo 7 Oktober disimpan
sebagai arsip di bawah dan bukan keadaan/data/model saat ini.

## Kondisi saat disiapkan

Aplikasi disiapkan untuk upload manual dengan **800 rekening-proxy observasi
serta 0 transaksi**. Proxy teknis ini tidak membuktikan identitas nasabah;
AccountID sumber tidak disamakan dengan hasil entity resolution. Pengguna,
audit, konfigurasi rule dan riwayat model dipertahankan sesuai rencana reset.
Periksa Overview sebelum mulai: angka kondisi awal berubah setelah upload.

Sumber publik baru berisi **50.000 observasi tanpa label fraud terverifikasi**;
mata uang **USD** dikonfirmasi Jevon pada 14 Oktober. Simulasi baru dibuat
terpisah: **1.000.000 transaksi training**, **200.000 validation**, dan
**200.000 final test setelah model dibekukan**. Fit memakai observasi training
sumber dan simulasi tanpa target. Label stress sintetis hanya untuk evaluasi,
bukan jawaban fraud nyata dan tidak masuk file upload.

Model baru `aml_if_v1` dan `fraud_if_v1` **tetap DRAFT** karena precision stress
sintetis belum memadai; model tersebut belum dipromosikan untuk scoring.
Alur model supervised lama dihentikan dan kedua versi lama sudah
RETIRED melalui transisi yang diaudit. Periksa status aktual di **Model signals**.
DRAFT tidak dijalankan oleh deteksi. Skor anomali bukan probabilitas fraud;
penjelasan model baru memakai selisih skor penggantian median, bukan TreeSHAP.

Rule P02 tetap ACTIVE dengan threshold **IDR** semula. **USD tidak dikonversi
ke IDR**, sehingga file USD ini berada di luar cakupan rule P02. `ML_SCORE`
tetap DRAFT menunggu CF-05; tidak ada aktivasi rule untuk membuat alert model.

## Akun dan kewenangan

Akun yang dipertahankan: `dataops@fcip.internal`, `analyst@fcip.internal`,
`triage@fcip.internal`, dan `investigator@fcip.internal`. Password demo
sebelumnya adalah `DevPassword123!`; seed tidak mengganti password akun yang
sudah ada. Untuk membuat login development yang belum ada tanpa menambahkan
kembali customer/account dummy lama:

```bash
docker compose exec backend python -m app.scripts.seed --users-only
```

| Akun | Kewenangan |
|---|---|
| Data Ops | Upload CSV dan menjalankan deteksi manual |
| Analyst | Membaca hasil/sinyal dan menjalankan deteksi |
| Triage | Memutuskan alert OPEN yang tersedia: false positive atau escalate |
| Investigator | Membuka case dari alert escalated yang belum memiliki case |

`ROLE_ADMIN` belum tersedia; admin bukan pengganti Triage (FRD §4.1.1).

## File yang diunggah sekarang

Gunakan hanya:
`backend/ml_data/behavioral_s20261015/test_web/transactions_001.csv`.

| File | Expected records | Ukuran byte |
|---|---:|---:|
| `transactions_001.csv` | 200000 | 38.293.500 |

Satu file ini berada di bawah batas API **52.428.800 byte (50 MiB)**.
`upload_manifest.json` pada folder yang sama mencatat jumlah dan SHA-256;
manifest adalah bahan pemeriksaan, bukan file upload. CSV membawa observasi
canonical, termasuk lokasi, IP, login, durasi, saldo, demografi yang memang
tercatat, presisi timestamp dan penanda zona waktu sumber. Nilai yang tidak
diketahui tetap null. File `ground_truth.csv`, `lineage.csv`, source profile,
dan laporan evaluasi tidak diunggah.

## Langkah upload manual

1. Login sebagai **Data Ops** dan buka **Overview**.
2. Pilih file `transactions_001.csv` dari folder `test_web` di atas.
3. Isi Source system `BEHAVIORAL_SIMULATION`, Business date `2026-10-15`,
   Expected records `200000`, lalu tekan **Upload CSV** satu kali.
4. Tunggu hasil batch. Catat total read, accepted, quarantine dan status aktual.
   Pengujian terisolasi file ini menerima 200.000 baris dan 0 quarantine;
   angka tersebut bukan klaim bahwa file sudah diunggah ke aplikasi live.
5. Buka daftar transaksi dan audit batch untuk memeriksa isi serta provenance.
   Upload tidak melakukan training atau mengaktifkan model.
6. **Jalankan deteksi** tetap merupakan aksi manual terpisah. Selama model
   baru DRAFT dan model lama RETIRED, tidak ada model ACTIVE untuk scoring.
   P02 mengabaikan USD; jangan mengharapkan model flags/alert dari file ini.
7. Buka **Model signals** untuk melihat versi, status DRAFT, threshold dan
   batas evaluasinya. Kosongnya flags dalam keadaan ini bukan bukti transaksi
   bebas fraud. Alur triage/case memerlukan alert yang benar-benar tersedia.

Upload ulang file identik untuk source system dan business date yang sama
mendapat 409. Pengiriman ulang dengan metadata batch berbeda tetap mengirim
transaction_ref yang sudah ada ke quarantine; data lama tidak ditimpa.
Sistem tidak memblokir, menahan atau membalikkan transaksi (NG-01).

## Batas kualitas dan interpretasi

Precision/recall fraud sumber publik tidak dapat diukur karena tidak ada
label terverifikasi. Angka stress sintetis mengukur asumsi skenario simulator.
Evaluasi model baru menunjukkan beban flag dan pemisahan stress yang belum
memadai, termasuk pergeseran antara sumber historis yang jarang/day-only dan
simulasi yang lebih padat. Karena itu status model tetap DRAFT. Hasil tinggi
model lama di bawah tidak dipakai untuk menyatakan kualitas engine baru.
Sinyal selalu merupakan observasi pendukung investigasi, bukan bukti salah
(FR-406); keputusan manusia dan CF-05 tetap berlaku.

## Arsip demo 7 Oktober 2026 — bukan petunjuk setup saat ini

Dataset Full seed `20261007`, dua bagian upload lama, dan model supervised
`aml_gbm_v1`/`fraud_gbm_v1` berasal dari generator 1.4.0 lama. Direktori dataset
serta artefak lokal lama telah dikeluarkan dari alur aktif pada penggantian
15 Oktober. Instruksi regenerasi, registrasi ACTIVE dan pergantian database
lama tidak berlaku untuk demo USD ini. Tabel berikut dipertahankan sebagai
bukti pengujian historis, bukan angka kondisi live atau kualitas model baru.

### Hasil pembanding historis yang diuji pada 7 Oktober

Angka berikut berasal dari pengujian endpoint pada **database verifikasi
terpisah**, bukan isi database demo saat ini. Pada pengujian historis kedua bagian diunggah
berurutan ke keadaan awal terisolasi sebelum run deteksi pertama; hasilnya:

| File | Total | Accepted | Quarantine | Status batch |
|---|---:|---:|---:|---|
| `transactions_001.csv` | 313.720 | 306.718 | 7.002 | COMPLETED |
| `transactions_002.csv` | 108.026 | 105.646 | 2.380 | COMPLETED |
| **Total** | **421.746** | **412.364** | **9.382** | |

Setelah semua bagian masuk, run pertama menghasilkan **58 alert P02**,
**1.933 unit entity-week AML** di atas threshold dari 147.931 unit dinilai,
dan **4.346 transaksi Fraud** di atas threshold dari 412.364 transaksi
dinilai. Tidak ada alert model karena `ML_SCORE` DRAFT.

Data transaksi dan atribut master yang dibangun aplikasi sama persis dengan
loader offline. Uji role juga lulus: Data Ops ditolak ketika meminta disposisi
(403); Triage dapat false positive dan escalate dengan alasan yang memenuhi
syarat. Hasil ini memverifikasi alur teknis, bukan recall terhadap label atau
performa pada data nyata.

Evaluasi kualitas terpisah terhadap label offline pada seed baru juga selesai:
recall skenario AML **517/517 (100%)**, Fraud **153/155 (98,71%)**, tanpa
training ulang atau mengubah threshold. Masih ada kesalahan pada look-alike
dan dua skenario P05. Angka ini berbeda dari recall per transaksi/unit dan
tidak membuktikan performa data nyata. Lihat
[laporan evaluasi fresh](ml/evaluation_manual_s20261007.md) untuk metrik,
provenance, dan keterbatasan. Labels tidak dipakai oleh website/inference.

### Catatan sesi UI, 7 Oktober

Pada pemeriksaan sesi UI, Jevon sudah mengunggah bagian pertama sebagai
`BAT-000001` (306.718 diterima / 7.002 quarantine) dan menjalankan deteksi
parsial (37 alert). Run parsial tidak dapat dianggap hasil akhir Full:
skor yang sudah tersimpan tidak diganti ketika bagian kedua ditambahkan.
Database dan hasil manual tersebut dipertahankan selama makeover.

Pada 15:15:37 WIB, intersepsi tes UI gagal dan CSV dummy
`qa-transactions.csv` masuk sebagai **BAT-000002**: **0 transaksi diterima,
3 baris quarantine**. Pada saat itu batch serta auditnya dipertahankan; jumlah quarantine
demo bertambah 3 dari artefak tes ini. Audit historis dipertahankan ketika
reset operasional kemudian dilakukan. Correlation ID:
`ce24ef64-401d-4dc7-8c47-33ae81a4a601`. Seluruh tes operasi setelah kejadian
ini dijalankan pada frontend, backend dan database QA terpisah. Catatan ini
menjelaskan selisih artefak QA ketika membandingkan dengan angka dataset.
