import { beforeAll, afterEach, afterAll } from 'vitest'
import { server } from '../mocks/server'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom' // optional utilities

// Start server before all tests
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))

//  Close server after all tests
afterAll(() => server.close())

// Reset handlers after each test `important for test isolation`
afterEach(() => {
  server.resetHandlers()
  cleanup()
})

// Mock text encoding/decoding for JSDOM
import { TextEncoder, TextDecoder } from 'util'
global.TextEncoder = TextEncoder as any
global.TextDecoder = TextDecoder as any

// Mock window.scrollTo (JSDOM doesn't implement it)
Object.defineProperty(window, 'scrollTo', { value: () => {}, writable: true });
