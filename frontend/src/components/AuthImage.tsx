/**
 * Image d'un média protégé (miniatures de preuves).
 *
 * - le fichier est récupéré **avec le jeton** (une balise `<img>` n'envoie pas `Authorization`) ;
 * - chargement **différé** : la requête ne part que lorsque l'image approche de l'écran
 *   (`IntersectionObserver`) — une galerie de 100 preuves ne télécharge pas 100 vignettes d'un coup ;
 * - l'URL objet est libérée au démontage ;
 * - en cas d'échec (hors ligne, accès retiré) un repère neutre est affiché : jamais d'icône cassée.
 */

import { useEffect, useRef, useState } from "react";

import { fetchBlob } from "../api/client";

const API_PREFIX = "/api";

type Props = {
  /** Chemin renvoyé par l'API, ex. `/api/evidences/12/thumbnail/`. */
  src: string;
  alt: string;
  width?: number;
  height?: number;
  className?: string;
  style?: React.CSSProperties;
};

export default function AuthImage({ src, alt, width, height, className, style }: Props) {
  const holder = useRef<HTMLSpanElement | null>(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === "undefined");
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (visible || !holder.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    let created: string | null = null;
    setFailed(false);
    const path = src.startsWith(API_PREFIX) ? src.slice(API_PREFIX.length) : src;
    fetchBlob(path)
      .then((blob) => {
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [visible, src]);

  if (objectUrl) {
    return (
      <img
        src={objectUrl}
        alt={alt}
        width={width}
        height={height}
        loading="lazy"
        decoding="async"
        className={className}
        style={style}
      />
    );
  }
  return (
    <span
      ref={holder}
      role="img"
      aria-label={failed ? `${alt} (indisponible)` : alt}
      className={`image-placeholder${failed ? " image-failed" : ""}`}
      style={{ width, height, ...style }}
    >
      {failed ? "Image indisponible" : ""}
    </span>
  );
}
