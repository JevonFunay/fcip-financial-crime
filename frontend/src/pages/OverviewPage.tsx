import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  fetchBatches,
  fetchOverview,
  fetchQuarantine,
  fetchTransactions,
  runDetection,
  uploadTransactions,
} from "../api/data";
import { errorMessage } from "../api/errors";
import { fetchModels, fetchScores } from "../api/ml";
import { Icon, type IconName } from "../components/Icon";
import { SignalNotice } from "../components/Notices";
import { useAuth } from "../context/AuthContext";
import { formatAmount, formatDateTime } from "../format";
import type { Direction } from "../types";

const PAGE_SIZE = 25;
// Matches the existing ingestion API cap; does not change its validation.
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
const number = (value: number) => value.toLocaleString("id-ID");

export function OverviewPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [direction, setDirection] = useState<Direction | "">("");
  const [offset, setOffset] = useState(0);
  // Empty metadata retains the existing API defaults (FR-101).
  const [sourceSystem, setSourceSystem] = useState("");
  const [businessDate, setBusinessDate] = useState("");
  const [expectedRecords, setExpectedRecords] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const overview = useQuery({ queryKey: ["overview"], queryFn: fetchOverview });
  const transactions = useQuery({
    queryKey: ["transactions", search, direction, offset],
    queryFn: () =>
      fetchTransactions({
        search: search || undefined,
        direction: direction || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: (previous) => previous,
  });
  const quarantine = useQuery({
    queryKey: ["quarantine"],
    queryFn: () => fetchQuarantine(25),
  });
  const batches = useQuery({
    queryKey: ["batches"],
    queryFn: () => fetchBatches(10),
  });
  const models = useQuery({ queryKey: ["models"], queryFn: fetchModels });
  // Separate units and real server totals; never add AML weeks to Fraud transactions.
  const aml = useQuery({
    queryKey: ["model-counts", "AML"],
    queryFn: () => fetchScores({ unit: "AML", limit: 1, offset: 0 }),
  });
  const fraud = useQuery({
    queryKey: ["model-counts", "FRAUD"],
    queryFn: () => fetchScores({ unit: "FRAUD", limit: 1, offset: 0 }),
  });

  function refreshData() {
    for (const key of [
      "overview",
      "transactions",
      "quarantine",
      "batches",
      "models",
      "model-counts",
      "model-scores",
      "alerts",
      "audit",
    ]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  }
  const upload = useMutation({
    mutationFn: (file: File) =>
      uploadTransactions(file, {
        source_system: sourceSystem,
        business_date: businessDate,
        expected_records: expectedRecords,
      }),
    onSuccess: () => {
      refreshData();
      setSelectedFile(null);
      if (fileInput.current) fileInput.current.value = "";
    },
  });
  const detect = useMutation({
    mutationFn: runDetection,
    onSuccess: refreshData,
  });
  const busy = upload.isPending || detect.isPending;
  const canUpload = user?.role === "ROLE_DATA_OPS";
  const canDetect = canUpload || user?.role === "ROLE_ANALYST";
  const total = transactions.data?.total ?? 0;
  const data = overview.data;
  const qualityTotal = data ? data.transactions + data.quarantined : 0;
  const shownOffset = transactions.data?.offset ?? offset;
  const shownFrom = total === 0 ? 0 : shownOffset + 1;
  const shownTo = Math.min(shownOffset + PAGE_SIZE, total);

  function chooseFile(file: File | undefined) {
    if (busy) return;
    setFileError("");
    upload.reset();
    if (!file) {
      setSelectedFile(null);
      return;
    }
    if (
      !file.name.toLowerCase().endsWith(".csv") ||
      file.size > MAX_UPLOAD_BYTES
    ) {
      setSelectedFile(null);
      setFileError(
        file.size > MAX_UPLOAD_BYTES
          ? "File melebihi batas upload 50 MiB. Gunakan bagian CSV yang lebih kecil."
          : "Pilih file transaksi dengan format .csv.",
      );
      if (fileInput.current) fileInput.current.value = "";
      return;
    }
    setSelectedFile(file);
  }

  return (
    <>
      <div className="page-header">
        <div>
          <span className="eyebrow">MONITORING WORKSPACE</span>
          <h1>Overview</h1>
          <p className="page-subtitle">
            Dari data transaksi ke observasi yang dapat ditelusuri.
          </p>
        </div>
        <button
          type="button"
          className="button-secondary refresh-button"
          onClick={refreshData}
          disabled={overview.isFetching}
        >
          <Icon name="history" size={15} />
          Refresh data
        </button>
      </div>
      {overview.error && (
        <p className="error" role="alert">
          Ringkasan tidak dapat dimuat: {errorMessage(overview.error)}
        </p>
      )}
      <section className="tiles" aria-label="Ringkasan monitoring">
        <Tile
          label="Transaksi diterima"
          value={data?.transactions}
          icon="wallet"
          note="Riwayat dalam database"
        />
        <Tile
          label="Alert terbuka"
          value={data?.alerts_open}
          icon="alert"
          note="Menunggu tinjauan manusia"
          tone="alert"
          to="/alerts"
        />
        <Tile
          label="Sinyal AML"
          value={aml.data?.total}
          icon="activity"
          note="Entity-week di atas threshold"
          to="/model-flags?unit=AML"
        />
        <Tile
          label="Sinyal Fraud"
          value={fraud.data?.total}
          icon="scan"
          note="Transaksi di atas threshold"
          to="/model-flags?unit=FRAUD"
        />
      </section>
      {(aml.error || fraud.error) && (
        <p className="error" role="alert">
          Jumlah sinyal belum tersedia: {errorMessage(aml.error ?? fraud.error)}
        </p>
      )}
      <SignalNotice
        text={aml.data?.signal_notice ?? fraud.data?.signal_notice}
      />
      <div className="data-summary" aria-label="Master data dan status review">
        <span className="data-summary-item">
          <Icon name="users" size={16} />
          <strong>{data ? number(data.customers) : "—"}</strong>nasabah
        </span>
        <span className="data-summary-item">
          <Icon name="wallet" size={16} />
          <strong>{data ? number(data.accounts) : "—"}</strong>rekening
        </span>
        <span className="data-summary-item">
          <Icon name="arrow-right" size={16} />
          <strong>{data ? number(data.alerts_escalated) : "—"}</strong>escalated
        </span>
        <span className="data-summary-item">
          <Icon name="file" size={16} />
          <strong>{data ? number(data.cases) : "—"}</strong>case
        </span>
        <span className="data-summary-item small">
          <Icon name="clock" size={15} />
          {data?.latest_transaction_date
            ? `Terbaru: ${formatDateTime(data.latest_transaction_date)}`
            : "Belum ada riwayat transaksi"}
        </span>
      </div>

      <div className="overview-upper">
        {canUpload ? (
          <section className="card" id="upload-data">
            <div className="section-head">
              <div>
                <span className="section-kicker">DATA OPERATIONS</span>
                <h2>Upload data transaksi</h2>
                <p>
                  Unggah CSV, lalu jalankan deteksi setelah semua bagian
                  selesai.
                </p>
              </div>
              <span className="badge badge-blue">CSV · 50 MiB</span>
            </div>
            <div className="operation-tabs" aria-label="Urutan pemrosesan">
              <strong>01 Upload & validasi</strong>
              <span />
              <span className="small">02 Deteksi</span>
              <span />
              <span className="small">03 Review</span>
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (selectedFile && !busy) upload.mutate(selectedFile);
              }}
            >
              <div
                className={`upload-drop ${dragging ? "is-dragging" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (!busy) setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  chooseFile(event.dataTransfer.files[0]);
                }}
              >
                <Icon name={selectedFile ? "file" : "upload"} size={24} />
                <div>
                  <strong>
                    {selectedFile
                      ? selectedFile.name
                      : "Tarik file CSV ke sini"}
                  </strong>
                  <p>
                    {selectedFile
                      ? `${(selectedFile.size / (1024 * 1024)).toLocaleString("id-ID", { maximumFractionDigits: 2 })} MiB · Siap diunggah`
                      : "CSV transaksi · UTF-8 · Maksimal 50 MiB per file"}
                  </p>
                  <label htmlFor="csv-file" className="file-select">
                    {selectedFile
                      ? "Ganti file"
                      : "atau pilih file dari perangkat"}
                  </label>
                  <input
                    id="csv-file"
                    ref={fileInput}
                    className="sr-only"
                    type="file"
                    accept=".csv,text/csv"
                    aria-label="Pilih CSV transaksi"
                    disabled={busy}
                    onChange={(event) => chooseFile(event.target.files?.[0])}
                  />
                </div>
              </div>
              <div className="batch-fields">
                <label>
                  Source system
                  <input
                    aria-label="Source system"
                    value={sourceSystem}
                    onChange={(event) => setSourceSystem(event.target.value)}
                    placeholder="MANUAL_UPLOAD"
                    disabled={busy}
                  />
                </label>
                <label>
                  Business date
                  <input
                    aria-label="Business date"
                    type="date"
                    value={businessDate}
                    onChange={(event) => setBusinessDate(event.target.value)}
                    disabled={busy}
                  />
                </label>
                <label>
                  Expected records
                  <input
                    aria-label="Expected records"
                    type="number"
                    min={0}
                    step={1}
                    value={expectedRecords}
                    onChange={(event) => setExpectedRecords(event.target.value)}
                    placeholder="Opsional"
                    disabled={busy}
                  />
                </label>
              </div>
              <div className="upload-footer">
                <p>
                  File terdiri dari beberapa bagian? Upload semuanya sebelum
                  menjalankan deteksi.
                </p>
                <button type="submit" disabled={busy || !selectedFile}>
                  <Icon name="upload" size={16} />
                  {upload.isPending ? "Mengunggah..." : "Upload CSV"}
                </button>
              </div>
              {fileError && (
                <p className="error upload-feedback" role="alert">
                  {fileError}
                </p>
              )}
              {upload.error && (
                <p className="error upload-feedback" role="alert">
                  {errorMessage(upload.error)}
                </p>
              )}
              {upload.isPending && (
                <div className="processing-state" role="status">
                  <span className="spinner" />
                  Upload dan validasi sedang berjalan. Tunggu hasil batch.
                </div>
              )}
              {upload.data && (
                <div className="upload-feedback" role="status">
                  <strong>
                    {upload.data.batch_ref} · {upload.data.batch_status}
                  </strong>
                  <p className="muted small">{upload.data.file_name}</p>
                  <div className="upload-result-counts">
                    <span>
                      <strong>{number(upload.data.total_rows)}</strong>baris
                      dibaca
                    </span>
                    <span className="ok">
                      <strong>{number(upload.data.accepted)}</strong>diterima
                    </span>
                    <span className="warn">
                      <strong>{number(upload.data.quarantined)}</strong>
                      quarantine
                    </span>
                  </div>
                  <a href="#ingestion-batches">
                    Lihat batch & jejak audit{" "}
                    <Icon name="arrow-right" size={13} />
                  </a>
                </div>
              )}
            </form>
          </section>
        ) : (
          <section className="card">
            <div className="section-head">
              <div>
                <span className="section-kicker">DATA QUALITY</span>
                <h2>Kualitas data transaksi</h2>
                <p>Hasil validasi dari seluruh riwayat unggahan.</p>
              </div>
              <Icon name="layers" size={20} />
            </div>
            <Quality
              accepted={data?.transactions}
              quarantined={data?.quarantined}
            />
            <div className="notice" style={{ marginTop: 20, marginBottom: 0 }}>
              Upload dilakukan oleh Data Operations. Kamu dapat meninjau hasil
              dan jejak setiap batch di bawah.
            </div>
          </section>
        )}
        <section className="card">
          <div className="section-head">
            <div>
              <span className="section-kicker">DETECTION ENGINE</span>
              <h2>Rule & model</h2>
              <p>Riwayat dinilai dengan versi model terdaftar.</p>
            </div>
            <Icon name="scan" size={20} />
          </div>
          {models.isLoading && (
            <p className="loading-text" role="status">
              Memuat versi model...
            </p>
          )}
          {models.error && (
            <p className="error" role="alert">
              {errorMessage(models.error)}
            </p>
          )}
          {models.data?.length === 0 && (
            <p className="muted small">
              Belum ada model terdaftar. Hubungi pengelola workspace.
            </p>
          )}
          {models.data?.map((model) => (
            <div key={model.id} className="model-health">
              <div className="model-health-head">
                <strong>
                  {model.unit === "AML"
                    ? "AML · Weekly entity model"
                    : "Fraud · Transaction model"}
                </strong>
                <span
                  className={`badge ${model.state === "ACTIVE" ? "badge-green" : "badge-amber"}`}
                >
                  {model.state}
                </span>
              </div>
              <p>
                {model.version} · Threshold{" "}
                {model.threshold.toLocaleString("id-ID", {
                  maximumFractionDigits: 4,
                })}
              </p>
              <p>
                Unit skor:{" "}
                {model.unit === "AML"
                  ? "satu nasabah per minggu kalender"
                  : "satu transaksi"}
              </p>
            </div>
          ))}
          {canDetect && (
            <div className="detection-actions">
              <button
                type="button"
                onClick={() => detect.mutate()}
                disabled={busy || !data?.transactions}
              >
                <Icon name="scan" size={17} />
                {detect.isPending ? "Menjalankan..." : "Jalankan deteksi"}
              </button>
            </div>
          )}
          <p className="muted small" style={{ marginTop: 12 }}>
            {data?.transactions
              ? "Deteksi manual menjalankan rule P02 dan seluruh model ACTIVE. Upload seluruh bagian sebelum memulai."
              : "Deteksi dapat dijalankan setelah transaksi diterima."}
          </p>
          <Link className="small" to="/model-flags">
            Lihat sinyal & versi model <Icon name="arrow-right" size={13} />
          </Link>
          {detect.isPending && (
            <div className="processing-state" role="status">
              <span className="spinner" />
              Rule dan model sedang menilai riwayat. Proses dapat memerlukan
              beberapa menit.
            </div>
          )}
          {detect.error && (
            <p className="error upload-feedback" role="alert">
              {errorMessage(detect.error)}
            </p>
          )}
          {detect.data && (
            <div className="upload-feedback" role="status">
              <strong>Deteksi selesai</strong>
              {detect.data.rules.map((rule) => (
                <p
                  key={rule.detection_run_id}
                  className="small"
                  style={{ marginTop: 8 }}
                >
                  {rule.pattern_code}: {number(rule.alerts_created)} alert baru,{" "}
                  {number(rule.alerts_already_existing)} sudah ada.
                </p>
              ))}
              {detect.data.ml.models.map((model) => (
                <p key={model.model_version} className="small">
                  {model.model_version}: {number(model.units_scored)}{" "}
                  {model.unit === "AML" ? "entity-week" : "transaksi"} dinilai;{" "}
                  {number(model.units_flagged)} di atas threshold.
                </p>
              ))}
              <p className="muted small">
                {detect.data.ml.ml_rule_versions_evaluated.length
                  ? `ML_SCORE: ${number(detect.data.ml.alerts_created)} alert baru.`
                  : "ML_SCORE masih DRAFT (CF-05): sinyal model tidak membuat alert sendiri."}
              </p>
              <Link className="small" to="/alerts">
                Buka antrean alert <Icon name="arrow-right" size={13} />
              </Link>
            </div>
          )}
        </section>
      </div>

      <section className="card" aria-busy={transactions.isFetching}>
        <div className="section-head">
          <div>
            <span className="section-kicker">TRANSACTION EXPLORER</span>
            <h2>Riwayat transaksi</h2>
            <p>
              {transactions.data
                ? `${number(total)} transaksi · Tanggal mengikuti waktu transaksi`
                : "Cari dan telusuri transaksi yang diterima."}
            </p>
          </div>
          <div className="toolbar">
            <input
              type="search"
              placeholder="Cari ref, rekening, atau nasabah"
              aria-label="Cari transaksi"
              maxLength={100}
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
            />
            <select
              aria-label="Filter arah transaksi"
              value={direction}
              onChange={(event) => {
                setDirection(event.target.value as Direction | "");
                setOffset(0);
              }}
            >
              <option value="">Semua arah</option>
              <option value="CREDIT">Credit</option>
              <option value="DEBIT">Debit</option>
            </select>
          </div>
        </div>
        {transactions.isLoading && (
          <p className="loading-text" role="status">
            Memuat transaksi...
          </p>
        )}
        {transactions.isFetching && !transactions.isLoading && (
          <p className="loading-text" role="status">
            Memperbarui hasil transaksi...
          </p>
        )}
        {transactions.error && (
          <p className="error" role="alert">
            {errorMessage(transactions.error)}
          </p>
        )}
        {transactions.data?.items.length === 0 && (
          <Empty
            icon="wallet"
            title={
              search || direction
                ? "Tidak ada transaksi yang sesuai"
                : "Workspace siap untuk data baru"
            }
            text={
              search || direction
                ? "Coba kata pencarian atau arah transaksi yang berbeda."
                : canUpload
                  ? "Pilih CSV transaksi di area upload. Hasil validasi akan muncul di sini."
                  : "Transaksi akan muncul setelah Data Operations mengunggah data."
            }
          />
        )}
        {!!transactions.data?.items.length && (
          <>
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Riwayat transaksi"
            >
              <table className="table">
                <thead>
                  <tr>
                    <th>Ref transaksi</th>
                    <th>Tanggal</th>
                    <th>Nasabah</th>
                    <th>Rekening</th>
                    <th className="num">Nominal</th>
                    <th>Arah</th>
                    <th>Channel</th>
                    <th>Counterparty</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.data.items.map((row) => (
                    <tr key={row.id}>
                      <td className="mono">{row.transaction_ref}</td>
                      <td>{formatDateTime(row.transaction_date)}</td>
                      <td>
                        {row.customer_ref}
                        <div className="muted">{row.customer_name}</div>
                      </td>
                      <td className="mono">{row.account_number}</td>
                      <td className="num">
                        {formatAmount(row.amount, row.currency)}
                      </td>
                      <td>
                        <span
                          className={`badge ${row.direction === "CREDIT" ? "badge-green" : "badge-blue"}`}
                        >
                          {row.direction}
                        </span>
                      </td>
                      <td>{row.channel}</td>
                      <td>{row.counterparty_ref ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <span className="muted">
                {number(shownFrom)}–{number(shownTo)} dari {number(total)}
              </span>
              <div className="inline-controls">
                <button
                  type="button"
                  disabled={offset === 0 || transactions.isFetching}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  Sebelumnya
                </button>
                <button
                  type="button"
                  disabled={
                    offset + PAGE_SIZE >= total || transactions.isFetching
                  }
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  Berikutnya
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      <div className="overview-bottom">
        <section className="card" id="ingestion-batches">
          <div className="section-head">
            <div>
              <span className="section-kicker">INGESTION HISTORY</span>
              <h2>Batch unggahan</h2>
              <p>
                {batches.data
                  ? `${number(batches.data.total)} batch · Menampilkan ${batches.data.items.length} terbaru`
                  : "Setiap unggahan punya checksum dan jejak pemrosesan."}
              </p>
            </div>
            <Icon name="upload" size={19} />
          </div>
          {batches.isLoading && (
            <p className="loading-text" role="status">
              Memuat batch...
            </p>
          )}
          {batches.error && (
            <p className="error" role="alert">
              {errorMessage(batches.error)}
            </p>
          )}
          {batches.data?.items.length === 0 && (
            <Empty
              icon="file"
              title="Belum ada batch unggahan"
              text="Setiap file yang diunggah akan tercatat bersama hasil validasi dan jejak audit."
            />
          )}
          {!!batches.data?.items.length && (
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="Batch unggahan"
            >
              <table className="table">
                <thead>
                  <tr>
                    <th>Batch / File</th>
                    <th>Sumber</th>
                    <th>Hasil validasi</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {batches.data.items.map((batch) => (
                    <tr key={batch.id}>
                      <td>
                        <Link
                          className="mono"
                          to={`/audit?correlation_id=${batch.correlation_id}`}
                        >
                          {batch.batch_ref}
                        </Link>
                        <div className="muted">{batch.file_name}</div>
                        <div className="mono muted" title={batch.file_checksum}>
                          SHA {batch.file_checksum.slice(0, 12)}…
                        </div>
                      </td>
                      <td>
                        {batch.source_system}
                        <div className="muted">{batch.business_date}</div>
                        <div className="muted">
                          {batch.registered_by_email ?? "—"}
                        </div>
                      </td>
                      <td>
                        {number(batch.accepted_rows)} diterima
                        <div className="muted">
                          {number(batch.quarantined_rows)} quarantine
                        </div>
                        <div className="muted">
                          {number(batch.total_rows)} dibaca
                        </div>
                      </td>
                      <td>
                        <span
                          className={`badge ${batch.status === "COMPLETED" ? "badge-green" : "badge-amber"}`}
                        >
                          {batch.status}
                        </span>
                        <div className="muted">
                          {formatDateTime(batch.created_at)}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="card">
          <div className="section-head">
            <div>
              <span className="section-kicker">VALIDATION REVIEW</span>
              <h2>Quarantine</h2>
              <p>Baris gagal validasi tersimpan beserta alasannya.</p>
            </div>
            <span className="badge badge-amber">
              {quarantine.data ? number(quarantine.data.total) : "—"} baris
            </span>
          </div>
          <Quality
            accepted={data?.transactions}
            quarantined={data?.quarantined}
          />
          {quarantine.isLoading && (
            <p className="loading-text" role="status">
              Memuat quarantine...
            </p>
          )}
          {quarantine.error && (
            <p className="error" role="alert">
              {errorMessage(quarantine.error)}
            </p>
          )}
          {quarantine.data?.items.length === 0 && (
            <Empty
              icon="check"
              title="Belum ada baris di quarantine"
              text={
                qualityTotal
                  ? "Semua baris yang dibaca lolos validasi."
                  : "Hasil validasi akan tersedia setelah upload pertama."
              }
            />
          )}
          {!!quarantine.data?.items.length && (
            <>
              <p className="muted small" style={{ margin: "18px 0 9px" }}>
                Menampilkan {quarantine.data.items.length} dari{" "}
                {number(quarantine.data.total)} baris.
              </p>
              <div
                className="table-scroll"
                tabIndex={0}
                role="region"
                aria-label="Baris quarantine"
              >
                <table className="table">
                  <thead>
                    <tr>
                      <th>Baris / File</th>
                      <th>Alasan validasi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {quarantine.data.items.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <strong className="mono">
                            Baris {item.row_number}
                          </strong>
                          <div className="muted mono">
                            {item.source_file_name}
                          </div>
                        </td>
                        <td>{item.error_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}

function Tile({
  label,
  value,
  icon,
  note,
  tone,
  to,
}: {
  label: string;
  value?: number;
  icon: IconName;
  note: string;
  tone?: string;
  to?: string;
}) {
  const content = (
    <>
      <div className="tile-top">
        <span className="tile-label">{label}</span>
        <span className="tile-icon">
          <Icon name={icon} size={17} />
        </span>
      </div>
      <strong className="tile-value">
        {value === undefined ? "—" : number(value)}
      </strong>
      <span className="tile-note">{note}</span>
    </>
  );
  const className = `tile ${tone ? `tile-${tone}` : ""}`;
  return to ? (
    <Link className={`${className} tile-link`} to={to}>
      {content}
    </Link>
  ) : (
    <div className={className}>{content}</div>
  );
}
function Empty({
  icon,
  title,
  text,
}: {
  icon: IconName;
  title: string;
  text: string;
}) {
  return (
    <div className="empty-state">
      <Icon name={icon} />
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
function Quality({
  accepted,
  quarantined,
}: {
  accepted?: number;
  quarantined?: number;
}) {
  const total = (accepted ?? 0) + (quarantined ?? 0);
  return (
    <div aria-label="Hasil validasi keseluruhan">
      <div className="quality-track" aria-hidden="true">
        <span
          className="quality-accepted"
          style={{ width: `${total ? ((accepted ?? 0) * 100) / total : 0}%` }}
        />
        <span
          className="quality-rejected"
          style={{
            width: `${total ? ((quarantined ?? 0) * 100) / total : 0}%`,
          }}
        />
      </div>
      <div className="quality-legend">
        <span>{accepted === undefined ? "—" : number(accepted)} diterima</span>
        <span>
          {quarantined === undefined ? "—" : number(quarantined)} quarantine
        </span>
      </div>
    </div>
  );
}
