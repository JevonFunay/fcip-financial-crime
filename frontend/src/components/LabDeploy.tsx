import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  approveLabModel,
  monitorLabDataset,
  returnLabModel,
  setLabModelAlerts,
  submitLabModel,
  suspendLabModel,
} from "../api/lab";
import { errorMessage } from "../api/errors";
import { formatDateTime } from "../format";
import type { LabDataset, LabModelsResponse, LabModelVersion, LabMonitorResult } from "../types/lab";
import { Icon } from "./Icon";

const STATE_LABEL: Record<string, string> = {
  DRAFT: "Draft",
  PENDING_APPROVAL: "Menunggu MLRO",
  ACTIVE: "Aktif",
  SUSPENDED: "Dinonaktifkan",
  RETIRED: "Diganti",
  IN_SIMULATION: "Simulasi",
};
const STATE_BADGE: Record<string, string> = {
  DRAFT: "badge-amber",
  PENDING_APPROVAL: "badge-blue",
  ACTIVE: "badge-green",
  SUSPENDED: "",
  RETIRED: "",
  IN_SIMULATION: "badge-blue",
};

function pct(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(100 * value).toLocaleString("id-ID", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`
    : "–";
}

function num(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("id-ID") : "–";
}

function score(value: number): string {
  const digits = value >= 0.01 && value <= 0.99 ? 4 : 6;
  return Number(value.toPrecision(digits)).toLocaleString("id-ID", { maximumSignificantDigits: digits });
}

function useModelsUpdate() {
  const queryClient = useQueryClient();
  return (data: LabModelsResponse) => {
    queryClient.setQueryData(["lab-models"], data);
    for (const key of ["models", "model-scores", "lab-run", "alerts", "audit"]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };
}

// --- model alerts switch (CF-05) ---------------------------------------------------------------

function ModelAlertsBanner({ data, isMlro }: { data: LabModelsResponse; isMlro: boolean }) {
  const update = useModelsUpdate();
  const [reason, setReason] = useState("");
  const state = data.model_alerts;
  const toggle = useMutation({
    mutationFn: () => setLabModelAlerts(!state.active, reason),
    onSuccess: (result) => {
      update(result);
      setReason("");
    },
  });
  return (
    <div className={`lab-alerts-switch ${state.active ? "is-on" : ""}`}>
      <div>
        <p>
          <strong>Alert dari skor model</strong>{" "}
          <span className={`badge ${state.active ? "badge-green" : "badge-amber"}`}>
            {state.active ? "AKTIF" : "NONAKTIF"}
          </span>
        </p>
        <p className="small">
          {state.active
            ? "Skor di atas threshold model ACTIVE membuat alert."
            : "Skor model menjadi sinyal pendukung; tidak membuat alert sendiri."}
        </p>
      </div>
      {isMlro && (
        <div className="lab-alerts-actions">
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={`Alasan (min. ${data.min_reason_length} karakter)`}
            aria-label="Alasan mengubah alert dari skor model"
          />
          <button
            type="button"
            className={state.active ? "button-secondary" : ""}
            onClick={() => toggle.mutate()}
            disabled={toggle.isPending || reason.trim().length < data.min_reason_length}
          >
            {state.active ? "Matikan alert dari skor" : "Aktifkan alert dari skor"}
          </button>
          {toggle.error && <p className="error small" role="alert">{errorMessage(toggle.error)}</p>}
        </div>
      )}
    </div>
  );
}

// --- one model version ---------------------------------------------------------------------------

function VersionCard({
  version,
  role,
  minReason,
  modelAlertsActive,
  highlight,
}: {
  version: LabModelVersion;
  role: string | undefined;
  minReason: number;
  modelAlertsActive: boolean;
  highlight: boolean;
}) {
  const update = useModelsUpdate();
  const [text, setText] = useState("");
  const [alertsOn, setAlertsOn] = useState(true);
  const done = (result: LabModelsResponse) => {
    update(result);
    setText("");
  };
  const submit = useMutation({ mutationFn: () => submitLabModel(version.id, text), onSuccess: done });
  const approve = useMutation({
    // Only switch model alerts when the MLRO changes it here; otherwise leave it as it is.
    mutationFn: () => approveLabModel(version.id, text, alertsOn === modelAlertsActive ? null : alertsOn),
    onSuccess: done,
  });
  const giveBack = useMutation({ mutationFn: () => returnLabModel(version.id, text), onSuccess: done });
  const suspend = useMutation({ mutationFn: () => suspendLabModel(version.id, text), onSuccess: done });
  const busy = submit.isPending || approve.isPending || giveBack.isPending || suspend.isPending;
  const error = submit.error ?? approve.error ?? giveBack.error ?? suspend.error;
  const longEnough = text.trim().length >= minReason;
  const isAnalyst = role === "ROLE_ANALYST";
  const isMlro = role === "ROLE_MLRO";
  const canAct =
    (version.state === "DRAFT" && isAnalyst) ||
    (version.state === "PENDING_APPROVAL" && isMlro) ||
    (version.state === "ACTIVE" && isMlro);
  const waiting =
    version.state === "DRAFT"
      ? "Analyst mengajukan model ini ke MLRO."
      : version.state === "PENDING_APPROVAL"
        ? "Menunggu persetujuan MLRO (bukan pengaju)."
        : version.state === "ACTIVE"
          ? "Model ini menilai data baru yang diunggah untuk dipantau."
          : null;

  return (
    <article className={`lab-version ${highlight ? "is-current" : ""} state-${version.state.toLowerCase()}`}>
      <div className="lab-version-head">
        <div>
          <strong className="mono">{version.version}</strong>
          <span className="small muted">
            {" "}
            · {version.model_name} · {version.training_kind === "supervised" ? "berlabel" : "tanpa label"}
          </span>
        </div>
        <span className={`badge ${STATE_BADGE[version.state] ?? ""}`}>{STATE_LABEL[version.state] ?? version.state}</span>
      </div>
      <dl className="lab-version-facts">
        <div>
          <dt>Dataset</dt>
          <dd>{version.dataset_name ?? "–"}</dd>
        </div>
        <div>
          <dt>Threshold</dt>
          <dd className="mono">{score(version.threshold)}</dd>
        </div>
        <div>
          <dt>Validasi</dt>
          <dd>
            presisi {pct(version.validation.precision)} · recall {pct(version.validation.recall)}
          </dd>
        </div>
        <div>
          <dt>{version.evaluation_state === "REVEALED" ? "Uji final terkunci" : "Uji historis · sudah terbuka"}</dt>
          <dd>
            presisi {pct(version.test.precision)} · recall {pct(version.test.recall)} ·{" "}
            {num(Math.round(version.test.flagged_per_10k ?? NaN))} alert/10k
          </dd>
        </div>
        <div>
          <dt>Orang</dt>
          <dd>
            dibuat {version.created_by_email ?? "–"}
            {version.submitted_by_email && <> · diajukan {version.submitted_by_email}</>}
            {version.approved_by_email && <> · disetujui {version.approved_by_email}</>}
          </dd>
        </div>
        {version.superseded_by_version && (
          <div>
            <dt>Diganti oleh</dt>
            <dd className="mono">{version.superseded_by_version}</dd>
          </div>
        )}
      </dl>
      {canAct ? (
        <div className="lab-version-action">
          <textarea
            rows={2}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={
              version.state === "DRAFT"
                ? `Justifikasi pengajuan (min. ${minReason} karakter)`
                : version.state === "PENDING_APPROVAL"
                  ? `Catatan persetujuan atau alasan pengembalian (min. ${minReason} karakter)`
                  : `Alasan menonaktifkan (min. ${minReason} karakter)`
            }
            aria-label="Catatan untuk tindakan ini"
            disabled={busy}
          />
          {version.state === "PENDING_APPROVAL" && (
            <label className="inline lab-alerts-check">
              <input type="checkbox" checked={alertsOn} onChange={(event) => setAlertsOn(event.target.checked)} />
              <span>Skor di atas threshold langsung menjadi alert (tanpa menunggu rule pola)</span>
            </label>
          )}
          <div className="lab-version-buttons">
            {version.state === "DRAFT" && (
              <button type="button" onClick={() => submit.mutate()} disabled={busy || !longEnough}>
                <Icon name="arrow-right" size={15} />
                {submit.isPending ? "Mengajukan..." : "Ajukan ke MLRO"}
              </button>
            )}
            {version.state === "PENDING_APPROVAL" && (
              <>
                <button type="button" onClick={() => approve.mutate()} disabled={busy || !longEnough}>
                  <Icon name="check" size={15} />
                  {approve.isPending ? "Menyetujui..." : "Setujui & aktifkan"}
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => giveBack.mutate()}
                  disabled={busy || !longEnough}
                >
                  Kembalikan ke Analyst
                </button>
              </>
            )}
            {version.state === "ACTIVE" && (
              <button type="button" className="button-secondary" onClick={() => suspend.mutate()} disabled={busy || !longEnough}>
                Nonaktifkan model
              </button>
            )}
          </div>
          {error && <p className="error small" role="alert">{errorMessage(error)}</p>}
        </div>
      ) : (
        waiting && <p className="small muted lab-version-waiting">{waiting}</p>
      )}
      {version.history.length > 0 && (
        <details className="lab-version-history">
          <summary>Riwayat ({version.history.length})</summary>
          <ol>
            {version.history.map((h, i) => (
              <li key={i}>
                <span className="small muted">{formatDateTime(h.at)}</span>{" "}
                <strong>
                  {h.from_state ? `${STATE_LABEL[h.from_state] ?? h.from_state} → ` : ""}
                  {STATE_LABEL[h.to_state] ?? h.to_state}
                </strong>{" "}
                <span className="small">
                  oleh {h.actor_email ?? "sistem"}
                  {h.actor_role ? ` (${h.actor_role.replace("ROLE_", "")})` : ""}
                </span>
                {h.reason && <div className="small muted">{h.reason}</div>}
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  );
}

export function ModelApprovalPanel({
  data,
  role,
  datasetId,
}: {
  data: LabModelsResponse;
  role: string | undefined;
  datasetId: string | null;
}) {
  const ordered = [...data.items].sort((a, b) => {
    const rank = (v: LabModelVersion) => (v.state === "ACTIVE" ? 0 : v.state === "PENDING_APPROVAL" ? 1 : v.state === "DRAFT" ? 2 : 3);
    return rank(a) - rank(b) || b.created_at.localeCompare(a.created_at);
  });
  return (
    <section className="card lab-section" id="lab-step-6" aria-labelledby="lab-step-6-title">
      <div className="section-head">
        <div>
          <span className="section-kicker">LANGKAH 6</span>
          <h2 id="lab-step-6-title">Persetujuan & status model</h2>
          <p>
            Persetujuan oleh MLRO lain mengganti model aktif sebelumnya.
          </p>
        </div>
        <span className="badge badge-blue">{data.items.length} model dari lab</span>
      </div>
      <ModelAlertsBanner data={data} isMlro={role === "ROLE_MLRO"} />
      {ordered.length === 0 ? (
        <p className="muted small">Belum ada model yang disimpan dari lab. Simpan model di langkah 5.</p>
      ) : (
        <div className="lab-versions">
          {ordered.map((version) => (
            <VersionCard
              key={version.id}
              version={version}
              role={role}
              minReason={data.min_reason_length}
              modelAlertsActive={data.model_alerts.active}
              highlight={version.dataset_id === datasetId}
            />
          ))}
        </div>
      )}
    </section>
  );
}

// --- monitoring new data -------------------------------------------------------------------------

export function MonitorPanel({
  dataset,
  models,
  canMonitor,
  maxBytes,
  onResult,
}: {
  dataset: LabDataset;
  models: LabModelsResponse;
  canMonitor: boolean;
  maxBytes: number;
  onResult: (result: LabMonitorResult) => void;
}) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const monitor = useMutation({
    mutationFn: () => monitorLabDataset(dataset.id, file as File),
    onSuccess: (result) => {
      onResult(result);
      setFile(null);
      if (input.current) input.current.value = "";
      for (const key of ["alerts", "overview", "transactions", "batches", "quarantine", "model-scores", "model-counts", "audit"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
  const active = models.items.find((v) => v.state === "ACTIVE");
  const result = monitor.data;
  const mapped = dataset.status === "MAPPED";

  function choose(candidate: File | undefined) {
    setFileError("");
    monitor.reset();
    if (!candidate) return;
    if (!/\.(csv|parquet)$/i.test(candidate.name) || candidate.size > maxBytes) {
      setFileError(candidate.size > maxBytes ? "File melebihi batas upload." : "Pilih file .csv atau .parquet.");
      return;
    }
    setFile(candidate);
  }

  return (
    <section className="card lab-section" id="lab-step-7" aria-labelledby="lab-step-7-title">
      <div className="section-head">
        <div>
          <span className="section-kicker">LANGKAH 7</span>
          <h2 id="lab-step-7-title">Pantau data baru</h2>
          <p>
            Gunakan susunan kolom <strong>{dataset.name}</strong>. Upload dilanjutkan dengan deteksi.
          </p>
        </div>
        <span className="badge badge-blue">CSV / Parquet</span>
      </div>
      <ul className="lab-monitor-status">
        <li>
          <span>Model aktif</span>
          <strong className={active ? "" : "warn"}>
            {active ? `${active.version} · ${active.model_name}` : "Belum ada — setujui model di langkah 6"}
          </strong>
        </li>
        <li>
          <span>Alert dari skor model</span>
          <strong className={models.model_alerts.active ? "ok" : "warn"}>
            {models.model_alerts.active ? "Aktif (tanpa menunggu rule)" : "Nonaktif (skor hanya sebagai sinyal)"}
          </strong>
        </li>
        <li>
          <span>Pemetaan kolom</span>
          <strong className={mapped ? "ok" : "warn"}>{mapped ? "Dipakai ulang dari dataset ini" : "Belum dipetakan"}</strong>
        </li>
      </ul>
      {canMonitor ? (
        <form
          className="lab-monitor-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (file && !monitor.isPending) monitor.mutate();
          }}
        >
          <label htmlFor="lab-monitor-file" className="file-select">
            <Icon name={file ? "file" : "upload"} size={16} /> {file ? file.name : "Pilih file transaksi baru"}
          </label>
          <input
            id="lab-monitor-file"
            ref={input}
            className="sr-only"
            type="file"
            accept=".csv,text/csv,.parquet"
            onChange={(event) => choose(event.target.files?.[0])}
          />
          <button type="submit" disabled={!file || monitor.isPending || !mapped}>
            <Icon name="activity" size={16} />
            {monitor.isPending ? "Mengunggah & mendeteksi..." : "Unggah & jalankan deteksi"}
          </button>
          {fileError && <p className="error small" role="alert">{fileError}</p>}
          {monitor.error && <p className="error small" role="alert">{errorMessage(monitor.error)}</p>}
          {monitor.isPending && (
            <div className="processing-state" role="status">
              <span className="spinner" />
              Validasi, penyimpanan batch, lalu deteksi oleh rule dan model.
            </div>
          )}
        </form>
      ) : (
        <p className="muted small">Data Operations mengunggah data baru untuk dipantau (FR-101).</p>
      )}
      {result && (
        <div className="lab-monitor-result" role="status">
          <div className="lab-tiles">
            <div className="lab-tile">
              <span>Baris diterima</span>
              <strong>{num(result.accepted)}</strong>
              <small>
                dari {num(result.total_rows)} · batch {result.batch_ref}
              </small>
            </div>
            <div className="lab-tile">
              <span>Dikarantina</span>
              <strong>{num(result.quarantined)}</strong>
              <small>dengan alasan, di Overview</small>
            </div>
            <div className="lab-tile">
              <span>Alert dari model</span>
              <strong>{num(result.model_alerts_created)}</strong>
              <small>
                {result.models.map((m) => `${num(m.units_flagged)} transaksi ≥ threshold`).join(" · ") || "tidak ada model aktif"}
              </small>
            </div>
            <div className="lab-tile">
              <span>Alert dari rule</span>
              <strong>{num(result.rule_alerts_created)}</strong>
              <small>seluruh rule pola yang aktif (P01–P10)</small>
            </div>
          </div>
          <p className="small muted">
            {num(result.account_proxies_created)} rekening baru dicatat sebagai proxy anonim (tanpa identitas orang) ·
            selesai dalam {result.seconds.toLocaleString("id-ID")} detik.
          </p>
          {result.mapping_report.notes.map((note) => (
            <p key={note} className="notice lab-note">{note}</p>
          ))}
          <Link to="/alerts" className="btn-link">
            Buka antrean alert <Icon name="arrow-right" size={14} />
          </Link>
          <p className="signal-notice small">{result.signal_notice}</p>
        </div>
      )}
    </section>
  );
}
