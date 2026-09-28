import "@testing-library/jest-dom/vitest";
// jsdom n'implémente pas IndexedDB : la file hors ligne (MVP-009) a besoin d'un moteur réel
// en test — c'est aussi ce qui garantit que l'implémentation utilisée en production fonctionne.
import "fake-indexeddb/auto";

// `crypto.randomUUID` n'existe pas dans toutes les versions de jsdom utilisées en CI.
if (!globalThis.crypto?.randomUUID) {
  Object.defineProperty(globalThis.crypto ?? (globalThis.crypto = {} as Crypto), "randomUUID", {
    value: () => "00000000-0000-4000-8000-000000000000",
    configurable: true,
  });
}

// jsdom ne fournit pas `Blob.prototype.arrayBuffer` : la compression de photo en a besoin pour
// calculer l'empreinte SHA-256 du fichier réellement envoyé.
if (typeof Blob !== "undefined" && !Blob.prototype.arrayBuffer) {
  Object.defineProperty(Blob.prototype, "arrayBuffer", {
    value(this: Blob): Promise<ArrayBuffer> {
      return new Promise<ArrayBuffer>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as ArrayBuffer);
        reader.onerror = () => reject(reader.error);
        reader.readAsArrayBuffer(this);
      });
    },
    configurable: true,
    writable: true,
  });
}

// `crypto.subtle` n'est pas fourni par jsdom : les preuves terrain calculent leur empreinte
// SHA-256 avec WebCrypto, on branche donc l'implémentation Node pour les tests.
if (!globalThis.crypto?.subtle) {
  const { webcrypto } = await import("node:crypto");
  Object.defineProperty(globalThis, "crypto", {
    value: webcrypto,
    configurable: true,
  });
}
