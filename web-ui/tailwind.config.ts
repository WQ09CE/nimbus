import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: [
          "var(--font-mono)",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "Liberation Mono",
          "Courier New",
          "monospace",
        ],
        display: ["var(--font-display)", "system-ui", "sans-serif"],
      },
      colors: {
        nimbus: {
          bg: '#0c1220',
          surface: 'rgba(148,163,184,0.06)',
          'surface-hover': 'rgba(148,163,184,0.10)',
          accent: '#7dd3fc',
          'accent-soft': '#bae6fd',
          'accent-2': '#c4b5fd',
          text: '#e2e8f0',
          'text-dim': '#94a3b8',
          border: 'rgba(148,163,184,0.12)',
          glow: 'rgba(125,211,252,0.08)',
        },
      },
      animation: {
        'breathe': 'breathe 3s ease-in-out infinite',
        'cloud-drift': 'cloud-drift 20s ease-in-out infinite',
      },
      keyframes: {
        breathe: {
          '0%, 100%': { opacity: '0.6', transform: 'scale(1)' },
          '50%': { opacity: '1', transform: 'scale(1.05)' },
        },
        'cloud-drift': {
          '0%, 100%': { transform: 'translateX(0) translateY(0)' },
          '50%': { transform: 'translateX(20px) translateY(-10px)' },
        },
      },
    },
  },
  plugins: [],
};

export default config;
