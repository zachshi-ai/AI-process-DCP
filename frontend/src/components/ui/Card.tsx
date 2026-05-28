import React from 'react';

export function Card({ children, className = '' }: { children: React.ReactNode, className?: string }) {
  return (
    <div className={`bg-white rounded-2xl shadow-soft border border-white/40 backdrop-blur-sm p-6 sm:p-8 ${className}`}>
      {children}
    </div>
  );
}
