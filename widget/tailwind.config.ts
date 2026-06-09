import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'rc-bg-base': '#080B14',
        'rc-bg-surface': '#0E1220',
        'rc-bg-elevated': '#141828',
        'rc-accent': '#6366F1',
        'rc-accent-2': '#8B5CF6',
      },
    },
  },
  plugins: [],
}

export default config
