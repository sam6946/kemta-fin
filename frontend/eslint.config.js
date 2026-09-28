import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

/**
 * Configuration ESLint (flat config) du frontend KEMTA SUIVI.
 * Usage : `npm run lint`
 */
export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", "coverage/**", "playwright-report/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Les erreurs d'API sont traitées par les écrans ; un `any` isolé reste toléré
      // pour les tests qui simulent des charges réseau partielles.
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
);
