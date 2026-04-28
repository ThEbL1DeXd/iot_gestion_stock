/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        panel: {
          900: '#0f172a',
          800: '#172554',
          700: '#1e1b4b'
        }
      },
      fontFamily: {
        display: ['Space Grotesk', 'sans-serif'],
        body: ['Manrope', 'sans-serif']
      },
      boxShadow: {
        soft: '0 12px 30px rgba(15, 23, 42, 0.35)'
      }
    }
  },
  plugins: []
};
