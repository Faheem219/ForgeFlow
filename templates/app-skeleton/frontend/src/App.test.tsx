import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { HomePage } from './pages/HomePage'

// Example unit test — proves the Vitest + Testing Library + jsdom toolchain works so generated
// feature tests slot straight in. Replace/extend under src/features/<feature>/.
//
// Like e2e/home.spec.ts, it asserts the page RENDERS rather than what it says. Asserting the
// skeleton's placeholder copy here contradicted the build's placeholder gate, and the repair loop
// is barred from editing test files, so nothing could ever reconcile the two.
describe('HomePage', () => {
  it('renders', () => {
    const { container } = render(<HomePage />)
    expect(container).not.toBeEmptyDOMElement()
  })
})
