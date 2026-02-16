/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  eslint: {
    ignoreDuringBuilds: true,
  },
  // Proxy API requests to nimbus server (port 4096)
  // This allows external access via port 3000 only — no need to expose port 4096
  async rewrites() {
    const nimbusUrl = process.env.NIMBUS_API_URL || "http://localhost:4096";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${nimbusUrl}/api/v1/:path*`,
      },
      {
        source: "/debug/:path*",
        destination: `${nimbusUrl}/debug/:path*`,
      },
    ];
  },
  webpack: (config, { dev, isServer }) => {
    return config;
  },
  // 减少不必要的日志
  logging: {
    fetches: {
      fullUrl: false,
    },
  },
};

export default nextConfig;
