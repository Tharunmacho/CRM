/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        /* Every colour below resolves to a design token (src/styles/theme.css), so the palette
           picker and the light/dark switch re-skin existing utilities without touching screens. */
        ink: {
          DEFAULT: 'rgb(var(--c-ink) / <alpha-value>)',
          900: 'rgb(var(--c-ink-900) / <alpha-value>)',
          800: 'rgb(var(--c-ink-800) / <alpha-value>)',
          700: 'rgb(var(--c-ink-700) / <alpha-value>)',
          600: 'rgb(var(--c-ink-600) / <alpha-value>)',
          500: 'rgb(var(--c-ink-500) / <alpha-value>)',
          400: 'rgb(var(--c-ink-400) / <alpha-value>)',
          300: 'rgb(var(--c-ink-300) / <alpha-value>)',
        },
        /** Legacy sidebar blue; the shell now uses the neutral sidebar tokens. */
        brand: {
          DEFAULT: '#124E96',
          900: '#0D3A70',
          800: '#124E96',
          700: '#1A5FB4',
          600: '#15559F',
          400: '#86AEDC',
          300: '#A9C6E8',
        },
        pipeline: {
          ok: '#00225C',
          late: '#800020',
          'late-ink': '#800020',
        },
        accent: {
          DEFAULT: 'rgb(var(--c-accent) / <alpha-value>)',
          700: 'rgb(var(--c-accent-700) / <alpha-value>)',
          600: 'rgb(var(--c-accent-600) / <alpha-value>)',
          500: 'rgb(var(--c-accent-500) / <alpha-value>)',
          400: 'rgb(var(--c-accent-400) / <alpha-value>)',
          300: 'rgb(var(--c-accent-300) / <alpha-value>)',
        },
        /** The one interactive colour: it is the palette's primary. */
        navy: {
          DEFAULT: 'rgb(var(--c-navy) / <alpha-value>)',
          700: 'rgb(var(--c-navy-700) / <alpha-value>)',
          600: 'rgb(var(--c-navy) / <alpha-value>)',
          500: 'rgb(var(--c-navy) / <alpha-value>)',
          100: 'rgb(var(--c-navy-100) / <alpha-value>)',
          50: 'rgb(var(--c-navy-50) / <alpha-value>)',
        },
        stamp: {
          DEFAULT: 'rgb(var(--c-stamp) / <alpha-value>)',
          700: 'rgb(var(--c-stamp-700) / <alpha-value>)',
          600: 'rgb(var(--c-stamp-600) / <alpha-value>)',
          500: 'rgb(var(--c-stamp-500) / <alpha-value>)',
          100: 'rgb(var(--c-stamp-100) / <alpha-value>)',
          50: 'rgb(var(--c-stamp-50) / <alpha-value>)',
        },
        amber: {
          DEFAULT: 'rgb(var(--c-amber) / <alpha-value>)',
          700: 'rgb(var(--c-amber-700) / <alpha-value>)',
          600: 'rgb(var(--c-amber-600) / <alpha-value>)',
          500: 'rgb(var(--c-amber-500) / <alpha-value>)',
          100: 'rgb(var(--c-amber-100) / <alpha-value>)',
          50: 'rgb(var(--c-amber-50) / <alpha-value>)',
        },
        field: 'rgb(var(--c-field) / <alpha-value>)',
        canvas: 'rgb(var(--c-canvas) / <alpha-value>)',
        /** Card surface: white in light mode, the card colour in dark mode. */
        white: 'rgb(var(--c-surface) / <alpha-value>)',
        rule: 'rgb(var(--c-rule) / <alpha-value>)',
        'rule-soft': 'rgb(var(--c-rule-soft) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Poppins', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        micro: ['0.8125rem', { lineHeight: '1.25rem' }],
        xs: ['0.875rem', { lineHeight: '1.375rem' }],
        sm: ['1rem', { lineHeight: '1.5rem' }],
        base: ['1.0625rem', { lineHeight: '1.6875rem' }],
        lead: ['1.0625rem', { lineHeight: '1.625rem' }],
        h3: ['1.1875rem', { lineHeight: '1.625rem', letterSpacing: '-0.01em' }],
        h2: ['1.375rem', { lineHeight: '1.875rem', letterSpacing: '-0.015em' }],
        h1: ['1.75rem', { lineHeight: '2.25rem', letterSpacing: '-0.02em' }],
        stat: ['3rem', { lineHeight: '3.25rem', letterSpacing: '-0.03em' }],
      },
      borderRadius: { control: '6px', panel: '10px' },
      boxShadow: {
        overlay: '0 24px 60px -16px rgba(17,28,46,0.26), 0 0 0 1px rgba(17,28,46,0.05)',
        pop: '0 12px 32px -10px rgba(17,28,46,0.20), 0 0 0 1px rgba(17,28,46,0.05)',
        soft: 'none',
      },
      keyframes: {
        'stamp-in': {
          '0%': { opacity: '0', transform: 'scale(0.96)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        'sheet-in': {
          '0%': { opacity: '0', transform: 'translateY(6px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
      },
      animation: {
        'stamp-in': 'stamp-in 220ms cubic-bezier(.2,.8,.3,1)',
        'sheet-in': 'sheet-in 180ms cubic-bezier(.2,.8,.3,1)',
        'fade-in': 'fade-in 140ms ease-out',
      },
    },
  },
  plugins: [],
}
