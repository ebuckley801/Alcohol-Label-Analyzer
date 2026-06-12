import React, { ReactNode } from "react";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger";
  size?: "sm" | "md" | "lg";
  children: ReactNode;
}

export function Button({
  variant = "primary",
  size = "md",
  className = "",
  disabled = false,
  ...props
}: ButtonProps) {
  const baseStyles = "font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 dark:focus:ring-offset-gray-900";

  const variantStyles = {
    primary: `bg-accent text-white hover:bg-blue-900 disabled:bg-gray-400 focus:ring-accent/50 ${disabled ? "opacity-60 cursor-not-allowed" : ""}`,
    secondary: `bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-100 dark:hover:bg-gray-600 disabled:opacity-60 focus:ring-gray-300 ${disabled ? "cursor-not-allowed" : ""}`,
    danger: `bg-danger text-white hover:bg-red-700 disabled:bg-gray-400 focus:ring-danger/50 ${disabled ? "opacity-60 cursor-not-allowed" : ""}`,
  };

  const sizeStyles = {
    sm: "px-3 py-2 text-sm",
    md: "px-4 py-2 text-base",
    lg: "px-6 py-3 text-lg",
  };

  return (
    <button
      className={`${baseStyles} ${variantStyles[variant]} ${sizeStyles[size]} ${className}`}
      disabled={disabled}
      {...props}
    />
  );
}
