import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'

// Example component for testing
function HomePage() {
  return (
    <div>
      <h1>Welcome to Nimbus</h1>
      <p>This is the home page</p>
    </div>
  )
}

describe('HomePage', () => {
  it('renders a heading', () => {
    render(<HomePage />)

    const heading = screen.getByRole('heading', {
      name: /welcome to nimbus/i,
    })

    expect(heading).toBeInTheDocument()
  })

  it('renders a paragraph', () => {
    render(<HomePage />)

    const paragraph = screen.getByText('This is the home page')

    expect(paragraph).toBeInTheDocument()
  })
})