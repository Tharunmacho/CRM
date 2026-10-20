/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: '#111C2E',
          900: '#0B1420',
          800: '#111C2E',
          700: '#1C2A3F',
          600: '#2C3D55',
          500: '#4A5B73',
          400: '#6B7A91',
          300: '#95A1B2',
        },
        /**
         * Sidebar blue. Scoped to the navigation chrome and the login panel —
         * content surfaces stay on the `ink` navy-black scale.
         */
        brand: {
          DEFAULT: '#124E96',
          900: '#0D3A70', // deepest — pressed states
          800: '#124E96', // the sidebar itself
          700: '#1A5FB4', // active nav item
          600: '#15559F', // hover
          400: '#86AEDC',
          300: '#A9C6E8',
        },
        /** Pipeline bars on the dashboard, client-specified. */
        pipeline: {
          ok: '#00225C',
          late: '#800020',
          // The "N late" label — #800020 reads 10.8:1 on white, so it can be
          // the same colour as the bar segment.
          'late-ink': '#800020',
        },
        /** Action green, client-specified. */
        accent: {
          DEFAULT: '#0DA574',
          700: '#0B8A61', // hover / pressed
          600: '#0DA574', // buttons
          500: '#2BBA8C',
          400: '#4ACFA3',
          300: '#6FD9B4', // pipeline bars - lightened, still solid
        },
        /** The one interactive colour: actions, selection, focus. */
        navy: {
          DEFAULT: '#4169E1',
          700: '#3154C6', // hover / pressed
          600: '#4169E1',
          500: '#4169E1',
          100: '#CED9FF',
          50: '#F1F5FF',
        },
        stamp: {
          DEFAULT: '#9B2C2C',
          700: '#7D2222',
          600: '#9B2C2C',
          500: '#B94A4A',
          100: '#F7E2E2',
          50: '#FCF0F0',
        },
        amber: {
          DEFAULT: '#B45309',
          700: '#8F4207',
          600: '#B45309',
          500: '#D97B1F',
          100: '#FBEBD7',
          50: '#FDF6EC',
        },
        field: '#EEF1F5',
        /** Page background. */
        canvas: '#FFFFFF',
        rule: '#D7DDE5',
        'rule-soft': '#E6EAF0',
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
      borderRadius: { control: '8px', panel: '14px' },
      boxShadow: {
        overlay: '0 24px 60px -16px rgba(17,28,46,0.26), 0 0 0 1px rgba(17,28,46,0.05)',
        pop: '0 12px 32px -10px rgba(17,28,46,0.20), 0 0 0 1px rgba(17,28,46,0.05)',
        soft: '0 1px 2px rgba(17,28,46,0.03), 0 6px 16px -8px rgba(17,28,46,0.07)',
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
