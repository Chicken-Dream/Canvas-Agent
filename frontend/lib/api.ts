// `??` not `||`: an explicitly-empty NEXT_PUBLIC_API_URL means "same
// origin, use relative paths" (the production same-origin Caddy-proxy
// setup - see docker-compose.prod.yml) and must NOT fall through to the
// localhost default just because "" is falsy in JS. Only a genuinely
// unset (undefined) value should fall back to the local-dev default.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export interface Me {
  id: number;
  canvas_user_id: number;
  name: string;
  email: string | null;
}

export interface Outline {
  source: "syllabus_body" | "module_item" | "page" | "none" | "user_submitted";
  title: string | null;
  canvas_url: string | null;
  external_url: string | null;
  confidence: number;
  detected_at: string;
}

export interface OutlineDetail extends Outline {
  fetched_content: string | null;
}

export interface Course {
  id: number;
  canvas_course_id: number;
  name: string;
  course_code: string | null;
  term: string | null;
  html_url: string | null;
  difficulty: number | null;
  outline: Outline | null;
}

export interface Assignment {
  id: number;
  canvas_assignment_id: number;
  name: string;
  due_at: string | null;
  points_possible: number | null;
  html_url: string | null;
  submission_status: string | null;
  submitted_at: string | null;
  score: number | null;
  weight_pct: number | null;
}

export interface AssignmentWithCourse extends Assignment {
  course_id: number;
  course_name: string;
  course_code: string | null;
}

export interface CourseDetail extends Course {
  assignments: Assignment[];
  outline: OutlineDetail | null;
  description_text: string | null;
  description_url: string | null;
}

export interface SyncResult {
  courses: number;
  assignments: number;
  outlines_found: number;
}

export interface UrgentAssignment {
  assignment_id: number;
  name: string;
  course_code: string | null;
  due_at: string;
  reason: string;
}

export interface UrgencyReport {
  generated_at: string;
  top_assignments: UrgentAssignment[];
}

export const api = {
  me: () => request<Me>("/auth/me"),
  devLogin: (access_token: string) =>
    request<Me>("/auth/dev-login", { method: "POST", body: JSON.stringify({ access_token }) }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  oauthLoginUrl: () => `${API_URL}/auth/canvas/login`,
  sync: () => request<SyncResult>("/api/sync", { method: "POST" }),
  courses: () => request<Course[]>("/api/courses"),
  course: (id: number) => request<CourseDetail>(`/api/courses/${id}`),
  updateCourseDifficulty: (id: number, difficulty: number) =>
    request<Course>(`/api/courses/${id}`, { method: "PATCH", body: JSON.stringify({ difficulty }) }),
  submitCourseOutline: (id: number, url: string) =>
    request<OutlineDetail>(`/api/courses/${id}/outline`, { method: "POST", body: JSON.stringify({ url }) }),
  assignments: (opts?: { upcoming?: boolean; days?: number }) => {
    const params = new URLSearchParams();
    if (opts?.upcoming) params.set("upcoming", "true");
    if (opts?.days) params.set("days", String(opts.days));
    const qs = params.toString();
    return request<AssignmentWithCourse[]>(`/api/assignments${qs ? `?${qs}` : ""}`);
  },
  urgencyReport: () => request<UrgencyReport | null>("/api/agent/urgency"),
  evaluateUrgency: () => request<UrgencyReport>("/api/agent/urgency/evaluate", { method: "POST" }),
};
