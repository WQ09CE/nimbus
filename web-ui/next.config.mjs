/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  eslint: {
    ignoreDuringBuilds: true,
  },
  // 禁用热重载 - 避免开发时代码修改中断用户会话
  webpack: (config, { dev, isServer }) => {
    if (dev && !isServer) {
      // 禁用 Fast Refresh (React Hot Reload)
      config.watchOptions = {
        ignored: ['**/*'],  // 忽略所有文件变化
      };
    }
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
