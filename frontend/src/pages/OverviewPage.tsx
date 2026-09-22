import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  fetchOverview,
  fetchQuarantine,
  fetchTransactions,
  runDetection,
  uploadTransactions,
} from "../api/data";
import { errorMessage } from "../api/errors";
import { useAuth } from "../context/AuthContext";
import { formatAmount, formatDateTime } from "../format";
import type { Direction } from "../types";

const PAGE_SIZE = 25;

export function OverviewPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [direction, setDirection] = useState<Direction | "">("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
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
  const quarantine = useQuery({ queryKey: ["quarantine"], queryFn: () => fetchQuarantine(25) });

  function refreshData() {
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
    void queryClient.invalidateQueries({ queryKey: ["transactions"] });
    void queryClient.invalidateQueries({ queryKey: ["quarantine"] });
  }

  const upload = useMutation({
    mutationFn: (file: File) => uploadTransactions(file),
    onSuccess: () => {
      refreshData();
      if (fileInput.current) fileInput.current.value = "";
    },
  });

  const detect = useMutation({
    mutationFn: runDetection,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  const canUpload = user?.role === "ROLE_DATA_OPS";
  const canDetect = user?.role === "ROLE_DATA_OPS" || user?.role === "ROLE_ANALYST";

  const total = transactions.data?.total ?? 0;
  const shownFrom = total === 0 ? 0 : offset + 1;
  const shownTo = Math.min(offset + PAGE_SIZE, total);

  return (
    <>
      <div className="page-header">
        <h1>Overview</h1>
        {overview.data?.latest_transaction_date && (
          <span className="muted">
            Transaksi terbaru: {formatDateTime(overview.data.latest_transaction_date)}
          </span>
        )}
      </div>

      <section className="tiles">
        <Tile label="Transaksi" value={overview.data?.transactions} />
        <Tile label="Quarantine" value={overview.data?.quarantined} tone={overview.data?.quarantined ? "warn" : undefined} />
        <Tile label="Alert terbuka" value={overview.data?.alerts_open} tone={overview.data?.alerts_open ? "alert" : undefined} to="/alerts" />
        <Tile label="Escalated" value={overview.data?.alerts_escalated} />
        <Tile label="Case" value={overview.data?.cases} />
        <Tile label="Nasabah / rekening" value={overview.data ? `${overview.data.customers} / ${overview.data.accounts}` : undefined} />
      </section>

      {(canUpload || canDetect) && (
        <section className="card ops">
          <h2>Operasi data</h2>
          <div className="ops-row">
            {canUpload && (
              <form
                className="ops-item"
                onSubmit={(e) => {
                  e.preventDefault();
                  const file = fileInput.current?.files?.[0];
                  if (file) upload.mutate(file);
                }}
              >
                <label htmlFor="csv-file">Upload CSV transaksi</label>
                <div className="inline-controls">
                  <input id="csv-file" ref={fileInput} type="file" accept=".csv,text/csv" required />
                  <button type="submit" disabled={upload.isPending}>
                    {upload.isPending ? "Mengunggah..." : "Upload"}
                  </button>
                </div>
                {upload.error && <p className="error">{errorMessage(upload.error)}</p>}
                {upload.data && (
                  <p className="ok">
                    {upload.data.file_name}: {upload.data.total_rows} baris dibaca, {upload.data.accepted} diterima,{" "}
                    {upload.data.quarantined} masuk quarantine.
                  </p>
                )}
              </form>
            )}

            {canDetect && (
              <div className="ops-item">
                <span className="ops-label">Deteksi P02 Structuring</span>
                <div className="inline-controls">
                  <button type="button" onClick={() => detect.mutate()} disabled={detect.isPending}>
                    {detect.isPending ? "Menjalankan..." : "Jalankan deteksi"}
                  </button>
                </div>
                {detect.error && <p className="error">{errorMessage(detect.error)}</p>}
                {detect.data && (
                  <p className="ok">
                    {detect.data.alerts_created} alert baru
                    {detect.data.alerts_already_existing > 0 &&
                      `, ${detect.data.alerts_already_existing} sudah ada sebelumnya`}
                    . <Link to="/alerts">Lihat antrean alert →</Link>
                  </p>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      <section className="card">
        <div className="section-head">
          <h2>Transaksi</h2>
          <div className="toolbar">
            <input
              type="search"
              placeholder="Cari ref, rekening, atau nasabah"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              aria-label="Cari transaksi"
            />
            <select
              value={direction}
              onChange={(e) => {
                setDirection(e.target.value as Direction | "");
                setOffset(0);
              }}
              aria-label="Filter arah transaksi"
            >
              <option value="">Semua arah</option>
              <option value="CREDIT">Credit</option>
              <option value="DEBIT">Debit</option>
            </select>
          </div>
        </div>

        {transactions.isLoading && <p className="muted">Memuat...</p>}
        {transactions.error && <p className="error">{errorMessage(transactions.error)}</p>}

        {transactions.data && transactions.data.items.length === 0 && (
          <p className="muted">
            {search || direction
              ? "Tidak ada transaksi yang cocok dengan filter ini."
              : "Belum ada transaksi. Upload file CSV untuk mengisi data."}
          </p>
        )}

        {transactions.data && transactions.data.items.length > 0 && (
          <>
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Ref</th>
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
                      <td className="num">{formatAmount(row.amount, row.currency)}</td>
                      <td>{row.direction}</td>
                      <td>{row.channel}</td>
                      <td>{row.counterparty_ref ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="pager">
              <span className="muted">
                {shownFrom}–{shownTo} dari {total}
              </span>
              <div className="inline-controls">
                <button type="button" onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0}>
                  Sebelumnya
                </button>
                <button
                  type="button"
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  disabled={offset + PAGE_SIZE >= total}
                >
                  Berikutnya
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      <section className="card">
        <div className="section-head">
          <h2>Quarantine {quarantine.data ? `(${quarantine.data.total})` : ""}</h2>
          <span className="muted">Baris CSV yang gagal validasi — tidak dibuang, disimpan dengan alasannya.</span>
        </div>

        {quarantine.data && quarantine.data.items.length === 0 && (
          <p className="muted">Belum ada baris yang masuk quarantine.</p>
        )}

        {quarantine.data && quarantine.data.items.length > 0 && (
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Baris</th>
                  <th>File</th>
                  <th>Alasan ditolak</th>
                </tr>
              </thead>
              <tbody>
                {quarantine.data.items.map((item) => (
                  <tr key={item.id}>
                    <td className="mono">{item.row_number}</td>
                    <td className="mono">{item.source_file_name}</td>
                    <td>{item.error_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function Tile({
  label,
  value,
  tone,
  to,
}: {
  label: string;
  value: number | string | undefined;
  tone?: "warn" | "alert";
  to?: string;
}) {
  const body = (
    <>
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value ?? "–"}</span>
    </>
  );
  const className = `tile${tone ? ` tile-${tone}` : ""}`;
  return to ? (
    <Link to={to} className={`${className} tile-link`}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}
