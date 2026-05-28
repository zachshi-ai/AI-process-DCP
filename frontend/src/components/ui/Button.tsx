import React from 'react';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'cta' | 'ghost';
  isLoading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = '', variant = 'primary', isLoading, children, ...props }, ref) => {
    const baseStyles = "inline-flex items-center justify-center rounded-xl px-6 py-2.5 text-sm font-semibold tracking-wide transition-all duration-300 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed";
    
    const variants = {
      primary: "bg-[#E8B4B8] text-[#2D3436] hover:bg-[#DCA1A6] shadow-[0_8px_20px_rgba(232,180,184,0.3)] hover:shadow-[0_12px_25px_rgba(232,180,184,0.4)] hover:-translate-y-0.5",
      secondary: "bg-[#A8D5BA] text-[#2D3436] hover:bg-[#96C7A9] shadow-[0_8px_20px_rgba(168,213,186,0.3)] hover:shadow-[0_12px_25px_rgba(168,213,186,0.4)] hover:-translate-y-0.5",
      cta: "bg-[#D4AF37] text-white hover:bg-[#C29F2F] shadow-[0_8px_20px_rgba(212,175,55,0.3)] hover:shadow-[0_12px_25px_rgba(212,175,55,0.4)] hover:-translate-y-0.5",
      ghost: "bg-transparent text-[#2D3436] hover:bg-black/5"
    };

    return (
      <button
        ref={ref}
        className={`${baseStyles} ${variants[variant]} ${className}`}
        disabled={isLoading || props.disabled}
        aria-busy={isLoading}
        {...props}
      >
        {isLoading && (
          <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-current" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
        )}
        {children}
      </button>
    );
  }
);

Button.displayName = 'Button';
