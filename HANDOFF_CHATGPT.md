# Handoff: Financial Crime Intelligence Platform (FCIP)

Kamu akan melanjutkan pengembangan sebuah proyek capstone. Baca seluruh prompt ini sebelum mengerjakan apa pun. Kalau kamu tidak bisa membaca file di repository secara langsung, minta Jevon menempelkan file yang kamu butuhkan, terutama `docs/PROJECT_CONTEXT.md`, `docs/PROGRESS.md` dan `README.md`. Jangan menebak isi file.

---

## 1. Siapa dan apa

- **Pengguna:** Jevon (Jevon Christian Putra Funay), mahasiswa UKDW. Proyek capstone UKDW × V-TEKI, 16 minggu. Saat handoff (7 Oktober 2026) kita di **minggu 5**. Jevon membangunnya sendirian, walaupun spesifikasinya ditulis untuk dua kelompok (Group A dan Group B).
- **Bahasa:** Jevon menulis dalam bahasa Indonesia santai. Balas dalam bahasa Indonesia. Kode, komentar, nama commit dan dokumen di repo ditulis dalam bahasa Inggris.
- **Produk:** Financial Crime Intelligence Platform untuk organisasi fiktif *PT Nusantara Digital Payment* (dompet digital). Platform memantau transaksi, mendeteksi pola kejahatan keuangan (AML dan Fraud), lalu menyerahkan alert ke manusia untuk ditriase dan diinvestigasi. **Prinsip mutlak: platform hanya memberi saran, tidak pernah bertindak, memutuskan atau mengirim** (tidak memblokir transaksi, tidak mengirim laporan ke regulator).
- **Semua data sintetis.** Tidak ada data nyata. Setiap angka metrik wajib membawa peringatan data sintetis (TRD §10.6, FRD L-01).

## 2. Spesifikasi (sumber kebenaran)

- `docs/FRD_ID_Financial Crime Intelligence Platform.docx.pdf`: kebutuhan fungsional, 167 halaman, bahasa Indonesia.
- `docs/TRD_ID_Financial Crime Intelligence Platform.docx.pdf`: kebutuhan teknis, 116 halaman.
- **Denominator progres:** 120 FR, 18 NFR, 12 pattern deteksi (P01–P12), sekitar 90 tabel, 80 endpoint, 9 role RBAC, 30 komponen TRD.
- **Aturan membaca spec:** sebelum menyatakan "spec tidak mengatur X", cari juga istilah bahasa Indonesianya (contoh: "severity" di FRD ditulis "tingkat keparahan"). Setiap angka yang ditebak, bukan diambil dari FRD/TRD, harus ditandai sebagai asumsi.

## 3. Aturan kerja (wajib, disetel Jevon)

**Boleh diputuskan sendiri, lalu dicatat:**
- detail implementasi, desain tes, refactor, perbaikan bug di dalam lingkup tahap yang sedang dikerjakan
- pilihan yang jelas mengikuti FRD/TRD (sebutkan section-nya)
- commit lokal setiap tahap selesai dan semua tes hijau
- asumsi kecil yang mudah dibalik: jalankan, lalu catat sebagai `AS-xx` di `docs/PROJECT_CONTEXT.md` §8

**WAJIB berhenti dan tanya Jevon dulu:**
- perubahan generator data atau data bersama (`backend/sample_data/Tiny`, `Small`). Generator 1.4.0 adalah perubahan terakhir sebelum training
- apa pun yang bertentangan dengan FRD/TRD atau arahan mentor, atau yang masih menunggu mentor (CF-05 dan open item lain)
- asumsi baru yang tidak ada di spec DAN berpengaruh ke hasil model atau ke tim
- mengubah lingkup atau urutan tahap
- apa pun yang tidak bisa dibalik: hapus data, ubah riwayat git, force push, melonggarkan keamanan
- `git push`: selalu Jevon yang menjalankan, tidak pernah kamu

**Cara melapor:**
- jangan laporan panjang per langkah; catat progres di `docs/PROGRESS.md`
- di akhir setiap tahap: ringkasan maksimal 10 baris, daftar "keputusan yang saya ambil sendiri" untuk dicek Jevon, dan **ringkasan capstone dalam bahasa Indonesia** (siap tempel ke laporan)
- jujur soal keterbatasan dan hasil yang janggal; jangan menyatakan sesuatu sudah diuji kalau belum

