/** @type {import('next').NextConfig} */
const nextConfig = {
  // Les fichiers JSON produits par le moteur Python vivent hors du dossier
  // `app/`. Sans cette directive, le traceur de dépendances de Next ne les
  // embarque pas dans le bundle serveur et la lecture échoue en production.
  outputFileTracingIncludes: {
    "/**": ["./data/**/*.json"],
  },
};

export default nextConfig;
