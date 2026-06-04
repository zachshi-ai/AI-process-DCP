import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { Button } from './Button';

describe('Button Component', () => {
  it('renders correctly with default props', () => {
    render(<Button>Click me</Button>);
    const button = screen.getByRole('button', { name: /click me/i });
    expect(button).toBeInTheDocument();
    expect(button).toHaveClass('bg-[#E8B4B8]'); // default variant is primary
  });

  it('renders the CTA variant correctly', () => {
    render(<Button variant="cta">Submit</Button>);
    const button = screen.getByRole('button', { name: /submit/i });
    expect(button).toHaveClass('bg-[#D4AF37]');
  });

  it('shows loading spinner and disables button when isLoading is true', () => {
    render(<Button isLoading>Loading</Button>);
    const button = screen.getByRole('button', { name: /loading/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    // SVG spinner should be present
    expect(button.querySelector('svg')).toBeInTheDocument();
  });

  it('calls onClick handler when clicked', async () => {
    let clicked = false;
    render(<Button onClick={() => clicked = true}>Action</Button>);
    const button = screen.getByRole('button', { name: /action/i });
    fireEvent.click(button);
    expect(clicked).toBe(true);
  });
});