**Aturan tim lain:**
- kalau generator berubah, Tiny/Small di-generate ulang di commit yang sama (ada tes byte-for-byte)
- `labels.csv` dan `label_transactions.csv` hanya untuk evaluasi, tidak pernah jadi input fitur
- dataset Full dan dataset ML tidak masuk git (`backend/ml_data/`, `backend/ml_models/` di-gitignore); dibuat ulang dari seed
- kosakata UI untuk sinyal harus deskriptif, tidak menuduh (BR-406.2): jangan "mencurigakan", "penipuan", "pencucian uang", "suspicious", "fake", "fraud" sebagai kesimpulan. Contoh benar: "6 akun berbagi device ini", "skor model 0,92 (threshold 0,18)"
- setiap tampilan sinyal model membawa keterangan tetap FR-406: sinyal adalah observasi yang mendukung investigasi, bukan bukti perbuatan salah

## 4. Stack dan cara menjalankan

- **Backend:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, pytest. ML: numpy, pandas, pyarrow, LightGBM 4.5.0, scikit-learn 1.5.2 (`libgomp1` ada di Dockerfile).
- **Frontend:** React + TypeScript + Vite, TanStack Query, axios.
- **Docker Compose:** `db`, `backend` (menjalankan `alembic upgrade head` saat start), `frontend` (port 5173). Backend di port 8000.
- **Tes:** `docker compose exec backend pytest` (atau `.venv/bin/python -m pytest` di `backend/`). Tes memakai database `<db>_test` yang dibangun ulang dari migrasi setiap sesi. Status terakhir: **406 lulus** di host, 405 + 1 skip di Docker, 5 tes `slow` lulus (`pytest -m slow`). Frontend: `npm run build` (typecheck + build).
- **User dev:** `python -m app.scripts.seed` membuat `dataops@`, `analyst@`, `triage@`, `investigator@fcip.internal`, password `DevPassword123!`.

## 5. Yang sudah dibangun di aplikasi (sekitar 17% lingkup MVP)

- Auth (argon2id, JWT, refresh cookie rotating, idle/absolute timeout, lockout) dan RBAC 4 dari 9 role (DATA_OPS, ANALYST, TRIAGE, INVESTIGATOR)
- Ingestion CSV transaksi: registrasi batch FR-101, validasi FR-102, quarantine, log proses + rekonsiliasi FR-105. Batas upload 50 MB
- Deteksi **P02 Structuring** sebagai rule-as-data: rule `RUL-0001` v1 ACTIVE (seed migrasi 0006, bypass maker-checker terdokumentasi). Tabel `rule`/`rule_version`/`simulation_result` dengan immutability di database (trigger). Endpoint rule CRUD dan simulasi (FR-301–303 tahap 3–4) **ditunda** demi ML
- Antrean alert, detail alert dengan faktor deteksi, disposisi (false positive / escalate, alasan minimal 20 karakter), case dari alert, audit trail + ekspor CSV
- Frontend: Login, Overview (tile, tabel transaksi, quarantine, upload, tombol "Jalankan deteksi"), Alert queue, Alert detail, Case detail, Audit, **Model flags** (baru)
- Hitungan: sekitar 16/120 FR, 19/~90 tabel, 20/80 endpoint

## 6. Data sintetis

- Generator: `backend/app/scripts/generate_raw_dataset.py`, **versi 1.4.0**, deterministik per seed. Menghasilkan kontrak file TRD §6.1: customers, business_customers, beneficial_owners, accounts, merchants, devices, transactions, watchlist, manifest (SHA-256, `synthetic_declaration: true`), plus `labels.csv` dan `label_transactions.csv` (kunci jawaban, evaluasi saja).
- Profil: tiny (1%), small (5%, sekitar 21 ribu transaksi), full (sekitar 421 ribu, target TRD §11.1 420.000). Tiny/Small ada di git (`backend/sample_data/Tiny`, `Small`).
- Isi skenario: 12 pattern FRD (P01–P12) + **ATO** (account takeover, ekstensi proyek) dengan positif, look-alike (perilaku sah yang mirip pattern, dibuat dari daftar false positive FRD §8), boundary case (tepat di ambang pattern), cohort kontrol (tidak boleh memicu alert), dan populasi entity resolution.
- 1.4.0 menyesuaikan semua pernyataan perilaku TRD §11.2 (aktivitas heavy-tailed, settlement mingguan merchant, tiket sesuai MCC, dll.). Ada audit artefak otomatis (`audit_scenario_artefacts.py`) yang memastikan skenario tidak meninggalkan jejak resep: 0 temuan di 5 seed.
- Dataset ML: **training** = Full seed `20260923`, **test** = Full seed `20261001` (perintah di README, "Datasets for the ML pipeline").

## 7. Pipeline ML (arahan mentor: dataset enterprise, training, uji di data baru, lalu ke live monitoring; harus mencakup Fraud DAN AML)

