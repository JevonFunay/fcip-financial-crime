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
      <section className="login-form-panel">
        <form className="login-card" onSubmit={(e) => void handleSubmit(e)}>
          <div className="login-heading">
            <div className="login-wordmark">
              <span className="brand-mark" aria-hidden="true">
                <Icon name="shield" size={21} />
              </span>
              <strong>FCIP</strong>
            </div>
            <p className="muted">Financial Crime Intelligence Platform</p>
            <h1>Masuk</h1>
          </div>
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
            {submitting ? "Sedang masuk..." : "Masuk"}
          </button>
          <div className="login-note">
            <p>
              Data demonstrasi dari sumber publik dan simulasi. Sinyal model
              mendukung investigasi; keputusan tetap oleh manusia.
            </p>
          </div>
        </form>
      </section>
    </main>
  );
}
