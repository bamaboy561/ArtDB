import { FormEvent, useState } from "react";
import { api, User } from "../api";
import { Icon, useTheme } from "../ui";

export default function Login({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useTheme();

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
      <div className="login-art" aria-hidden="true">
        <div className="login-art-inner">
          <div className="brand-mark large">A</div>
          <h2>Продажи, остатки и закупки всех салонов — в одном месте</h2>
          <ul>
            <li>
              <Icon name="orders" /> Заказ поставщику в один файл
            </li>
            <li>
              <Icon name="truck" /> Что в пути и что уже пришло
            </li>
            <li>
              <Icon name="box" /> Прогноз по продажам салонов
            </li>
          </ul>
        </div>
      </div>
      <form className="login-card" onSubmit={submit}>
        <div className="brand">
          <div className="brand-mark">A</div>
          <div className="brand-name">ArtDB</div>
        </div>
        <div>
          <h1>Вход</h1>
          <p className="muted">Те же логины и пароли, что на старом сайте.</p>
        </div>
        <label className="field">
          <span>Логин, email или телефон</span>
          <input value={login} onChange={(event) => setLogin(event.target.value)} autoComplete="username" required autoFocus />
        </label>
        <label className="field">
          <span>Пароль</span>
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />
        </label>
        {error && (
          <div className="alert error">
            <Icon name="alert" size={16} />
            {error}
          </div>
        )}
        <button className="button primary large" disabled={busy}>
          {busy ? "Входим…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
