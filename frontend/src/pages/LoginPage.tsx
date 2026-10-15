import { type FormEvent, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { errorMessage } from "../api/errors";
import { Icon } from "../components/Icon";
import { useAuth } from "../context/AuthContext";

export function LoginPage() {
  const { user, loading, login } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (loading) {
    return <p className="muted content">Checking session...</p>;
  }
  if (user) {
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login">
      <section className="login-story" aria-label="Tentang FCIP">
        <div className="login-brand">
          <span className="brand-mark">
            <Icon name="shield" size={28} />
          </span>
          <strong>
            FCIP<span>.</span>
          </strong>
        </div>
        <span className="eyebrow">FINANCIAL CRIME INTELLIGENCE PLATFORM</span>
        <h1>
          Melihat pola.
          <br />
          <span>Memahami konteks.</span>
        </h1>
        <p className="login-intro">
          Satu workspace untuk validasi data, observasi model, dan investigasi
          yang dapat ditelusuri.
        </p>
        <div className="login-journey">
          <div>
            <Icon name="upload" />
            <span>
              <strong>Ingest & validasi</strong>
              <small>Riwayat transaksi dengan jejak batch</small>
            </span>
            <span className="journey-number">01</span>
          </div>
          <div>
            <Icon name="activity" />
            <span>
              <strong>Rule & model</strong>
              <small>Faktor deteksi yang dapat dijelaskan</small>
            </span>
            <span className="journey-number">02</span>
          </div>
          <div>
            <Icon name="shield" />
            <span>
              <strong>Tinjauan manusia</strong>
              <small>Keputusan dan alasan oleh analis</small>
            </span>
            <span className="journey-number">03</span>
          </div>
        </div>
        <p className="login-story-foot">
          PT Nusantara Digital Payment
          <br />
          Capstone UKDW × V-TEKI · Data demonstrasi
        </p>
      </section>
      <section className="login-form-panel">
        <form className="login-card" onSubmit={(e) => void handleSubmit(e)}>
          <span className="login-welcome-icon">
            <Icon name="shield" size={27} />
          </span>
          <span className="eyebrow">WORKSPACE ACCESS</span>
          <h2>Selamat datang kembali</h2>
          <p className="muted">Masuk dengan akun sesuai peran kamu.</p>
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
              autoFocus
              placeholder="nama@fcip.internal"
              disabled={submitting}
            />
          </label>
          <label>
            Password
            <div className="password-field">
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                placeholder="Masukkan password"
                disabled={submitting}
              />
              <button
                type="button"
                className="icon-button"
                aria-label={
                  showPassword ? "Sembunyikan password" : "Tampilkan password"
                }
                aria-pressed={showPassword}
                onClick={() => setShowPassword(!showPassword)}
              >
                <Icon name={showPassword ? "eye-off" : "eye"} size={19} />
              </button>
            </div>
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="login-submit" disabled={submitting}>
            {submitting ? "Sedang masuk..." : "Masuk ke workspace"}
            <Icon name="arrow-right" size={18} />
          </button>
          <div className="login-note">
            <Icon name="info" size={17} />
            <p>
              Data demonstrasi dari sumber publik dan simulasi. Sinyal model
              mendukung investigasi; keputusan tetap oleh manusia.
            </p>
          </div>
        </form>
        <p className="login-form-foot">Financial Crime Intelligence Platform</p>
      </section>
    </main>
  );
}
