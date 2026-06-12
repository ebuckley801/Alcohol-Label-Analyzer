interface StatsCardProps {
  label: string;
  value: number;
  color: "blue" | "green" | "red" | "yellow";
}

export function StatsCard({ label, value, color }: StatsCardProps) {
  const colorStyles = {
    blue: "bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-200",
    green: "bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-200",
    red: "bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-200",
    yellow: "bg-yellow-50 dark:bg-yellow-900/20 text-yellow-700 dark:text-yellow-200",
  };

  return (
    <div className={`rounded-lg p-4 ${colorStyles[color]}`}>
      <p className="text-sm font-medium opacity-80">{label}</p>
      <p className="text-3xl font-bold mt-1">{value}</p>
    </div>
  );
}
