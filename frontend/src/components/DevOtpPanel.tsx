/**
 * Panneau **développement uniquement** : affiche les derniers codes SMS/email envoyés
 * par l'adaptateur console, pour tester le parcours OTP sans téléphone.
 *
 * - présent uniquement dans le build de développement (`import.meta.env.DEV`) ;
 * - l'endpoint `/api/dev/outbox/` renvoie 404 en production, le panneau disparaît donc
 *   de lui-même si le build est malgré tout servi en développement ;
 * - jamais de secret technique : uniquement le message destiné à l'utilisateur.
 */

import { useCallback, useEffect, useState } from "react";

type OutboxItem = { phone?: string; to?: string; message: string };

export default function DevOtpPanel({ onPick, phone }: { onPick?: (code: string) => void; phone?: string }) {
  const [items, setItems] = useState<OutboxItem[]>([]);
  const [open, setOpen] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/dev/outbox/");
      if (!response.ok) {
        setUnavailable(true);
        return;
      }
      const data = (await response.json()) as { sms: OutboxItem[]; emails: OutboxItem[] };
      setItems([...data.sms.slice().reverse(), ...data.emails.slice().reverse()]);
      setUnavailable(false);
    } catch {
      setUnavailable(true);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  if (!import.meta.env.DEV || unavailable) return null;

  return (
    <div style={{ marginTop: 14, borderTop: "1px dashed #cbd5e1", paddingTop: 10 }}>
      <button type="button" className="button button-ghost" onClick={() => setOpen((value) => !value)}>
        {open ? "Masquer" : "Afficher"} les codes de test (développement)
      </button>
      {open ? (
        <div style={{ marginTop: 10, fontSize: "0.82rem", color: "#475569" }}>
          <p style={{ margin: "0 0 8px" }}>
            Adaptateur SMS/email « console » — aucune donnée réelle n'est envoyée.
          </p>
          {items.length === 0 ? <p>Aucun message pour le moment.</p> : null}
          <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 8 }}>
            {items.map((item, index) => {
              const code = item.message.match(/(\d{6})/)?.[1];
              const relevant = !phone || (item.phone ?? "").includes(phone.replace(/\D/g, "").slice(-9));
              return (
                <li key={`${index}-${item.message.slice(0, 12)}`} style={{ opacity: relevant ? 1 : 0.55 }}>
                  <strong>{item.phone ?? item.to ?? "destinataire inconnu"}</strong> — {item.message}
                  {code && onPick && relevant ? (
                    <>
                      {" "}
                      <button
                        type="button"
                        className="button button-ghost"
                        style={{ padding: "4px 8px", minHeight: 0, fontSize: "0.78rem" }}
                        onClick={() => onPick(code)}
                      >
                        Utiliser {code}
                      </button>
                    </>
                  ) : null}
                </li>
              );
            })}
          </ul>
          <button type="button" className="button button-ghost" onClick={() => void load()}>
            Rafraîchir
          </button>
        </div>
      ) : null}
    </div>
  );
}
