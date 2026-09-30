"use client";

import { useState, type FormEvent } from "react";
import { ArrowRight, LoaderCircle, Route } from "lucide-react";
import { authClient } from "@/lib/auth-client";

export function AccountForm() {
  const [register, setRegister] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      const credentials = {
        email: String(fields.get("email") ?? "").trim(),
        password: String(fields.get("password") ?? ""),
      };
      const result = register
        ? await authClient.signUp.email({ ...credentials, name: String(fields.get("name") ?? "").trim() })
        : await authClient.signIn.email(credentials);
      if (result.error) {
        setError(result.error.status === 429
          ? "尝试次数过多，请稍后再试。"
          : register ? "无法创建账号，请检查邮箱是否已注册，以及密码是否至少 10 位。"
            : "登录失败，请检查邮箱和密码。"
        );
      } else {
        window.location.assign("/");
      }
    } catch {
      setError("暂时无法连接，请稍后再试。");
    } finally {
      setPending(false);
    }
  }

  return <main className="account-page">
    <div className="account-waymark" aria-hidden="true"><Route size={38} strokeWidth={1.2} /></div>
    <section className="account-card" aria-labelledby="account-title">
      <div className="account-brand"><Route size={20} /> RouteFrom</div>
      <p className="eyebrow">个人足迹工作台</p>
      <h1 id="account-title">{register ? "开始记录你的世界" : "回到你的足迹"}</h1>
      <p className="account-intro">导入原始 CSV，把走过的路线与常去的地点保存在自己的地图里。</p>
      <form onSubmit={submit}>
        <fieldset disabled={pending}>
          {register && <label>称呼<input name="name" autoComplete="name" required maxLength={80} placeholder="怎么称呼你" /></label>}
          <label>邮箱<input name="email" type="email" autoComplete="email" required maxLength={254} placeholder="you@example.com" /></label>
          <label>密码<input name="password" type="password" autoComplete={register ? "new-password" : "current-password"} required minLength={register ? 10 : undefined} maxLength={128} placeholder={register ? "至少 10 位" : "输入密码"} /></label>
          {error && <p className="account-error" role="alert">{error}</p>}
          <button className="account-submit" type="submit">
            {pending ? <LoaderCircle size={16} className="is-spinning" /> : <ArrowRight size={16} />}
            {pending ? "正在连接…" : register ? "创建账号" : "登录"}
          </button>
        </fieldset>
      </form>
      <button className="account-switch" disabled={pending} onClick={() => { setRegister(!register); setError(null); }}>
        {register ? "已有账号？返回登录" : "第一次来？创建账号"}
      </button>
    </section>
  </main>;
}
