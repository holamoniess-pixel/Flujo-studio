import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/testSetup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    pool: 'forks',
    maxWorkers: 1,
    minWorkers: 1,
    restoreMocks: true,
    clearMocks: true,
    // M2: resource-flake resilience. A pre-existing baseline failure was a
    // worker-startup timeout under load, not an assertion bug. The defaults
    // (5s test / 10s hook) are tight for a single-fork jsdom run on a busy
    // machine, so allow more headroom. Assertions are unchanged.
    testTimeout: 20000,
    hookTimeout: 20000,
  },
})
