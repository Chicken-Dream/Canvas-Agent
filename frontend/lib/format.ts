export function formatDueDate(dueAt: string | null): string {
  if (!dueAt) return "No due date";
  const date = new Date(dueAt);
  return date.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function urgencyClasses(dueAt: string | null): string {
  if (!dueAt) return "bg-slate-100 text-slate-600";
  const diffMs = new Date(dueAt).getTime() - Date.now();
  const diffHours = diffMs / (1000 * 60 * 60);
  if (diffHours < 0) return "bg-slate-200 text-slate-500";
  if (diffHours < 24) return "bg-red-100 text-red-700";
  if (diffHours < 72) return "bg-amber-100 text-amber-700";
  return "bg-emerald-50 text-emerald-700";
}

export function outlineBadge(confidence: number): { label: string; classes: string } {
  if (confidence >= 0.8) return { label: "Outline found", classes: "bg-emerald-100 text-emerald-700" };
  if (confidence > 0) return { label: "Possible outline", classes: "bg-amber-100 text-amber-700" };
  return { label: "Outline not found", classes: "bg-slate-100 text-slate-500" };
}
