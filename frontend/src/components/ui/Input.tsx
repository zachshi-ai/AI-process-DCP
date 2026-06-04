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
        <label className="block text-sm font-semibold text-[#2D3436]">
          {label}
        </label>
        <Component
          ref={ref}
          className={`w-full px-4 py-2.5 rounded-xl border border-gray-200 bg-gray-50/50 text-[#2D3436] placeholder-gray-400 transition-all duration-300 focus:bg-white focus:border-[#E8B4B8] focus:ring-4 focus:ring-[#E8B4B8]/20 outline-none ${className}`}
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
