/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'brand-primary': '#E8B4B8',
        'brand-secondary': '#A8D5BA',
        'brand-cta': '#D4AF37',
        'brand-bg': '#FFF5F5',
        'brand-text': '#2D3436',
      },
      boxShadow: {
        soft: '0 10px 40px -10px rgba(0,0,0,0.05)',
      },
    },
  },
  plugins: [],
}