**Konflik dengan TRD (sudah diputuskan tim, menunggu ratifikasi mentor):** TRD merancang model anomali *tanpa label* (FR-401, baseline 90 hari per entity). Mentor meminta model yang dilatih. Keputusan: model **supervised**, deviasi baseline FR-401 dimasukkan sebagai fitur, dan skor FR-401 tanpa label dilaporkan sebagai pembanding.

**Pembagian domain (untuk laporan):** AML = P01, P02, P06, P07, P08, P12. Fraud = P04, P10, ATO. Irisan = P03, P05, P09, P11.

**Dua model (kepemilikan berdasarkan kecepatan perilaku):**
- **Model AML:** satu baris per (entity, minggu kalender). Memegang P01, P02, P03, P06, P07, P08, P09, P10, P11.
- **Model Fraud:** satu baris per transaksi, dinilai saat transaksi itu. Memegang ATO, P04, P05.
- P12 (screening watchlist) di luar kedua model.

**Tahapan (semua selesai):**
1. **1b, library fitur** (`backend/app/ml/`): `feature_set.py` (registry, parameter terkunci dengan sumbernya, dirender ke `docs/ml/feature_set_fs_v2.md`), `loader.py` (allow-list file/kolom, validasi sama dengan ingestion), `features.py` (point-in-time, bisa dipanggil untuk satu entity), `build_features.py`. 57 fitur AML, 33 fitur Fraud. Fitur yang butuh riwayat bernilai NaN di bawah ambang FRD.
2. **fs_v2:** fitur tiket P10 dibandingkan dengan median tiket per MCC dari data training (`app/reference/mcc_ticket_ref_v1.json`, `app/ml/reference.py`). Asumsi AS-04.
3. **Tahap 2, label + training** (`labels.py`, `models.py`, `comparators.py`, `train.py`): positif = baris skenario milik entity berlabel di dalam window label. Look-alike/boundary = negatif. Peserta P11 tanpa label, entity P12, pattern milik model lain, dan 30 hari setelah skenario dikecualikan. Split validasi 20% per skenario. Threshold = F1 maksimum tanpa mem-flag entity kontrol. Bobot positif seimbang per pattern. Canary (label diacak 10×). Model card dengan envelope TRD §10.5. Hasil validasi: AP 0,998 (AML), 0,997 (Fraud).
4. **Tahap 3, evaluasi di seed test** (`evaluate.py`, laporan `docs/ml/evaluation_v1.md`): recall LightGBM 100% (AML, 529 label) dan 99% (Fraud, 155 label). **Rule FRD juga mencapai recall yang sama** karena skenario dibangun sesuai definisi FRD; keunggulan model ada di **presisi** (7 vs 231 look-alike AML ter-flag; beban background 0,2 vs 28,8 per minggu). Uji ketahanan: pattern yang dikeluarkan dari training dengan bentuk khas tidak tertangkap (P02 11%, P06 0%, P10 0%, P04 13%, P05 0%), artinya model belajar tipologi yang diajarkan, bukan "kecurigaan" umum. Model Fraud mem-flag 1 dari 830 entity kontrol di data test (gagal T-DET-CONTROL-01; threshold sengaja tidak diubah supaya tidak tuning di data test). Skor anomali FR-401 tanpa label hampir tidak mendeteksi (AP 0,04).
5. **Tahap 4, integrasi ke aplikasi** (commit `e1de56e`, belum di-push saat handoff):
   - migrasi 0007: kolom transaksi baru (`transaction_type`, `counterparty_country`, `merchant_ref`, `device_ref`, `source_status`), tabel `merchant` dan `device`
   - migrasi 0008: `model_version` (envelope TRD §10.5, immutable, model disimpan sebagai teks), `ml_score` (insert-only), rule `ML_SCORE` = `RUL-0002` v1 **DRAFT**
   - `app/services/ml_scoring.py`: membangun ulang Dataset dari database dan memanggil kode training yang sama, sehingga skor di aplikasi identik dengan offline (terbukti: unit yang ditandai sama persis di data Full)
   - `app/scripts/register_models.py` (registrasi ACTIVE dengan bypass maker-checker terdokumentasi), `app/scripts/load_raw_dataset.py --ingest` (untuk file di atas 50 MB)
   - API: `POST /detection/run` (semua rule lalu semua model), `GET /ml/models`, `GET /ml/scores`; alert punya `model_score` dan `model_signals`
   - UI: kolom Model score di antrean, bagian Model signals di detail alert, halaman Model flags. **UI belum pernah dicek langsung di browser**, baru lolos build
   - Uji Full: 412.160 transaksi diterima, satu run deteksi 44 detik dan sekitar 2,4 GB RAM, 65 alert P02

