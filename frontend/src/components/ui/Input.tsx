import React from 'react';

type BaseProps = {
  label: string;
  helperText?: string;
  isTextarea?: boolean;
};

type InputProps = BaseProps & React.InputHTMLAttributes<HTMLInputElement> & React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Input = React.forwardRef<any, InputProps>(
  ({ label, helperText, isTextarea, className = '', ...props }, ref) => {
    const Component = isTextarea ? 'textarea' : 'input';
    return (
      <div className="space-y-1.5">
        <label className="block text-sm font-semibold text-brand-text">
          {label}
        </label>
        <Component
          ref={ref}
          className={`w-full px-4 py-2.5 rounded-lg border border-gray-200 bg-white text-brand-text placeholder-gray-400 transition-colors duration-200 focus:border-brand-primary focus:ring-4 focus:ring-brand-primary/15 outline-none ${className}`}
          {...(props as any)}
        />
        {helperText && (
          <p className="text-xs text-gray-500 mt-1">{helperText}</p>
        )}
      </div>
    );
  }
);

Input.displayName = 'Input';
