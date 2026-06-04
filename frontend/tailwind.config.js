/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'brand-primary': '#3370FF',
        'brand-secondary': '#00B96B',
        'brand-cta': '#FF7D00',
        'brand-bg': '#F7F8FA',
        'brand-text': '#1F2329',
      },
      boxShadow: {
        soft: '0 10px 40px -10px rgba(0,0,0,0.05)',
      },
    },
  },
  plugins: [],
}
