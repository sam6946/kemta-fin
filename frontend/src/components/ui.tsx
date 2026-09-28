import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <main className="auth-layout">
      <header className="brand">
        <span className="brand-mark">KEMTA</span>
        <span className="brand-sub">Suivi de chantier</span>
      </header>
      <section className="card">
        <h1>{title}</h1>
        {subtitle ? <p className="subtitle">{subtitle}</p> : null}
        {children}
      </section>
      {footer ? <footer className="auth-footer">{footer}</footer> : null}
    </main>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  // Association explicite label ↔ champ (`htmlFor`/`id`) : indispensable pour les
  // lecteurs d'écran et pour les tests, l'indication n'étant pas intégrée au libellé.
  const id = useId();
  const hintId = `${id}-hint`;

  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<{ id?: string; "aria-describedby"?: string }>, {
        id: (children.props as { id?: string }).id ?? id,
        "aria-describedby": hint ? hintId : undefined,
      })
    : children;

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {control}
      {hint ? (
        <span className="field-hint" id={hintId}>
          {hint}
        </span>
      ) : null}
      {error ? (
        <span className="field-error" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function Alert({
  tone = "info",
  children,
}: {
  tone?: "info" | "error" | "success" | "warning";
  children: ReactNode;
}) {
  return (
    <div className={`alert alert-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Button({
  children,
  loading,
  variant = "primary",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean; variant?: "primary" | "ghost" }) {
  return (
    <button className={`button button-${variant}`} disabled={loading || rest.disabled} {...rest}>
      {loading ? "…" : children}
    </button>
  );
}
