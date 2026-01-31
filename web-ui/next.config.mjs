/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  // 减少不必要的日志
  logging: {
    fetches: {
      fullUrl: false,
    },
  },
};

export default nextConfig;
