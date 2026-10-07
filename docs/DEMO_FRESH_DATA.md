# Demo upload manual — dataset baru, 7 Oktober 2026

Panduan ini memakai aplikasi berjalan di http://localhost:5173. Mockup untuk
mentor adalah [file preview terpisah](mockups/fcip-upload-results-preview.html);
upload sebenarnya dilakukan di halaman **Overview** aplikasi.

## Kondisi awal

Database demo `fcip_demo_s20261007` disiapkan terpisah dari database lama `fcip`.
Sebelum upload, transaksi, quarantine, batch, alert, case, dan skor model
berjumlah **0**. Master data sengaja sudah tersedia: **11.200 customer,
13.455 account, 1.500 merchant, dan 11.932 device**. Empat akun lama disalin
dengan UUID dan password hash yang sama. Data asli tidak dihapus.

Model `aml_gbm_v1` dan `fraud_gbm_v1` memakai artefak training yang sudah ada,
ACTIVE dengan bypass maker-checker yang telah didokumentasikan. Threshold
tetap; tidak ada training ulang. Rule P02 ACTIVE; rule `ML_SCORE` tetap DRAFT
sambil menunggu keputusan CF-05.

Dataset baru dibuat 7 Oktober, setelah training model: generator **1.4.0**,
profil **Full**, seed **20261007**, reference date **2026-09-30** yang sama
dengan preset training/test. Dataset berisi **421.746 transaksi mentah**;
hasil penerimaan/quarantine pembanding telah diuji pada database terpisah
seperti tabel di bawah, sementara database demo menunggu upload sendiri.
Semua data sintetis; hasilnya bukan estimasi performa pada transaksi nyata.

## Akun dan kewenangan

Password keempat akun demo sudah diverifikasi: `DevPassword123!`.

| Akun | Yang dapat dilakukan |
|---|---|
| `dataops@fcip.internal` | Upload CSV dan menjalankan deteksi |
| `analyst@fcip.internal` | Membaca hasil/sinyal dan menjalankan deteksi |
| `triage@fcip.internal` | Menilai alert: false positive atau escalate |
| `investigator@fcip.internal` | Membuka case dari alert yang telah escalated dan belum punya case |

`ROLE_ADMIN` belum diimplementasikan. Triage menangani keputusan alert;
admin bukan pengganti Triage (FRD §4.1.1). Upload dibatasi pada Data Ops
(FRD §4.1.2; perilaku endpoint yang sudah ada).

## File yang diunggah

Folder siap upload:
`backend/sample_data/raw/manual_full_s20261007_web/`.

| Urutan | File | Expected records | Ukuran byte |
|---|---|---:|---:|
| 1 | `transactions_001.csv` | 313720 | 52.428.695 |
| 2 | `transactions_002.csv` | 108026 | 18.055.978 |

Kedua file berjumlah 421.746 baris, masing-masing di bawah batas API
**52.428.800 byte (50 MiB)**. `upload_manifest.json` mencatat jumlah, SHA-256,
dan verifikasi bahwa penggabungan kedua bagian menghasilkan proyeksi asli
dengan urutan baris yang sama. File ini untuk diperiksa, bukan diunggah.

CSV memakai proyeksi bridge yang sudah ada, termasuk kolom device, merchant,
jenis transaksi, negara counterparty, dan source status. Data ground truth
tidak masuk file upload. Endpoint saat ini menerima **CSV transaksi saja**;
ZIP paket, `labels.csv`, dan `label_transactions.csv` tidak diunggah.

## Upload sampai hasil

1. Login sebagai **Data Ops**, lalu buka **Overview**.
2. Pilih `transactions_001.csv`. Isi Source system `NDP_WALLET_CORE`,
   Business date `2026-09-30`, Expected records `313720`, lalu **Upload**.
   Tunggu hasil batch dan catat accepted/quarantine yang ditampilkan.
3. Pilih `transactions_002.csv`, dengan Source system dan Business date yang
   sama, Expected records `108026`, lalu **Upload**. Tunggu batch selesai.
4. **Upload kedua bagian sebelum menjalankan deteksi.** Skor yang tersimpan
   bersifat immutable; run dengan riwayat parsial bukan hasil akhir dataset.
5. Tekan **Jalankan deteksi** satu kali. Tombol masih berlabel P02, tetapi
   endpoint menjalankan rule P02 serta kedua model ACTIVE. Tunggu selesai.
6. Buka **Alert queue** untuk alert P02; buka detailnya untuk faktor rule dan
   **Model signals**. Buka **Model flags** untuk unit di atas threshold:
   AML per entity-week dan Fraud per transaksi. Jumlah kedua jenis unit tidak
   dijumlahkan sebagai jumlah transaksi unik.
7. Logout, login sebagai **Triage**, lalu buka sebuah alert OPEN. Pilih
   false positive atau escalate dengan alasan minimal **20 karakter**.
   Dari alert escalated, Triage atau Investigator dapat membuka case.
8. Buka correlation ID pada alert/case untuk memeriksa rantai audit.

**Batas keputusan manusia:** model flags saat ini dapat dibaca, tetapi belum
punya disposisi sendiri. Dengan `ML_SCORE` DRAFT, skor model tidak membuat
alert tersendiri (CF-05); form triage hanya tersedia pada rule alert.
Sinyal merupakan observasi pendukung investigasi, bukan bukti perbuatan salah
(FR-406). Sistem tidak memblokir atau membalikkan transaksi.

## Hasil pembanding yang sudah diuji

Angka berikut berasal dari pengujian endpoint pada **database verifikasi
terpisah**, bukan isi database demo saat ini. Database demo tetap kosong
untuk upload Jevon. Jika kedua bagian diunggah berurutan ke keadaan awal yang
disiapkan, hasil pembandingnya:

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

## Reproduksi melalui CLI

Perintah dari root repository, setelah backend menunjuk database demo.
Gunakan folder baru agar hasil siap upload yang sudah ada tetap utuh:

```bash
docker compose exec backend python -m app.scripts.generate_raw_dataset \
  --profile full --seed 20261007 --reference-date 2026-09-30 \
  --out ml_data/manual_full_s20261007_reproduced
docker compose exec backend python -m app.scripts.load_raw_dataset \
  --raw-dir ml_data/manual_full_s20261007_reproduced --master-data-only
docker compose exec backend python -m app.scripts.prepare_upload_csv \
  --raw-dir ml_data/manual_full_s20261007_reproduced \
  --out-dir sample_data/raw/manual_full_s20261007_reproduced_web
```

Master-data-only tidak memasukkan transaksi. Adapter memverifikasi manifest,
menggunakan proyeksi bridge, lalu memecah CSV tanpa mengubah urutan/nilai.
Kedua model sudah terdaftar di database demo; tidak perlu registrasi atau
training ulang. Upload tetap dilakukan sendiri lewat website.

## Kembali ke database lama

Backup privat tersimpan di
`backend/local_backups/before_demo_s20261007_20261007_120834/`:
`fcip_before_demo.dump` dan `env.before`. Folder ini diabaikan Git dan tidak
dibagikan karena berisi konfigurasi/kredensial atau password hash.

Untuk kembali ke DB lama yang tetap utuh, dari root repository:

```bash
cp backend/local_backups/before_demo_s20261007_20261007_120834/env.before .env
docker compose up -d --no-deps --force-recreate backend
```

Login ulang setelah berpindah database. Tidak perlu restore dump ke database
asli; jangan memakai `docker compose down -v` atau menimpa database `fcip`.