**Cara menjalankan pipeline (dari `backend/`):** generate dataset → `python -m app.ml.build_features <dataset>` → `python -m app.ml.train ml_data/train_full_s20260923` → `python -m app.ml.evaluate ml_data/test_full_s20261001 --train ml_data/train_full_s20260923`. Untuk aplikasi: `load_raw_dataset --raw-dir ml_data/test_full_s20261001 --ingest` → `register_models` → tombol "Jalankan deteksi". Detail di README bagian "The models inside the app (stage 4)". **Jangan** menjalankan `python -m app.ml.reference` kecuali data training berubah (menimpa file referensi yang di-commit).

## 8. Keputusan dan asumsi yang sudah tercatat

- **CF-05** (skor anomali boleh membuat alert sendiri?): arah mentor opsi (b), menunggu konfirmasi tertulis. Dibangun: (a) skor sebagai sinyal pendukung pada alert (aktif); (b) rule `ML_SCORE` DRAFT. Mengaktifkannya = ubah status, bukan ubah kode. Reason code `RC-ML-01` dan severity MEDIUM adalah usulan kita, menunggu ratifikasi
- **AS-02:** floor standar deviasi z-score FR-401 (1 transaksi; 10% rata-rata)
- **AS-03:** threshold LOW_CONFIDENCE_BASELINE tidak dikarang (open)
- **AS-04:** referensi tiket per MCC = median data training
- **AS-05:** bobot komposit skor anomali TRD §10.2 (bobot sama, |z| dipotong di 5), hanya untuk pembanding
- Bypass maker-checker terdokumentasi: `RUL-0001` dan registrasi model (FR-304/FR-405 dan ROLE_MLRO belum ada)
- Kode label ATO belum diratifikasi
- `geo_list_v1` = daftar geografi berisiko milik organisasi (konfigurasi)
- Upload: opsi C (paket untuk muatan awal, CSV transaksi harian; Full lewat script untuk demo)
- Notifikasi alert baru lewat auto-assign FR-603

## 9. Open item yang belum terjawab

- MQ-01 sampai MQ-10 (FRD §1.7) belum dijawab mentor; MQ-02 paling mendesak (P02 sudah diimplementasikan dengan default-nya)
- CF-01 (penutupan otomatis vs suppression duplikat), CF-05 (konfirmasi tertulis)
- Pembagian Group A/B tidak pernah terjadi
- Inkonsistensi spec: TRD §11.1 "~600 duplikat ER" vs tabel §11.5 = 820 (generator ikut §11.5)
- UAT-12: satu correlation ID dari batch sampai laporan belum terdefinisi dengan baik
- Untuk tahap 4 ke depan: kebijakan threshold setelah kegagalan kontrol di model Fraud; status sender P11 (389 dari 390 ter-flag oleh model AML)

## 10. Kemungkinan pekerjaan berikutnya (tanyakan Jevon mana yang dipilih)

1. **Paket UI "upload sampai hasil"** (sedang dibahas, belum disetujui): upload paket dataset dari web (file label ditolak), deteksi otomatis dengan progres, dashboard bergaya executive (tile, grafik volume, alert per pattern, banner data sintetis), antrean alert bergaya tabel risiko (pattern rule, skor model, indikator teratas dalam kata deskriptif, filter domain/pattern, tombol investigasi), sidebar per fungsi. Keputusan yang dibutuhkan: batas ukuran file / zip / script untuk Full; upload langsung memicu deteksi atau tetap tombol (belum ada background worker); dikerjakan sebelum atau sesudah keputusan CF-05. Jevon juga menimbang membuat mockup dulu untuk ditunjukkan ke mentor
2. Rule as data tahap 3–4 (endpoint rule FR-301/302, simulasi FR-303)
3. Memindahkan rule P01, P03–P11 ke aplikasi (logika versi offline ada di `app/ml/comparators.py`)
4. Lifecycle case (FR-804), antrean case (FR-808), penugasan alert (FR-603), SLA (FR-607)
5. Metrik kualitas data (FR-108)
6. Alur persetujuan model FR-405, lalu mode live monitoring (simulasi, wajib berlabel simulasi)

## 11. Langkah pertama untukmu

1. Baca `docs/PROJECT_CONTEXT.md` (konteks lengkap dan open item), `docs/PROGRESS.md`, `README.md`, dan `AGENTS.md`/`CLAUDE.md` (aturan kerja yang sama dengan bagian 3).
2. Cek kondisi repo: `git log --oneline -8`, `git status`. Jalankan tes untuk memastikan semuanya hijau.
3. Tanyakan Jevon pekerjaan mana dari bagian 10 yang mau dilanjutkan, lalu tunjukkan rencana singkat sebelum menulis kode.
