/**
 * Stockage local hors ligne (MVP-009) — IndexedDB avec repli mémoire.
 *
 * L'application de terrain doit continuer à fonctionner **sans réseau**, y compris après
 * fermeture du navigateur : la file d'opérations et les photos en attente d'envoi survivent
 * donc au rechargement (IndexedDB).
 *
 * Deux précautions :
 *
 *  - si IndexedDB est indisponible (navigation privée, navigateur ancien, environnement de
 *    test sans implémentation), on bascule sur un stockage mémoire : rien ne casse, mais
 *    l'interface prévient que la persistance n'est pas garantie ;
 *  - aucune donnée métier n'est dupliquée ici : le stockage local ne contient que ce qui
 *    **doit** être rejoué, jamais une copie de la base serveur.
 */

import { openDB, type IDBPDatabase } from "idb";

import type { OutboxOperation } from "./outboxTypes";

const DB_NAME = "kemta-suivi";
const DB_VERSION = 1;
const OUTBOX_STORE = "outbox";

/** Repli mémoire : la file fonctionne, mais elle est perdue au rechargement. */
const memory = new Map<string, OutboxOperation>();
let persistent = true;

let dbPromise: Promise<IDBPDatabase | null> | null = null;

async function openDatabase(): Promise<IDBPDatabase | null> {
  if (typeof indexedDB === "undefined") {
    persistent = false;
    return null;
  }
  try {
    return await openDB(DB_NAME, DB_VERSION, {
      upgrade(database) {
        if (!database.objectStoreNames.contains(OUTBOX_STORE)) {
          const store = database.createObjectStore(OUTBOX_STORE, { keyPath: "opId" });
          store.createIndex("byStatus", "status");
          store.createIndex("byProject", "projectId");
        }
      },
    });
  } catch {
    persistent = false;
    return null;
  }
}

async function database(): Promise<IDBPDatabase | null> {
  if (!dbPromise) dbPromise = openDatabase();
  return dbPromise;
}

/** Vrai si la file est réellement persistée sur l'appareil. */
export function isPersistent(): boolean {
  return persistent;
}

export const outboxStore = {
  async put(operation: OutboxOperation): Promise<void> {
    const db = await database();
    if (!db) {
      memory.set(operation.opId, operation);
      return;
    }
    await db.put(OUTBOX_STORE, operation);
  },

  async all(): Promise<OutboxOperation[]> {
    const db = await database();
    if (!db) return [...memory.values()];
    return (await db.getAll(OUTBOX_STORE)) as OutboxOperation[];
  },

  async delete(opId: string): Promise<void> {
    const db = await database();
    if (!db) {
      memory.delete(opId);
      return;
    }
    await db.delete(OUTBOX_STORE, opId);
  },

  async clear(): Promise<void> {
    const db = await database();
    if (!db) {
      memory.clear();
      return;
    }
    await db.clear(OUTBOX_STORE);
  },
};

/** Réinitialise le module (tests : repartir d'une file vide). */
export async function resetLocalStorageForTests(): Promise<void> {
  // Laisse d'abord se terminer les écritures encore en vol (une synchronisation lancée par un
  // test précédent) : sinon elles ressusciteraient des opérations après le nettoyage.
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
  await outboxStore.clear();
  memory.clear();
  dbPromise = null;
  persistent = true;
}
