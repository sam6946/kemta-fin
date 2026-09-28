/**
 * Chargement d'une ressource avec états explicites : chargement, erreur, hors ligne, données périmées.
 *
 * Le dernier résultat réussi est **conservé** quand un rafraîchissement échoue : l'écran continue
 * d'afficher les données connues, avec un bandeau « hors ligne / dernière mise à jour ». Aucun
 * polling : on recharge à la demande, au retour du réseau et quand l'onglet redevient visible
 * (au plus une fois par minute).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";

export type AsyncState<T> = {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  /** Vrai quand `data` date d'un chargement antérieur et que le dernier rafraîchissement a échoué. */
  stale: boolean;
  updatedAt: Date | null;
  reload: () => Promise<void>;
};

const MIN_REFRESH_MS = 60_000;

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const lastLoad = useRef(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const result = await loaderRef.current();
      setData(result);
      setError(null);
      setUpdatedAt(new Date());
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught : new ApiError("server_error", "Une erreur est survenue.", 500),
      );
    } finally {
      lastLoad.current = Date.now();
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    setData(null);
    setUpdatedAt(null);
    void reload();
  }, [reload]);

  useEffect(() => {
    function refreshIfOld() {
      if (Date.now() - lastLoad.current >= MIN_REFRESH_MS) void reload();
    }
    function onVisible() {
      if (document.visibilityState === "visible") refreshIfOld();
    }
    window.addEventListener("online", refreshIfOld);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("online", refreshIfOld);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reload]);

  return { data, loading, error, stale: Boolean(error && data), updatedAt, reload };
}

export function errorMessage(error: ApiError | null): string {
  if (!error) return "";
  if (error.isOffline) return "Pas de connexion : impossible d'actualiser pour le moment.";
  if (error.status === 403) return "Votre rôle ne permet pas de consulter cette information.";
  if (error.status === 429) return "Trop de requêtes : patientez quelques instants.";
  return error.message || "Une erreur est survenue.";
}
