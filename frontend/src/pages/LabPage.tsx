import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  fetchLabDataset,
  fetchLabDatasets,
  fetchLabOptions,
  fetchLabRun,
  saveLabMapping,
  saveLabModel,
  trainLabModels,
  uploadLabDataset,
} from "../api/lab";
import { errorMessage } from "../api/errors";
import { Icon } from "../components/Icon";
import { useAuth } from "../context/AuthContext";
import { formatDateTime } from "../format";
import type {
  LabCurvePoint,
  LabDataset,
  LabMappingIn,
  LabModelResult,
  LabOptions,
  LabRun,
  LabSavedModel,
} from "../types/lab";
import "../lab.css";

const WRITER_ROLES = ["ROLE_DATA_OPS", "ROLE_ANALYST"];
const POSITIVE_DEFAULTS = new Set(["1", "true", "yes", "y", "t", "fraud", "ya"]);
const NONE = "";

function pct(value: number | null | undefined, digits = 1): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(100 * value).toLocaleString("id-ID", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`
    : "–";
}

function num(value: number | null | undefined, digits = 0): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("id-ID", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "–";
}

function dec(value: number | null | undefined, digits = 3): string {
  return num(value, digits);
}

/** Model scores can crowd near 0 or 1, so show enough digits to tell neighbouring points apart. */
function threshold(value: number): string {
  const digits = value >= 0.01 && value <= 0.99 ? 4 : 6;
  return Number(value.toPrecision(digits)).toLocaleString("id-ID", { maximumSignificantDigits: digits });
}

function dateOnly(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString("id-ID", { dateStyle: "medium" }) : "–";
}

function kindLabel(kind: string): string {
  return kind === "supervised" ? "Berlabel · supervised" : "Tanpa label · unsupervised";
}

// --- stepper -------------------------------------------------------------------------------------

const STEPS = [
  { title: "Dataset berlabel", caption: "Unggah atau pilih" },
  { title: "Pemetaan kolom", caption: "Cocokkan & validasi" },
  { title: "Fitur & model", caption: "Pilih & latih" },
  { title: "Hasil & threshold", caption: "Bandingkan & atur" },
  { title: "Simpan draft", caption: "Masuk registry" },
];

function Stepper({ reached }: { reached: number }) {
  return (
    <ol className="lab-stepper" aria-label="Langkah lab model">
      {STEPS.map((step, index) => {
        const state = index < reached ? "is-done" : index === reached ? "is-current" : "is-locked";
        return (
          <li key={step.title} className={`lab-step ${state}`}>
            <span className="lab-step-number">{index < reached ? <Icon name="check" size={14} /> : index + 1}</span>
            <span>
              <strong>{step.title}</strong>
              <small>{step.caption}</small>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// --- step 1: dataset -----------------------------------------------------------------------------

function DatasetStep({
  options,
  datasets,
  selectedId,
  canWrite,
  onSelect,
}: {
  options: LabOptions;
  datasets: LabDataset[];
  selectedId: string | null;
  canWrite: boolean;
  onSelect: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState("");
  const upload = useMutation({
    mutationFn: () => uploadLabDataset(file as File, name),
    onSuccess: (dataset) => {
      queryClient.setQueryData(["lab-dataset", dataset.id], dataset);
      void queryClient.invalidateQueries({ queryKey: ["lab-datasets"] });
      setFile(null);
      setName("");
      if (fileInput.current) fileInput.current.value = "";
      onSelect(dataset.id);
    },
  });
  const maxMb = Math.round(options.max_upload_bytes / (1024 * 1024));

  function choose(candidate: File | undefined) {
    setFileError("");
    upload.reset();
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith(".csv") || candidate.size > options.max_upload_bytes) {
      setFileError(
        candidate.size > options.max_upload_bytes
          ? `File melebihi batas ${maxMb} MB.`
          : "Pilih file berformat .csv.",
      );
      return;
    }
    setFile(candidate);
  }

  return (
    <section className="card lab-section" aria-labelledby="lab-step-1">
      <div className="section-head">
        <div>
          <span className="section-kicker">LANGKAH 1</span>
          <h2 id="lab-step-1">Dataset berlabel</h2>
          <p>Dataset latih disimpan terpisah dari transaksi yang dimonitor dan wajib punya kolom label (kunci jawaban).</p>
        </div>
        <span className="badge badge-blue">CSV · {maxMb} MB</span>
      </div>
      <div className="lab-dataset-grid">
        {canWrite && (
          <form
            className="lab-upload"
            onSubmit={(event) => {
              event.preventDefault();
              if (file && !upload.isPending) upload.mutate();
            }}
          >
            <div
              className={`upload-drop ${dragging ? "is-dragging" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                choose(event.dataTransfer.files[0]);
              }}
            >
              <Icon name={file ? "file" : "upload"} size={24} />
              <div>
                <strong>{file ? file.name : "Tarik CSV berlabel ke sini"}</strong>
                <p>
                  {file
                    ? `${(file.size / (1024 * 1024)).toLocaleString("id-ID", { maximumFractionDigits: 2 })} MB · siap diunggah`
                    : "Satu baris per transaksi, dengan satu kolom label"}
                </p>
                <label htmlFor="lab-file" className="file-select">
                  {file ? "Ganti file" : "atau pilih file dari perangkat"}
                </label>
                <input
                  id="lab-file"
                  ref={fileInput}
                  className="sr-only"
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(event) => choose(event.target.files?.[0])}
                />
              </div>
            </div>
            <label>
              Nama dataset
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Opsional, misalnya: Sintetis FCIP 100 ribu"
              />
            </label>
            <button type="submit" disabled={!file || upload.isPending}>
              <Icon name="upload" size={16} />
              {upload.isPending ? "Mengunggah & membaca kolom..." : "Unggah dataset"}
            </button>
            {fileError && <p className="error" role="alert">{fileError}</p>}
            {upload.error && <p className="error" role="alert">{errorMessage(upload.error)}</p>}
          </form>
        )}
        <div className="lab-dataset-list">
          <h3>Dataset tersimpan</h3>
          {datasets.length === 0 ? (
            <p className="muted small">Belum ada dataset. {canWrite ? "Unggah CSV berlabel untuk mulai." : "Data Ops atau Analyst dapat mengunggah dataset."}</p>
          ) : (
            <ul>
              {datasets.map((dataset) => (
                <li key={dataset.id}>
                  <button
                    type="button"
                    className={`lab-dataset-item ${dataset.id === selectedId ? "is-selected" : ""}`}
                    onClick={() => onSelect(dataset.id)}
                    aria-pressed={dataset.id === selectedId}
                  >
                    <span>
                      <strong>{dataset.name}</strong>
                      <small>
                        {num(dataset.row_count)} baris · {dataset.runs.length} run · {formatDateTime(dataset.created_at)}
                      </small>
                    </span>
                    <span className={`badge ${dataset.status === "MAPPED" ? "badge-green" : "badge-amber"}`}>
                      {dataset.status === "MAPPED" ? "Siap dilatih" : "Perlu pemetaan"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

// --- step 2: mapping -----------------------------------------------------------------------------

function initialMapping(dataset: LabDataset): LabMappingIn {
  if (dataset.mapping) return dataset.mapping;
  const s = dataset.suggestion;
  return {
    fields: { ...s.fields },
    label_column: s.label_column ?? "",
    positive_values: s.positive_values,
    category_column: s.category_column,
    date_format: s.date_format,
    default_currency: null,
  };
}

function MappingStep({ options, dataset, canWrite }: { options: LabOptions; dataset: LabDataset; canWrite: boolean }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<LabMappingIn>(() => initialMapping(dataset));
  const [positiveText, setPositiveText] = useState("");
  useEffect(() => {
    setForm(initialMapping(dataset));
    setPositiveText("");
    // Re-initialise only when another dataset is opened, not on every refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset.id]);
  const save = useMutation({
    mutationFn: (mapping: LabMappingIn) => saveLabMapping(dataset.id, mapping),
    onSuccess: (updated) => {
      queryClient.setQueryData(["lab-dataset", updated.id], updated);
      void queryClient.invalidateQueries({ queryKey: ["lab-datasets"] });
    },
  });
  const labelValues = form.label_column ? dataset.suggestion.low_cardinality[form.label_column] : undefined;
  const reserved = new Set([form.label_column, form.category_column ?? ""].filter(Boolean));
  const validation = dataset.validation;

  function setField(key: string, column: string) {
    setForm((current) => ({ ...current, fields: { ...current.fields, [key]: column || null } }));
  }

  function setLabel(column: string) {
    const values = dataset.suggestion.low_cardinality[column] ?? [];
    setForm((current) => ({
      ...current,
      label_column: column,
      positive_values: values.filter((v) => POSITIVE_DEFAULTS.has(v.trim().toLowerCase())),
      fields: Object.fromEntries(Object.entries(current.fields).filter(([, c]) => c !== column)),
    }));
  }

  function submit() {
    const positives = labelValues
      ? form.positive_values
      : positiveText.split(",").map((v) => v.trim()).filter(Boolean);
    save.mutate({
      ...form,
      positive_values: positives,
      category_column: form.category_column || null,
      default_currency: form.default_currency?.trim() ? form.default_currency.trim().toUpperCase() : null,
    });
  }

  return (
    <section className="card lab-section" aria-labelledby="lab-step-2">
      <div className="section-head">
        <div>
          <span className="section-kicker">LANGKAH 2</span>
          <h2 id="lab-step-2">Pemetaan kolom · {dataset.name}</h2>
          <p>Cocokkan kolom file dengan field FCIP. Saran otomatis boleh diubah; kolom label tidak pernah menjadi fitur.</p>
        </div>
        <span className="badge badge-blue">{num(dataset.row_count)} baris · {dataset.columns.length} kolom</span>
      </div>

      <details className="lab-preview">
        <summary>Pratinjau 8 baris pertama</summary>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>{dataset.columns.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {dataset.preview.map((row, i) => (
                <tr key={i}>{dataset.columns.map((c) => <td key={c} className="mono">{row[c]}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <div className="lab-mapping-layout">
        <div className="lab-mapping">
          <h3>Field transaksi</h3>
          {options.fields.map((field) => (
            <label key={field.key} className="lab-mapping-row">
              <span>
                {field.label}
                {field.required && <em className="lab-required"> wajib</em>}
                {field.help && <small>{field.help}</small>}
              </span>
              <select
                value={form.fields[field.key] ?? NONE}
                onChange={(event) => setField(field.key, event.target.value)}
                disabled={!canWrite}
              >
                <option value={NONE}>— tidak ada —</option>
                {dataset.columns.filter((c) => !reserved.has(c)).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
          ))}
        </div>
        <div className="lab-mapping lab-mapping-side">
          <h3>Kunci jawaban</h3>
          <label>
            Kolom label
            <select value={form.label_column} onChange={(event) => setLabel(event.target.value)} disabled={!canWrite}>
              <option value={NONE}>— pilih kolom label —</option>
              {dataset.columns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          {form.label_column && (
            <fieldset className="lab-positive">
              <legend>Nilai yang dihitung positif</legend>
              {labelValues ? (
                labelValues.map((value) => (
                  <label key={value} className="inline">
                    <input
                      type="checkbox"
                      checked={form.positive_values.includes(value)}
                      disabled={!canWrite}
                      onChange={(event) =>
                        setForm((current) => ({
                          ...current,
                          positive_values: event.target.checked
                            ? [...current.positive_values, value]
                            : current.positive_values.filter((v) => v !== value),
                        }))
                      }
                    />
                    <span className="mono">{value === "" ? "(kosong)" : value}</span>
                  </label>
                ))
              ) : (
                <input
                  value={positiveText}
                  onChange={(event) => setPositiveText(event.target.value)}
                  placeholder="Nilai positif, pisahkan dengan koma"
                  disabled={!canWrite}
                />
              )}
            </fieldset>
          )}
          <label>
            Kolom kategori / pola <small className="muted">opsional, hanya untuk laporan per pola</small>
            <select
              value={form.category_column ?? NONE}
              onChange={(event) => setForm((current) => ({ ...current, category_column: event.target.value || null }))}
              disabled={!canWrite}
            >
              <option value={NONE}>— tidak ada —</option>
              {dataset.columns.filter((c) => c !== form.label_column).map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label>
            Format tanggal
            <select
              value={form.date_format}
              onChange={(event) => setForm((current) => ({ ...current, date_format: event.target.value as "iso" | "month_first" }))}
              disabled={!canWrite}
            >
              <option value="iso">ISO · 2026-04-01 13:45:00</option>
              <option value="month_first">Bulan dulu · 04/01/2026</option>
            </select>
          </label>
          <label>
            Mata uang default <small className="muted">dipakai jika kolom mata uang tidak ada</small>
            <input
              value={form.default_currency ?? ""}
              maxLength={3}
              placeholder="mis. IDR atau USD"
              onChange={(event) => setForm((current) => ({ ...current, default_currency: event.target.value }))}
              disabled={!canWrite}
            />
          </label>
          {canWrite && (
            <button type="button" onClick={submit} disabled={save.isPending || !form.label_column}>
              <Icon name="scan" size={16} />
              {save.isPending ? "Memvalidasi..." : "Validasi pemetaan"}
            </button>
          )}
          {save.error && <p className="error" role="alert">{errorMessage(save.error)}</p>}
        </div>
      </div>

      {validation && (
        <div className="lab-validation" role="status">
          <div className="lab-tiles">
            <div className="lab-tile">
              <span>Baris dapat dipakai</span>
              <strong>{num(validation.usable_rows)}</strong>
              <small>dari {num(validation.total_rows)} baris file</small>
            </div>
            <div className="lab-tile">
              <span>Positif (sesuai label)</span>
              <strong>{num(validation.positives)}</strong>
              <small>{pct(validation.positive_rate, 2)} dari baris dipakai</small>
            </div>
            <div className="lab-tile">
              <span>Dikarantina</span>
              <strong>{num(validation.quarantined)}</strong>
              <small>validator yang sama dengan upload FCIP</small>
            </div>
            <div className="lab-tile">
              <span>Periode data</span>
              <strong className="lab-tile-small">{dateOnly(validation.date_from)} – {dateOnly(validation.date_to)}</strong>
              <small>{num(validation.accounts)} akun · {validation.currency}</small>
            </div>
          </div>
          {validation.quarantine_reasons.length > 0 && (
            <details>
              <summary>Alasan karantina</summary>
              <ul className="lab-reasons">
                {validation.quarantine_reasons.map((r) => (
                  <li key={r.reason}><span className="mono">{r.reason}</span> <strong>{num(r.rows)}</strong></li>
                ))}
              </ul>
            </details>
          )}
          {validation.category_counts && (
            <p className="small muted">
              Positif per kategori: {Object.entries(validation.category_counts).map(([k, v]) => `${k} ${num(v)}`).join(" · ")}
            </p>
          )}
          {validation.notes.map((note) => <p key={note} className="notice lab-note">{note}</p>)}
          <p className={validation.ready ? "ok small" : "warn small"}>
            {validation.ready ? "Dataset siap dilatih. Lanjut ke langkah 3." : "Dataset belum siap dilatih."}
          </p>
        </div>
      )}
    </section>
  );
}

// --- step 3: features & models -------------------------------------------------------------------

function TrainingStep({
  options,
  dataset,
  canWrite,
  onTrained,
}: {
  options: LabOptions;
  dataset: LabDataset;
  canWrite: boolean;
  onTrained: (run: LabRun) => void;
}) {
  const queryClient = useQueryClient();
  const [groups, setGroups] = useState<string[]>(() => options.feature_groups.filter((g) => g.default).map((g) => g.key));
  const [models, setModels] = useState<string[]>(() => options.models.filter((m) => m.default).map((m) => m.key));
  const [trainShare, setTrainShare] = useState(0.6);
  const train = useMutation({
    mutationFn: () => trainLabModels(dataset.id, { feature_groups: groups, models, train_share: trainShare }),
    onSuccess: (run) => {
      queryClient.setQueryData(["lab-run", run.id], run);
      void queryClient.invalidateQueries({ queryKey: ["lab-dataset", dataset.id] });
      void queryClient.invalidateQueries({ queryKey: ["lab-datasets"] });
      onTrained(run);
    },
  });
  const rest = (1 - trainShare) / 2;

  function toggle(list: string[], key: string, on: boolean): string[] {
    return on ? [...list, key] : list.filter((k) => k !== key);
  }

  return (
    <section className="card lab-section" aria-labelledby="lab-step-3">
      <div className="section-head">
        <div>
          <span className="section-kicker">LANGKAH 3</span>
          <h2 id="lab-step-3">Fitur & model</h2>
          <p>Fitur dihitung per transaksi hanya dari riwayat sebelum transaksi itu (tanpa mengintip masa depan).</p>
        </div>
      </div>
      <div className="lab-config">
        <fieldset className="lab-checks">
          <legend>Kelompok fitur</legend>
          {options.feature_groups.map((group) => (
            <label key={group.key} className={`lab-check ${groups.includes(group.key) ? "is-on" : ""}`}>
              <input
                type="checkbox"
                checked={groups.includes(group.key)}
                disabled={!canWrite || train.isPending}
                onChange={(event) => setGroups((current) => toggle(current, group.key, event.target.checked))}
              />
              <span>
                <strong>{group.label}</strong>
                <small>{group.description}</small>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="lab-config-side">
          <fieldset className="lab-checks">
            <legend>Model yang dibandingkan</legend>
            {options.models.map((model) => (
              <label key={model.key} className={`lab-check ${models.includes(model.key) ? "is-on" : ""}`}>
                <input
                  type="checkbox"
                  checked={models.includes(model.key)}
                  disabled={!canWrite || train.isPending}
                  onChange={(event) => setModels((current) => toggle(current, model.key, event.target.checked))}
                />
                <span>
                  <strong>
                    {model.name} <span className={`lab-kind lab-kind-${model.kind}`}>{kindLabel(model.kind)}</span>
                  </strong>
                  <small>{model.description}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <label className="lab-share">
            Porsi data latih: {pct(trainShare, 0)}
            <input
              type="range"
              min={0.5}
              max={0.8}
              step={0.05}
              value={trainShare}
              disabled={!canWrite || train.isPending}
              onChange={(event) => setTrainShare(Number(event.target.value))}
            />
            <small className="muted">
              Urut waktu: {pct(trainShare, 0)} terlama untuk latih · {pct(rest, 0)} validasi · {pct(rest, 0)} terbaru untuk uji
            </small>
          </label>
          {canWrite ? (
            <button type="button" onClick={() => train.mutate()} disabled={train.isPending || !groups.length || !models.length}>
              <Icon name="activity" size={16} />
              {train.isPending ? "Melatih model..." : "Latih & bandingkan"}
            </button>
          ) : (
            <p className="muted small">Hanya Data Ops dan Analyst yang dapat melatih model.</p>
          )}
          {train.isPending && (
            <div className="processing-state" role="status">
              <span className="spinner" />
              Melatih {models.length} model pada {num(dataset.validation?.usable_rows)} baris. Biasanya 20–40 detik untuk 100 ribu baris.
            </div>
          )}
          {train.error && <p className="error" role="alert">{errorMessage(train.error)}</p>}
        </div>
      </div>
    </section>
  );
}

// --- step 4: results & threshold -----------------------------------------------------------------

function CurveChart({ curve, index, defaultIndex }: { curve: LabCurvePoint[]; index: number; defaultIndex: number }) {
  const width = 560;
  const height = 170;
  const pad = { left: 34, right: 10, top: 10, bottom: 24 };
  const x = (i: number) => pad.left + (curve.length > 1 ? (i / (curve.length - 1)) * (width - pad.left - pad.right) : 0);
  const y = (v: number) => pad.top + (1 - v) * (height - pad.top - pad.bottom);
  function path(key: "precision" | "recall"): string {
    let d = "";
    let pen = false;
    curve.forEach((point, i) => {
      const value = point[key];
      if (value === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(value).toFixed(1)} `;
      pen = true;
    });
    return d;
  }
  return (
    <svg className="lab-chart" viewBox={`0 0 ${width} ${height}`} role="img"
      aria-label="Presisi dan recall di data validasi untuk setiap threshold">
      {[0, 0.5, 1].map((v) => (
        <g key={v}>
          <line x1={pad.left} x2={width - pad.right} y1={y(v)} y2={y(v)} className="lab-chart-grid" />
          <text x={pad.left - 6} y={y(v) + 4} textAnchor="end" className="lab-chart-label">{v * 100}%</text>
        </g>
      ))}
      <line x1={x(defaultIndex)} x2={x(defaultIndex)} y1={pad.top} y2={height - pad.bottom} className="lab-chart-default" />
      <path d={path("precision")} className="lab-chart-precision" />
      <path d={path("recall")} className="lab-chart-recall" />
      <line x1={x(index)} x2={x(index)} y1={pad.top} y2={height - pad.bottom} className="lab-chart-cursor" />
      <text x={pad.left} y={height - 6} className="lab-chart-label">threshold rendah · banyak alert</text>
      <text x={width - pad.right} y={height - 6} textAnchor="end" className="lab-chart-label">threshold tinggi · sedikit alert</text>
    </svg>
  );
}

function ResultsStep({ run, canWrite }: { run: LabRun; canWrite: boolean }) {
  const queryClient = useQueryClient();
  const results = run.results;
  const [modelKey, setModelKey] = useState(results.best_model);
  const model = results.models.find((m) => m.key === modelKey) ?? results.models[0];
  const [index, setIndex] = useState(model.default_index);
  const [saved, setSaved] = useState<LabSavedModel | null>(null);
  useEffect(() => {
    setModelKey(results.best_model);
    setSaved(null);
  }, [run.id, results.best_model]);
  useEffect(() => {
    setIndex(model.default_index);
    setSaved(null);
  }, [model.key, model.default_index]);
  const save = useMutation({
    mutationFn: () => saveLabModel(run.id, model.key, index),
    onSuccess: (result) => {
      setSaved(result);
      void queryClient.invalidateQueries({ queryKey: ["lab-run", run.id] });
      void queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });
  const point = model.validation_curve[Math.min(index, model.validation_curve.length - 1)];
  const categories = useMemo(() => {
    const names = new Set<string>();
    results.models.forEach((m) => m.test_by_category?.forEach((c) => names.add(c.category)));
    return [...names];
  }, [results.models]);
  const savedHere = run.saved_versions.filter((v) => v.model_key === model.key);

  return (
    <>
      <section className="card lab-section" aria-labelledby="lab-step-4">
        <div className="section-head">
          <div>
            <span className="section-kicker">LANGKAH 4</span>
            <h2 id="lab-step-4">Perbandingan model</h2>
            <p>
              Diukur di data uji (periode terbaru) yang tidak dipakai untuk melatih atau memilih apa pun. Run{" "}
              {formatDateTime(run.created_at)} · {num(run.seconds, 1)} detik.
            </p>
          </div>
        </div>
        <div className="lab-split">
          {(["train", "validation", "test"] as const).map((part) => (
            <div key={part} className="lab-split-part">
              <span>{part === "train" ? "Latih" : part === "validation" ? "Validasi" : "Uji"}</span>
              <strong>{num(results.split[part].rows)} baris</strong>
              <small>
                {num(results.split[part].positives)} positif · {dateOnly(results.split[part].date_from)} – {dateOnly(results.split[part].date_to)}
              </small>
            </div>
          ))}
        </div>
        <div className="table-scroll">
          <table className="table lab-compare">
            <thead>
              <tr>
                <th>Model</th>
                <th>Jenis</th>
                <th className="num">Average precision</th>
                <th className="num">Presisi</th>
                <th className="num">Recall</th>
                <th className="num">Alert / 10.000 trx</th>
                <th className="num">Waktu latih</th>
              </tr>
            </thead>
            <tbody>
              {results.models.map((m: LabModelResult) => (
                <tr key={m.key} className={m.key === results.best_model ? "is-best" : ""}>
                  <td>
                    <strong>{m.name}</strong>
                    {m.key === results.best_model && <span className="badge badge-green lab-best">Terbaik di validasi</span>}
                  </td>
                  <td><span className={`lab-kind lab-kind-${m.kind}`}>{kindLabel(m.kind)}</span></td>
                  <td className="num">{dec(m.test.average_precision)}</td>
                  <td className="num">{pct(m.test.precision)}</td>
                  <td className="num">{pct(m.test.recall)}</td>
                  <td className="num">{num(m.test.flagged_per_10k, 0)}</td>
                  <td className="num">{num(m.seconds, 1)} s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted lab-footnote">
          Presisi & recall pada threshold default tiap model (F1 tertinggi di validasi). Pembanding: menandai transaksi
          secara acak hanya memberi presisi ≈ {pct(results.positive_rate_test, 2)} (rasio positif di data uji). Average
          precision: 1,0 = sempurna.
        </p>
        {categories.length > 0 && (
          <div className="table-scroll">
            <table className="table lab-categories">
              <thead>
                <tr>
                  <th>Kategori / pola (data uji)</th>
                  {results.models.map((m) => <th key={m.key} className="num">{m.name}</th>)}
                </tr>
              </thead>
              <tbody>
                {categories.map((category) => (
                  <tr key={category}>
                    <td className="mono">{category}</td>
                    {results.models.map((m) => {
                      const c = m.test_by_category?.find((x) => x.category === category);
                      return (
                        <td key={m.key} className="num">
                          {c ? `${num(c.caught)} / ${num(c.positives)} tertangkap` : "–"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <details className="lab-features-used">
          <summary>
            {results.features.used.length} fitur dipakai
            {results.features.unusable.length > 0 && ` · ${results.features.unusable.length} tidak tersedia di dataset ini`}
          </summary>
          <p className="small">
            <strong>Dipakai:</strong> <span className="mono">{results.features.used.join(", ")}</span>
          </p>
          {results.features.unusable.length > 0 && (
            <p className="small muted">
              <strong>Kosong atau konstan di data latih:</strong> <span className="mono">{results.features.unusable.join(", ")}</span>
            </p>
          )}
        </details>
      </section>

      <section className="card lab-section" aria-labelledby="lab-threshold">
        <div className="section-head">
          <div>
            <span className="section-kicker">LANGKAH 4 · THRESHOLD</span>
            <h2 id="lab-threshold">Pilih titik kerja</h2>
            <p>
              Angka di sini dari data validasi. Data uji baru dibuka saat model disimpan, supaya threshold tidak
              disetel ke data uji.
            </p>
          </div>
          <div className="lab-model-tabs" role="tablist" aria-label="Model">
            {results.models.map((m) => (
              <button
                key={m.key}
                type="button"
                role="tab"
                aria-selected={m.key === model.key}
                className={m.key === model.key ? "is-active" : "button-secondary"}
                onClick={() => setModelKey(m.key)}
              >
                {m.name}
              </button>
            ))}
          </div>
        </div>
        <div className="lab-threshold-layout">
          <div>
            <CurveChart curve={model.validation_curve} index={index} defaultIndex={model.default_index} />
            <div className="lab-legend small">
              <span className="lab-legend-precision">Presisi</span>
              <span className="lab-legend-recall">Recall</span>
              <span className="lab-legend-default">Default (F1 tertinggi)</span>
            </div>
            <label className="lab-slider">
              Threshold {threshold(point.threshold)} · titik {index + 1} dari {model.validation_curve.length}
              <input
                type="range"
                min={0}
                max={model.validation_curve.length - 1}
                step={1}
                value={index}
                onChange={(event) => {
                  setIndex(Number(event.target.value));
                  setSaved(null);
                }}
              />
            </label>
            <div className="lab-slider-actions">
              <button type="button" className="button-secondary" onClick={() => setIndex(model.default_index)}>
                Kembali ke default
              </button>
            </div>
          </div>
          <div className="lab-metrics">
            <div className="lab-metric">
              <span>Presisi</span>
              <strong>{pct(point.precision)}</strong>
              <small>dari alert, yang benar positif</small>
            </div>
            <div className="lab-metric">
              <span>Recall</span>
              <strong>{pct(point.recall)}</strong>
              <small>dari kasus positif, yang tertangkap</small>
            </div>
            <div className="lab-metric">
              <span>Alert / 10.000 trx</span>
              <strong>{num(point.flagged_per_10k, 0)}</strong>
              <small>beban kerja analis</small>
            </div>
            <p className="lab-plain">
              Dari <strong>{num(point.flagged)}</strong> alert di data validasi, <strong>{num(point.tp)}</strong> benar
              positif dan <strong>{num(point.fp)}</strong> alarm palsu; <strong>{num(point.fn)}</strong> kasus positif
              terlewat.
            </p>
            {model.top_features.length > 0 && (
              <div className="lab-top-features">
                <h3>Fitur paling berpengaruh · {model.name}</h3>
                <ol>
                  {model.top_features.slice(0, 5).map((f) => (
                    <li key={f.feature} title={f.definition}>
                      <span className="mono">{f.feature}</span>
                      <span className="lab-bar"><span style={{ width: `${Math.max(4, 100 * f.share)}%` }} /></span>
                      <small>{pct(f.share, 0)}</small>
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {model.kind === "unsupervised" && (
              <p className="notice lab-note">
                Isolation Forest dilatih tanpa label; label hanya dipakai untuk memilih threshold default dan mengukur hasil.
              </p>
            )}
          </div>
        </div>
      </section>

      <section className="card lab-section" aria-labelledby="lab-step-5">
        <div className="section-head">
          <div>
            <span className="section-kicker">LANGKAH 5</span>
            <h2 id="lab-step-5">Simpan sebagai draft</h2>
            <p>Model dan threshold terpilih masuk ke registry model FCIP dengan status DRAFT.</p>
          </div>
        </div>
        <div className="lab-save">
          <dl className="facts">
            <dt>Model</dt>
            <dd>{model.name} · {kindLabel(model.kind)}</dd>
            <dt>Threshold</dt>
            <dd className="mono">{threshold(point.threshold)}{index === model.default_index ? " (default)" : ""}</dd>
            <dt>Validasi</dt>
            <dd>presisi {pct(point.precision)} · recall {pct(point.recall)} · {num(point.flagged_per_10k, 0)} alert / 10.000 trx</dd>
            <dt>Dataset</dt>
            <dd>{run.dataset_name}</dd>
          </dl>
          {canWrite && (
            <button type="button" onClick={() => save.mutate()} disabled={save.isPending}>
              <Icon name="check" size={16} />
              {save.isPending ? "Menyimpan..." : "Simpan sebagai draft"}
            </button>
          )}
          {save.error && <p className="error" role="alert">{errorMessage(save.error)}</p>}
          {saved && (
            <div className="lab-saved" role="status">
              <p>
                <strong>{saved.already_saved ? "Sudah tersimpan sebelumnya" : "Tersimpan"}</strong> sebagai{" "}
                <span className="mono">{saved.version}</span> <span className="badge badge-amber">{saved.state}</span>
              </p>
              <p>
                Hasil di <strong>data uji</strong> pada threshold ini: presisi {pct(saved.test.precision)} · recall{" "}
                {pct(saved.test.recall)} · {num(saved.test.flagged_per_10k, 0)} alert / 10.000 trx.
              </p>
              <p className="small muted">
                DRAFT belum menilai transaksi apa pun. Aktivasi menunggu alur persetujuan model (FR-405) dan keputusan
                mentor soal CF-05 (apakah skor model boleh membuat alert sendiri).
              </p>
              <Link to="/model-flags" className="btn-link button-secondary">
                Lihat di registry model <Icon name="arrow-right" size={14} />
              </Link>
            </div>
          )}
          {!saved && savedHere.length > 0 && (
            <p className="small muted">
              Versi tersimpan dari run ini: {savedHere.map((v) => v.version).join(", ")}
            </p>
          )}
        </div>
      </section>
    </>
  );
}

// --- page ----------------------------------------------------------------------------------------

export function LabPage() {
  const { user } = useAuth();
  const canWrite = !!user && WRITER_ROLES.includes(user.role);
  const options = useQuery({ queryKey: ["lab-options"], queryFn: fetchLabOptions, staleTime: Infinity });
  const datasets = useQuery({ queryKey: ["lab-datasets"], queryFn: fetchLabDatasets });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedId && datasets.data?.length) setSelectedId(datasets.data[0].id);
  }, [datasets.data, selectedId]);
  const dataset = useQuery({
    queryKey: ["lab-dataset", selectedId],
    queryFn: () => fetchLabDataset(selectedId as string),
    enabled: !!selectedId,
  });
  useEffect(() => {
    setRunId(dataset.data?.runs[0]?.id ?? null);
    // Only when another dataset is opened; a new run sets runId itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset.data?.id]);
  const run = useQuery({
    queryKey: ["lab-run", runId],
    queryFn: () => fetchLabRun(runId as string),
    enabled: !!runId,
  });
  const ready = dataset.data?.status === "MAPPED" && !!dataset.data.validation?.ready;
  const reached = !dataset.data ? 0 : !ready ? 1 : !run.data ? 2 : run.data.saved_versions.length ? 5 : 3;

  return (
    <>
      <div className="page-header">
        <div>
          <span className="eyebrow">MODEL LAB · PROTOTIPE</span>
          <h1>Lab model</h1>
          <p className="page-subtitle">
            Unggah dataset berlabel, petakan kolom, latih beberapa model, bandingkan, lalu pilih threshold.
          </p>
        </div>
      </div>
      <p className="notice lab-prototype-notice">
        <Icon name="info" size={15} />
        <span>
          Prototipe arah <strong>framework</strong> untuk dibahas dengan mentor. Dataset lab tidak masuk ke transaksi
          yang dimonitor, dan model yang disimpan berstatus DRAFT: belum menilai data apa pun sampai alur persetujuan
          model (FR-405) dibangun. Angka dari data sintetis bukan ukuran kinerja produksi.
        </span>
      </p>
      <Stepper reached={reached} />
      {(options.isPending || datasets.isPending) && <p className="loading-text">Memuat lab...</p>}
      {(options.error || datasets.error) && (
        <p className="error" role="alert">{errorMessage(options.error ?? datasets.error)}</p>
      )}
      {options.data && datasets.data && (
        <>
          <DatasetStep
            options={options.data}
            datasets={datasets.data}
            selectedId={selectedId}
            canWrite={canWrite}
            onSelect={(id) => setSelectedId(id)}
          />
          {dataset.error && <p className="error" role="alert">{errorMessage(dataset.error)}</p>}
          {dataset.data && <MappingStep options={options.data} dataset={dataset.data} canWrite={canWrite} />}
          {dataset.data && ready && (
            <TrainingStep
              options={options.data}
              dataset={dataset.data}
              canWrite={canWrite}
              onTrained={(trained) => setRunId(trained.id)}
            />
          )}
          {dataset.data && ready && dataset.data.runs.length > 1 && (
            <label className="lab-run-picker">
              Run yang ditampilkan
              <select value={runId ?? ""} onChange={(event) => setRunId(event.target.value)}>
                {dataset.data.runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    {formatDateTime(r.created_at)} · {r.models.length} model · terbaik: {r.best_model}
                  </option>
                ))}
              </select>
            </label>
          )}
          {run.isFetching && !run.data && <p className="loading-text">Memuat hasil run...</p>}
          {run.data && ready && <ResultsStep run={run.data} canWrite={canWrite} />}
        </>
      )}
    </>
  );
}
