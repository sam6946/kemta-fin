/**
 * Traitement local des preuves terrain (MVP-007) : compression, empreinte, GPS, appareil.
 *
 * Tout se passe **sur le téléphone**, avant tout envoi :
 * - la photo est ré-encodée en JPEG ≤ 1600 px (une photo de 6 Mo devient ~350 Ko, ce qui rend
 *   l'envoi possible en 3G) ;
 * - l'empreinte SHA-256 est calculée localement : elle identifie la pièce et sert au
 *   dédoublonnage côté serveur ;
 * - la position est demandée explicitement ; un refus ou une indisponibilité produit un statut
 *   lisible, **jamais un échec silencieux**.
 */

export type GpsStatus = "AVAILABLE" | "UNAVAILABLE" | "DENIED";

export type GeoResult = {
  status: GpsStatus;
  latitude: number | null;
  longitude: number | null;
  accuracy: number | null;
  message: string;
};

export type DeviceInfo = {
  device_model: string;
  device_platform: string;
};

export type CompressedPhoto = {
  blob: Blob;
  hash: string;
  width: number;
  height: number;
  originalBytes: number;
  compressedBytes: number;
  mimeType: string;
};

export const MAX_EDGE_PX = 1600;
export const JPEG_QUALITY = 0.82;

/** Taille en octets, formatée pour l'utilisateur. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
}

/** Empreinte SHA-256 en hexadécimal (WebCrypto ; repli explicite si indisponible). */
export async function sha256Hex(payload: ArrayBuffer): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error(
      "Le calcul d'empreinte (WebCrypto) n'est pas disponible sur cet appareil : " +
        "mettez à jour le navigateur pour envoyer des preuves.",
    );
  }
  const digest = await globalThis.crypto.subtle.digest("SHA-256", payload);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/** Informations appareil envoyées avec la preuve (utiles au support terrain). */
export function deviceInfo(navigatorLike: Navigator = navigator): DeviceInfo {
  const agent = navigatorLike.userAgent ?? "";
  const platform = /Android/i.test(agent)
    ? "Android"
    : /iPhone|iPad|iPod/i.test(agent)
      ? "iOS"
      : /Windows/i.test(agent)
        ? "Windows"
        : /Mac OS X/i.test(agent)
          ? "macOS"
          : /Linux/i.test(agent)
            ? "Linux"
            : "Inconnu";

  // Modèle : « Android 13; Tecno Spark 10) » ou le premier segment utile.
  const android = agent.match(/Android[^;]*;\s*([^;)]+)\)/);
  const model = android?.[1]?.trim() ?? (platform === "iOS" ? "iPhone" : "Navigateur");

  return { device_model: model.slice(0, 120), device_platform: platform.slice(0, 60) };
}

/** Horodatage ISO de la capture (le serveur refusera un horodatage manifestement futur). */
export function capturedAtNow(date: Date = new Date()): string {
  return date.toISOString();
}

/**
 * Récupère la position en distinguant **refus**, **indisponibilité** et **délai dépassé**.
 * Aucun de ces cas ne bloque le dépôt : la preuve part avec son statut GPS.
 */
export function getPosition(
  options: { timeoutMs?: number; geolocation?: Geolocation } = {},
): Promise<GeoResult> {
  const geolocation = options.geolocation ?? globalThis.navigator?.geolocation;
  const timeoutMs = options.timeoutMs ?? 10_000;

  if (!geolocation) {
    return Promise.resolve({
      status: "UNAVAILABLE",
      latitude: null,
      longitude: null,
      accuracy: null,
      message: "Cet appareil ne fournit pas de position. La preuve sera déposée sans GPS.",
    });
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: GeoResult) => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };

    // Filet de sécurité : certains navigateurs n'appellent jamais le callback d'erreur.
    const timer = setTimeout(
      () =>
        finish({
          status: "UNAVAILABLE",
          latitude: null,
          longitude: null,
          accuracy: null,
          message: "Position non obtenue dans le délai imparti. La preuve partira sans GPS.",
        }),
      timeoutMs + 500,
    );

    geolocation.getCurrentPosition(
      (position) => {
        clearTimeout(timer);
        finish({
          status: "AVAILABLE",
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy ?? null,
          message: `Position obtenue (± ${Math.round(position.coords.accuracy ?? 0)} m).`,
        });
      },
      (error) => {
        clearTimeout(timer);
        if (error.code === 1) {
          finish({
            status: "DENIED",
            latitude: null,
            longitude: null,
            accuracy: null,
            message:
              "Localisation refusée : la preuve sera déposée sans GPS. Autorisez la position " +
              "dans les réglages du navigateur pour la prochaine photo.",
          });
          return;
        }
        finish({
          status: "UNAVAILABLE",
          latitude: null,
          longitude: null,
          accuracy: null,
          message: "Position indisponible (signal faible). La preuve partira sans GPS.",
        });
      },
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 15_000 },
    );
  });
}

function loadImage(file: Blob): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Image illisible : reprenez la photo."));
    };
    image.src = url;
  });
}

/** Redimensionne et ré-encode en JPEG. Renvoie le blob compressé et ses dimensions. */
export async function compressImage(
  file: File | Blob,
  options: { maxEdge?: number; quality?: number } = {},
): Promise<{ blob: Blob; width: number; height: number }> {
  const maxEdge = options.maxEdge ?? MAX_EDGE_PX;
  const quality = options.quality ?? JPEG_QUALITY;

  const image = await loadImage(file);
  const scale = Math.min(1, maxEdge / Math.max(image.width, image.height));
  const width = Math.max(1, Math.round(image.width * scale));
  const height = Math.max(1, Math.round(image.height * scale));

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Compression impossible sur cet appareil.");
  context.drawImage(image, 0, 0, width, height);

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", quality),
  );
  if (!blob) throw new Error("Compression impossible sur cet appareil.");
  return { blob, width, height };
}

/** Chaîne complète : compression → empreinte, avec les tailles avant/après pour l'utilisateur. */
export async function preparePhoto(file: File): Promise<CompressedPhoto> {
  const normalized = file.type === "image/jpeg" ? file : new File([file], "photo.jpg", { type: "image/jpeg" });
  const { blob, width, height } = await compressImage(normalized);
  const hash = await sha256Hex(await blob.arrayBuffer());
  return {
    blob,
    hash,
    width,
    height,
    originalBytes: file.size,
    compressedBytes: blob.size,
    mimeType: "image/jpeg",
  };
}

/** Valeur à mettre dans l'en-tête `Idempotency-Key` : stable pour une tentative donnée. */
export function newIdempotencyKey(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID().replace(/-/g, "");
  return `op${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}
