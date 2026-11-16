import { FormEvent, useState } from "react";
import { api, User } from "../api";

export default function Login({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onSignedIn(await api.login(login, password));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Не удалось войти.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <h1>ArtDB</h1>
        <p className="muted">Те же логины и пароли, что на старом сайте.</p>
        <label>
          Логин, email или телефон
          <input value={login} onChange={(event) => setLogin(event.target.value)} autoComplete="username" required />
        </label>
        <label>
          Пароль
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="button primary" disabled={busy}>
          {busy ? "Входим…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
