import * as React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Sidebar } from './Sidebar'

describe('Sidebar navigation accessibility', () => {
  it('gives compact navigation buttons accessible names and identifies the active page', () => {
    const markup = renderToStaticMarkup(React.createElement(Sidebar, {
      active: 'Photos',
      onSelect: () => undefined,
      retryCount: 0
    }))

    expect(markup).toContain('aria-label="Photos"')
    expect(markup).toContain('aria-current="page"')
    expect(markup).toContain('title="Settings"')
    expect(markup).toContain('Local library')
    expect(markup).not.toContain("Abhil's library")
  })
})
