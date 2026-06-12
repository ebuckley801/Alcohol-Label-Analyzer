/** @type {import('tailwindcss').Config} */
// eslint-disable-next-line no-undef,global-require
module.exports = {
  darkMode: "class",
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Modern color palette
        accent: "#264653",
        success: "#137752",
        warning: "#9a4f00",
        danger: "#a31b2a",
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', '"Segoe UI"', 'sans-serif'],
      },
      boxShadow: {
        sm: "0 1px 2px 0 rgba(0, 0, 0, 0.05)",
        base: "0 1px 3px 0 rgba(0, 0, 0, 0.1)",
        md: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
        lg: "0 10px 15px -3px rgba(0, 0, 0, 0.1)",
        xl: "0 14px 30px rgba(106, 86, 43, 0.15)",
      },
    },
  },
  plugins: [require("@tailwindcss/forms")],
};
