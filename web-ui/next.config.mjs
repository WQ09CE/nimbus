/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  eslint: {
    ignoreDuringBuilds: true,
  },
  // 允许跨域开发请求
  // experimental: {
  //   allowedDevOrigins: ["127.0.0.1:3000", "localhost:3000", "0.0.0.0:3000"],
  // },
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
