/** @type {import('next').NextConfig} */
const nextConfig = {
  // 禁用热重载和快速刷新
  reactStrictMode: false,
  swcMinify: false,
  webpack: (config, { dev }) => {
    if (dev) {
      // 禁用热模块替换
      config.watchOptions = {
        ignored: /node_modules/,
        poll: false, // 禁用文件轮询
      };

      // 禁用快速刷新
      config.experiments = {
        ...config.experiments,
        esmExternals: false,
      };
    }
    return config;
  },
  // 禁用快速刷新
  experimental: {
    fastRefresh: false,
  },
};

export default nextConfig;
