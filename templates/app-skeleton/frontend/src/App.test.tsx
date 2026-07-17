import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { HomePage } from './pages/HomePage'

// Example unit test — proves the Vitest + Testing Library + jsdom toolchain works so generated
// feature tests slot straight in. Replace/extend under src/features/<feature>/.
describe('HomePage', () => {
  it('renders the heading', () => {
    render(<HomePage />)
    expect(screen.getByRole('heading', { name: /your app starts here/i })).toBeInTheDocument()
  })
})
